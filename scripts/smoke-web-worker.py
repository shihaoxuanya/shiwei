"""Real public-URL -> isolated JSONL Worker -> offline/local snapshot smoke test.

Run with the project's Python environment, optionally --worker <packaged sidecar>.
No model provider, user library, network mocking, or production telemetry is used.
Development restarts use a subprocess-local audit hook that denies networking.
Frozen Workers cannot use that hook; their report explicitly says so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_BOOTSTRAP = r'''
import json, os, runpy, socket, sys
from pathlib import Path
attempts = 0
def deny_network(event, args):
    global attempts
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}:
        attempts += 1
        raise OSError("QA subprocess network is disabled")
sys.addaudithook(deny_network)
try:
    socket.getaddrinfo("example.com", 443)
except OSError:
    pass
assert attempts == 1, "Offline audit hook was not installed"
attempts = 0
try:
    runpy.run_module("shiwei_ai.worker.main", run_name="__main__")
finally:
    Path(os.environ["SHIWEI_QA_OFFLINE_REPORT"]).write_text(
        json.dumps({"hookActive": True, "blockedAttempts": attempts}), encoding="utf-8")
'''


class Client:
    def __init__(self, root: Path, worker: Path | None, *, offline: bool = False, sequence: int = 0):
        self.offline_report = root / f"offline-{sequence}.json"
        self.network_denied = offline and worker is None
        command = [str(worker.resolve())] if worker else [sys.executable, "-m", "shiwei_ai.worker.main"]
        if self.network_denied:
            command = [sys.executable, "-c", OFFLINE_BOOTSTRAP]
        env = {**os.environ, "SHIWEI_DATA_DIR": str(root / "original-library"),
               "SHIWEI_LOCATION_CONFIG": str(root / "config" / "library-location.json"),
               "SHIWEI_TELEMETRY_DISABLED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
               "PYTHONIOENCODING": "utf-8", "SHIWEI_QA_OFFLINE_REPORT": str(self.offline_report)}
        self.process = subprocess.Popen(command, cwd=ROOT / "services" / "ai-worker", env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.lines: Queue[str | None] = Queue()
        self.events: list[dict] = []
        self.logs: list[str] = []

        def read_stdout():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)

        def read_stderr():
            # Kept only in this ephemeral harness; never printed or persisted.
            for line in self.process.stderr:
                self.logs.append(line)

        Thread(target=read_stdout, daemon=True).start()
        self.log_thread = Thread(target=read_stderr, daemon=True)
        self.log_thread.start()

    def call(self, method: str, params: dict | None = None, *, timeout: float = 90):
        request_id = str(uuid4())
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": request_id,
            "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - monotonic()))
            except Empty:
                raise RuntimeError(f"Worker {method} exceeded the smoke-test deadline") from None
            if line is None:
                raise RuntimeError(f"Worker exited before {method} completed")
            value = json.loads(line)
            if "event" in value:
                self.events.append(value)
            elif value.get("id") == request_id:
                if "error" in value:
                    # Worker messages are already sanitized; do not dump envelopes/content.
                    raise RuntimeError(f"Worker {method}: {value['error'].get('code', 'ERROR')}")
                return value["result"]
            if monotonic() > deadline:
                raise RuntimeError(f"Worker {method} exceeded the smoke-test deadline")

    def close(self):
        if self.process.poll() is None:
            try:
                self.call("shutdown", timeout=10)
                self.process.stdin.close()
                self.process.wait(timeout=10)
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
        self.log_thread.join(timeout=2)
        if self.network_denied:
            report = json.loads(self.offline_report.read_text(encoding="utf-8"))
            assert report == {"hookActive": True, "blockedAttempts": 0}, "Local snapshot operations attempted networking"


def content(snapshot: dict) -> str:
    return "\n".join(block["text"] for section in snapshot["sections"] for block in section["blocks"])


def run(root: Path, worker: Path | None, public_url: str) -> dict:
    marker = "shiwei_smoke_" + uuid4().hex
    parts = urlsplit(public_url)
    url = urlunsplit((parts.scheme, parts.netloc, parts.path,
        urlencode([*parse_qsl(parts.query), ("qa", marker)]), ""))
    original = root / "旧资料保留.txt"
    original.write_text("Synthetic older file: evergreenproof local original stays intact.", encoding="utf-8")
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    note_body = "Synthetic retained note: cedarproof migration verification only."
    destination = root / "迁移目标"
    destination.mkdir()
    sentinel = destination / "不要覆盖.txt"
    sentinel.write_text("retained", encoding="utf-8")
    clients = []
    passed = []
    first = Client(root, worker)
    clients.append(first)
    try:
        version = first.call("ping")["workerVersion"]
        assert first.call("provider_status")["configured"] is False
        imported = first.call("import_paths", {"paths": [str(original)]})
        assert imported["summary"]["imported"] == 1
        file_id = imported["imported"][0]["sourceId"]
        note = first.call("create_note")["note"]
        first.call("update_note", {"noteId": note["id"], "title": "合成保留笔记", "content": note_body})
        report = first.call("import_url", {"url": url})
        if report["summary"]["imported"] != 1:
            reasons = [item.get("reason", "unknown") for item in report.get("failed", [])]
            raise RuntimeError("Real public webpage could not be imported (no mocked success): " + "; ".join(reasons))
        source_id = report["imported"][0]["sourceId"]
        before = first.call("worker_info")["dataDir"]
        snapshot = first.call("get_web_snapshot", {"sourceId": source_id})
        assert snapshot["title"] and len(content(snapshot)) > 100
        assert snapshot["chunks"]
        assert snapshot["originalUrl"] == url
        assert snapshot["capturedAt"]
        assert "rawHtmlBase64" not in snapshot
        assert "Python" in content(snapshot)
        source = next(item for item in first.call("list_sources")["sources"] if item["id"] == source_id)
        stored = Path(source["storedPath"])
        assert source["sourceType"] == "web_page" and stored.suffix == ".websnapshot"
        archive_hash = hashlib.sha256(stored.read_bytes()).hexdigest()
        duplicate = first.call("import_url", {"url": url})
        assert duplicate["summary"] == {"imported": 0, "skipped": 1, "failed": 0}, "Stable public body changed or duplicate import failed"
        assert duplicate["skipped"][0]["sourceId"] == source_id
        assert any(event.get("event") == "job_progress" for event in first.events)
        passed += ["real-public-html", "snapshot-saved", "snapshot-no-raw-html-ipc", "progress", "duplicate"]
    finally:
        first.close()

    second = Client(root, worker, offline=True, sequence=1)
    clients.append(second)
    try:
        reloaded = second.call("get_web_snapshot", {"sourceId": source_id})
        assert content(reloaded) == content(snapshot)
        assert reloaded["capturedAt"] == snapshot["capturedAt"]
        reindex = second.call("reindex_source", {"sourceId": source_id})
        assert reindex["chunkCount"] > 0
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == archive_hash
        current = second.call("get_web_snapshot", {"sourceId": source_id})
        chunk_ids = {item["chunkId"] for item in current["chunks"]}
        hits = second.call("search_lexical", {"query": "Python", "sourceType": "web_page"})["hits"]
        assert hits and {item["sourceId"] for item in hits} == {source_id}
        found = second.call("search_hybrid", {"query": "Python"})
        citations = found["citations"]
        assert citations
        for citation in citations:
            assert citation["citationId"].startswith("S")
            assert citation["sourceType"] == "web_page"
            assert citation["sourceId"] == source_id and citation["chunkId"] in chunk_ids
            assert citation["pageNumber"] is None
            assert citation["originalUrl"] == url
        answer = second.call("chat", {"query": snapshot["title"] + "的内容是什么？"})
        assert answer["citations"] and {item["sourceId"] for item in answer["citations"]} == {source_id}
        conversation_id = answer["conversationId"]
        history = second.call("get_conversation", {"conversationId": conversation_id})["conversation"]
        assert history["messages"][-1]["citations"][0]["sourceType"] == "web_page"
        migration = second.call("relocate_library", {"destinationParent": str(destination)})
        after = second.call("worker_info")["dataDir"]
        assert after == migration["dataDir"] and after != before and migration["retainedOriginal"]
        assert Path(before).is_dir() and stored.is_file()
        assert content(second.call("get_web_snapshot", {"sourceId": source_id})) == content(snapshot)
        passed += ["restart-snapshot", "local-reindex-no-refetch", "lexical-recall", "hybrid-citations", "chat-citations", "history", "verified-relocation"]
    finally:
        second.close()

    third = Client(root, worker, offline=True, sequence=2)
    clients.append(third)
    try:
        assert third.call("worker_info")["dataDir"] == after
        assert third.call("get_note", {"noteId": note["id"]})["note"]["content"] == note_body
        sources = third.call("list_sources")["sources"]
        migrated = next(item for item in sources if item["id"] == source_id)
        assert Path(migrated["storedPath"]).is_relative_to(Path(after))
        assert hashlib.sha256(Path(migrated["storedPath"]).read_bytes()).hexdigest() == archive_hash
        assert content(third.call("get_web_snapshot", {"sourceId": source_id})) == content(snapshot)
        assert third.call("get_conversation", {"conversationId": conversation_id})["conversation"]["messages"]
        assert third.call("delete_source", {"sourceId": source_id})["deleted"]
        assert not Path(migrated["storedPath"]).exists()
        assert stored.is_file(), "Retained old-library snapshot must not be deleted"
        assert all(item["sourceId"] != source_id for item in third.call("search_lexical", {"query": "Python"})["hits"])
        remaining = third.call("list_sources")["sources"]
        assert file_id in {item["id"] for item in remaining}
        assert third.call("search_lexical", {"query": "evergreenproof"})["hits"]
        assert third.call("get_note", {"noteId": note["id"]})["note"]["content"] == note_body
        assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
        assert sentinel.read_text(encoding="utf-8") == "retained"
        passed += ["relocated-restart", "delete-only-managed-web-snapshot", "old-file-note-preserved", "destination-preserved"]
    finally:
        third.close()

    assert all(marker not in "".join(client.logs) for client in clients), "URL query marker leaked into Worker logs"
    passed.append("url-query-not-logged")
    return {"status": "passed", "workerVersion": version, "packagedWorker": worker is not None,
        "publicNetworkMocked": False, "publicHost": parts.hostname, "syntheticLibrary": True,
        "userLibraryOpened": False, "modelCalls": 0, "productionTelemetry": False,
        "networkDeniedByHarnessOnRestart": worker is None,
        "offlineLimit": None if worker is None else "Frozen Worker ran local-only operations; physical network disconnection was not enforced by this harness.",
        "passed": passed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--root", type=Path, help="Optional new/empty QA directory; retained for inspection")
    parser.add_argument("--url", default="https://www.python.org/about/")
    args = parser.parse_args()
    if args.root:
        root = args.root.resolve()
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("Refusing a non-empty QA directory")
        root.mkdir(parents=True, exist_ok=True)
        result = run(root, args.worker, args.url)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with tempfile.TemporaryDirectory(prefix="shiwei-web-smoke-") as directory:
            result = run(Path(directory), args.worker, args.url)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
