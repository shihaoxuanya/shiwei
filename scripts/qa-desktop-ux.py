"""Loopback-only UX QA: production Worker + synthetic fixtures; not a product server.

The browser's OS IPC and model service are explicitly simulated. All import,
SQLite note save, FTS, retrieval, conversation and citation behavior is real.
Optional statistics are never sent; UI consent choices live only in this process.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from shiwei_ai.worker import WorkerServer
from shiwei_ai.worker.server import WorkerMethodError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=1422)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--root", type=Path)
    options = parser.parse_args()
    root = (options.root or Path(tempfile.mkdtemp(prefix="shiwei-desktop-ux-"))).resolve()
    marker = root / "qa-fixture.json"
    if not marker.is_file():
        seed = [sys.executable, str(Path(__file__).with_name("prepare-desktop-ux.py")), "--root", str(root)]
        if options.empty:
            seed.append("--empty")
        subprocess.run(seed, check=True)
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True or Path(manifest["dataDir"]).resolve() != root / "library":
        raise RuntimeError("Refusing a library without a matching synthetic QA ownership marker")
    print(json.dumps({"resumedSyntheticLibrary": str(root / "library")}, ensure_ascii=False), flush=True)
    spec = importlib.util.spec_from_file_location("qa_gateway", Path(__file__).with_name("qa-chat-ux.py"))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-single-writer")

    def initialize():
        server = WorkerServer(root / "library")
        server._gateway = fixture.FixtureGateway()
        server._provider_public = {"providerId": "custom", "protocol": "openai_compatible", "baseUrl": "https://synthetic.invalid/v1", "chatModel": "synthetic-qa-only", "embeddingModel": "", "embeddingMode": "none"}
        original_update = server._handlers["update_note"]
        failed_notes = set()
        def update(params):
            note_id = params.get("noteId", "")
            if "保存失败测试" in params.get("content", "") and note_id not in failed_notes:
                failed_notes.add(note_id)
                raise WorkerMethodError("QA_SAVE_FAILED", "隔离测试模拟：本次保存失败，点击重试即可恢复。")
            return original_update(params)
        server._handlers["update_note"] = update
        return server

    server = executor.submit(initialize).result()
    methods = {"ping", "worker_info", "chat", "cancel_chat", "get_conversation", "delete_conversation", "list_conversations", "list_sources", "import_paths", "delete_source", "reindex_source", "search_lexical", "search_hybrid", "list_notes", "get_note", "create_note", "update_note", "index_note", "delete_note", "provider_status", "index_status", "qa_fixture_paths", "qa_release_status", "qa_analytics_consent"}
    # Mirror a fresh production installation's disclosed default, but remain
    # entirely in memory: this QA bridge has no analytics sender or endpoint.
    consent = {"enabled": True, "choice": "enabled", "needsChoice": False}
    version = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:1420")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self):
            if self.headers.get("Origin") != "http://127.0.0.1:1420" or self.path != "/rpc":
                self.send_error(403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1_000_000:
                self.send_error(413)
                return
            try:
                payload = json.loads(self.rfile.read(size))
                method, params = payload["method"], payload.get("params") or {}
                if method not in methods or not isinstance(params, dict):
                    raise ValueError()
                if method == "import_paths" and (not params.get("paths") or not all(Path(path).resolve().is_relative_to(root / "inputs") for path in params["paths"])):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                self.send_error(403)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:1420")
            self.end_headers()
            def emit(envelope):
                try:
                    self.wfile.write((json.dumps(envelope, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            if method == "cancel_chat":
                emit({"result": {"cancelled": server.cancel_chat(str(params.get("clientRequestId", "")))}})
                return
            if method == "chat":
                client_id = params.setdefault("clientRequestId", str(uuid4()))
                if isinstance(client_id, str) and 1 <= len(client_id) <= 128:
                    # Register before queueing so Stop can mark an already queued
                    # request without waiting for SQLite's single-writer executor.
                    server.prepare_chat(client_id)
            def run():
                server._event_sink = emit
                if method == "qa_fixture_paths":
                    emit({"result": manifest})
                    return
                if method in {"qa_release_status", "qa_analytics_consent"}:
                    if method == "qa_analytics_consent":
                        enabled = params.get("enabled") is True
                        consent.update(enabled=enabled, choice="enabled" if enabled else "disabled", needsChoice=False)
                    emit({"result": {"version": version, "channel": "local-qa", "updaterConfigured": False, "analytics": {**consent, "configured": False}}})
                    return
                if method == "chat":
                    params["stream"] = True
                if method == "provider_status":
                    emit({"result": {**server._provider_status({}), "hasApiKey": True}})
                    return
                line = json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": str(uuid4()), "method": method, "params": params})
                emit(json.loads(server.process_line(line)))
            executor.submit(run).result()

    print(f"UX QA http://127.0.0.1:{options.port} — real Worker; synthetic library/model; no external model/statistics requests", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", options.port), Handler).serve_forever()
    finally:
        executor.submit(server._get_importer().close).result()
        executor.shutdown()


if __name__ == "__main__":
    main()
