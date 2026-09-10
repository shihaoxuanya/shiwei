"""Actual large-file/page boundaries through an isolated JSONL Worker.

Fixtures and the entire synthetic library are removed on exit. Hashing and PDF
padding use a 1 MiB buffer, never read_bytes() for large files. --worker targets
the final packaged sidecar; absence selects the development Worker. No models
or personal library are used. A disk preflight reports insufficient space as a
failure rather than silently replacing actual boundary tests with mocks.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
MIB = 1024 ** 2


def client_class():
    spec = importlib.util.spec_from_file_location("shiwei_web_smoke", ROOT / "scripts/smoke-web-worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Client


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(MIB), b""):
            digest.update(block)
    return digest.hexdigest()


def padded_pdf(path: Path, size: int, marker: str) -> None:
    """Produce an exact-size valid PDF with one visible page and unused padding."""
    prefix = bytearray(b"%PDF-1.4\n")
    offsets = []

    def obj(number: int, body: bytes):
        offsets.append(len(prefix))
        prefix.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    obj(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    content = f"BT /F1 16 Tf 40 700 Td ({marker} local recovery 38~50 days) Tj ET".encode("ascii")
    obj(5, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    offsets.append(len(prefix))
    stream_tail = b"\nendstream\nendobj\n"
    padding_size = size - 1024
    for _ in range(10):
        stream_head = f"6 0 obj\n<< /Length {padding_size} >>\nstream\n".encode()
        xref = len(prefix) + len(stream_head) + padding_size + len(stream_tail)
        tail = b"xref\n0 7\n0000000000 65535 f \n"
        tail += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
        tail += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        delta = size - (xref + len(tail))
        if delta == 0:
            break
        padding_size += delta
    else:
        raise AssertionError("Could not construct the exact-size PDF fixture")
    assert padding_size > 0
    with path.open("wb") as handle:
        handle.write(prefix)
        handle.write(stream_head)
        block = b"0" * MIB
        remaining = padding_size
        while remaining:
            length = min(remaining, len(block))
            handle.write(block[:length])
            remaining -= length
        handle.write(stream_tail)
        handle.write(tail)
    assert path.stat().st_size == size


def pages_pdf(path: Path, pages: int, last_marker: str) -> None:
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(path), pageCompression=1)
    for page in range(1, pages + 1):
        marker = last_marker if page == pages else f"Intermediate{pages}page{page:04d}"
        document.drawString(40, 700, f"{marker} synthetic migration 38~50 days")
        document.showPage()
    document.save()


def verify_import(client, path: Path, marker: str, pages: int, *, timeout: int):
    digest = sha256(path)
    events_start = len(client.events)
    start = monotonic()
    report = client.call("import_paths", {"paths": [str(path)]}, timeout=timeout)
    elapsed = monotonic() - start
    assert report["summary"] == {"imported": 1, "skipped": 0, "failed": 0}, "Actual boundary import did not succeed"
    source_id = report["imported"][0]["sourceId"]
    source = next(source for source in client.call("list_sources")["sources"] if source["id"] == source_id)
    assert source["size"] == path.stat().st_size
    assert sha256(Path(source["storedPath"])) == digest
    assert sha256(path) == digest
    hits = client.call("search_lexical", {"query": marker})["hits"]
    assert hits and hits[0]["sourceId"] == source_id and hits[0]["pageNumber"] == pages
    assert "38~50" in hits[0]["content"]
    citation = client.call("search_hybrid", {"query": marker})["citations"][0]
    assert citation["sourceId"] == source_id and citation["pageNumber"] == pages
    assert citation["citationId"].startswith("S")
    observed = []
    for event in client.events[events_start:]:
        if event.get("event") != "job_progress" or event["data"].get("jobId") != report["jobId"]:
            continue
        match = re.search(r"正在解析 PDF (\d+)/(\d+) 页", event["data"].get("currentStep", ""))
        if match:
            assert int(match.group(2)) == pages
            observed.append(int(match.group(1)))
    assert observed == list(range(1, pages + 1)), "Per-page progress was missing or out of order"
    return {"bytes": path.stat().st_size, "pages": pages, "importSeconds": round(elapsed, 3),
            "progressEvents": len(observed), "pageCitation": citation["pageNumber"], "sourceHashPreserved": True}


def run(directory: Path, worker: Path | None, timeout: int) -> dict:
    # Peak includes original + copied 1GiB, the rejected 1GiB+1 fixture, 101MiB
    # and its copy, and ample working reserve. No source file is memory loaded.
    free = shutil.disk_usage(directory).free
    if free < 5 * GIB:
        raise RuntimeError("Actual large-file smoke needs 5GiB free in the temporary volume; no mock result was substituted")
    client = client_class()(directory, worker, offline=True, sequence=0)
    results = {}
    try:
        version = client.call("ping")["workerVersion"]
        assert client.call("provider_status")["configured"] is False
        for name, size, marker in (
            ("超一百兆.pdf", 101 * MIB, "Overhundredproof"),
            ("恰好一GiB.pdf", GIB, "Exactgibproof"),
        ):
            path = directory / name
            padded_pdf(path, size, marker)
            results["actual101MiB" if size < GIB else "actual1GiB"] = verify_import(client, path, marker, 1, timeout=timeout)
            duplicate = client.call("import_paths", {"paths": [str(path)]}, timeout=timeout)
            assert duplicate["summary"] == {"imported": 0, "skipped": 1, "failed": 0}

        oversize = directory / "超过一GiB.pdf"
        # The worker must reject from stat alone; no valid PDF or RAM allocation
        # is needed above the byte boundary. This is an actual file, not mock stat.
        with oversize.open("wb") as handle:
            handle.truncate(GIB + 1)
        assert oversize.stat().st_size == GIB + 1
        rejected = client.call("import_paths", {"paths": [str(oversize)]}, timeout=timeout)
        assert rejected["summary"] == {"imported": 0, "skipped": 0, "failed": 1}
        assert "1GB" in rejected["failed"][0]["reason"]
        assert all(source["filename"] != oversize.name for source in client.call("list_sources")["sources"])
        results["actual1GiBPlusOneRejected"] = True

        for pages, marker in ((301, "Boundarythreehundredlast"), (3000, "Boundarythreethousandlast")):
            path = directory / f"页数边界{pages}.pdf"
            pages_pdf(path, pages, marker)
            results[f"actual{pages}Pages"] = verify_import(client, path, marker, pages, timeout=timeout)

        too_many = directory / "超过三千页.pdf"
        pages_pdf(too_many, 3001, "Mustnotindexpageproof")
        rejected = client.call("import_paths", {"paths": [str(too_many)]}, timeout=timeout)
        assert rejected["summary"] == {"imported": 0, "skipped": 0, "failed": 1}
        assert "3000" in rejected["failed"][0]["reason"]
        assert not client.call("search_lexical", {"query": "Mustnotindexpageproof"})["hits"]
        results["actual3001PagesRejected"] = True
    finally:
        client.close()
    return {"status": "passed", "workerVersion": version, "packagedWorker": worker is not None,
        "syntheticLibrary": True, "userLibraryOpened": False, "modelCalls": 0,
        "temporaryArtifactsRemovedOnExit": True, "initialFreeBytes": free,
        "networkDeniedByHarness": worker is None, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--temp-parent", type=Path, help="Temporary volume; never use an existing library here")
    parser.add_argument("--timeout", type=int, default=600, help="Per-RPC smoke deadline, not a production timeout")
    parser.add_argument("--report", type=Path, help="Optional new JSON report, never replaced if already present")
    args = parser.parse_args()
    if args.report and args.report.exists():
        raise RuntimeError("Refusing to overwrite an existing report")
    with tempfile.TemporaryDirectory(prefix="shiwei-large-smoke-", dir=args.temp_parent) as temporary:
        result = run(Path(temporary), args.worker, args.timeout)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
