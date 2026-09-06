"""Exercise the packaged worker with real local PDFs and no model credentials."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/ai-worker/tests"))
from test_pdf_import import make_pdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    python_relative = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    command = [str(args.worker.resolve())] if args.worker else [str(ROOT / "services/ai-worker/.venv" / python_relative), "-m", "shiwei_ai.worker.main"]
    with tempfile.TemporaryDirectory(prefix="shiwei-pdf-smoke-") as temporary:
        directory = Path(temporary)
        text_pdf, scan_pdf = directory / "恢复记录.PDF", directory / "扫描案例.pdf"
        make_pdf(text_pdf)
        make_pdf(scan_pdf, scanned=True)
        if args.evidence:
            import pypdfium2 as pdfium
            args.evidence.mkdir(parents=True, exist_ok=True)
            for label, path in (("text", text_pdf), ("scan", scan_pdf)):
                with pdfium.PdfDocument(str(path)) as pdf:
                    for index in range(len(pdf)):
                        page = pdf[index]
                        bitmap = page.render(scale=1)
                        bitmap.to_pil().save(args.evidence / f"{label}-page-{index + 1}.png")
                        bitmap.close()
                        page.close()
        requests = [
            ("ping", {}),
            ("import_paths", {"paths": [str(text_pdf), str(scan_pdf)]}),
            ("search_lexical", {"query": "MOUNTED"}),
            ("chat", {"query": "MOUNTED"}),
            ("import_paths", {"paths": [str(text_pdf), str(scan_pdf)]}),
            ("shutdown", {}),
        ]
        wire = "".join(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": str(index), "method": method, "params": params}, ensure_ascii=False) + "\n" for index, (method, params) in enumerate(requests))
        env = {**os.environ, "SHIWEI_DATA_DIR": str(directory / "library"), "TEMP": str(directory), "TMP": str(directory), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(command, input=wire, text=True, encoding="utf-8", capture_output=True, env=env, cwd=ROOT / "services/ai-worker", timeout=180)
        assert result.returncode == 0, f"Worker exit code {result.returncode}: {result.stderr[-1000:]}"
        replies = {message["id"]: message for line in result.stdout.splitlines() if (message := json.loads(line)).get("id") is not None}
        assert all("error" not in reply for reply in replies.values()), replies
        expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
        assert replies["0"]["result"]["workerVersion"] == expected_version, replies["0"]
        assert replies["1"]["result"]["summary"] == {"imported": 2, "skipped": 0, "failed": 0}, replies["1"]
        citations = replies["3"]["result"]["citations"]
        assert citations and all(item["pageNumber"] == 2 for item in citations), citations
        assert replies["4"]["result"]["summary"]["skipped"] == 2
        print(json.dumps({"status": "passed", "workerVersion": expected_version, "importedRealPdfs": 2, "offlineScanOcr": True, "pageCitations": len(citations), "deduplicated": 2}, ensure_ascii=False))


if __name__ == "__main__":
    main()
