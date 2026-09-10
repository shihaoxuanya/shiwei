"""Run the real JSONL Worker through relocation and restart using synthetic data only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import sqlite3
import subprocess
import sys
from threading import Thread
from uuid import uuid4


class Client:
    def __init__(self, root: Path, worker: Path | None = None):
        env = {**os.environ, "SHIWEI_DATA_DIR": str(root / "original-library"),
               "SHIWEI_LOCATION_CONFIG": str(root / "config" / "library-location.json"),
               "SHIWEI_TELEMETRY_DISABLED": "1", "PYTHONIOENCODING": "utf-8"}
        project = Path(__file__).resolve().parents[1] / "services" / "ai-worker"
        command = [str(worker.resolve())] if worker else [sys.executable, "-m", "shiwei_ai.worker.main"]
        self.process = subprocess.Popen(command, cwd=project,
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.lines: Queue[str | None] = Queue()
        def read():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)
        Thread(target=read, daemon=True).start()
        self.events: list[dict] = []

    def call(self, method, params=None):
        request_id = str(uuid4())
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": request_id,
                                             "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        while (line := self.lines.get(timeout=60)) is not None:
            value = json.loads(line)
            if "event" in value:
                self.events.append(value)
            elif value.get("id") == request_id:
                assert "error" not in value, value.get("error")
                return value["result"]
        raise RuntimeError("Synthetic Worker exited before its response")

    def close(self):
        if self.process.poll() is None:
            try:
                self.call("shutdown")
                self.process.stdin.close()
                self.process.wait(timeout=10)
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Refusing a non-empty QA directory")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "target-parent"
    destination.mkdir()
    sentinel = destination / "已有文件.txt"
    sentinel.write_text("do not overwrite", encoding="utf-8")
    original = root / "导入资料.txt"
    original.write_text("独立合成资料：水杉数据库恢复演练，SQL_ID 8v5abc。", encoding="utf-8")
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    note_text = "合成会议记录：2025年9月3日讨论Oracle至TiDB迁移。单次38~50天，增量追平7~14天。"
    client = Client(root, args.worker)
    try:
        imported = client.call("import_paths", {"paths": [str(original)]})
        assert imported["summary"]["imported"] == 1
        note = client.call("create_note")["note"]
        saved = client.call("update_note", {"noteId": note["id"], "title": "2025年9月3日会议", "content": note_text})
        assert saved["note"]["content"] == note_text
        answer = client.call("chat", {"query": "2025年9月3日会议内容是什么？"})
        history_before = client.call("get_conversation", {"conversationId": answer["conversationId"]})["conversation"]
        assert history_before["messages"]
        before = client.call("worker_info")["dataDir"]
        migration = client.call("relocate_library", {"destinationParent": str(destination)})
        after = client.call("worker_info")["dataDir"]
        assert after == migration["dataDir"] and before != after
        assert Path(before).is_dir() and migration["retainedOriginal"]
        assert client.call("get_note", {"noteId": note["id"]})["note"]["content"] == note_text
        assert client.call("search_lexical", {"query": "水杉"})["hits"]
        history_after = client.call("get_conversation", {"conversationId": answer["conversationId"]})["conversation"]
        assert [m["content"] for m in history_before["messages"]] == [m["content"] for m in history_after["messages"]]
        phases = [e["data"]["phase"] for e in client.events if e.get("event") == "library_migration_progress"]
        assert {"preparing", "copying", "verifying", "switching"} <= set(phases)
    finally:
        client.close()
    restarted = Client(root, args.worker)
    try:
        assert restarted.call("worker_info")["dataDir"] == after
        assert restarted.call("get_note", {"noteId": note["id"]})["note"]["content"] == note_text
        restarted.call("update_note", {"noteId": note["id"], "title": "2025年9月3日会议", "content": note_text + "\n迁移后新增内容。"})
        assert restarted.call("search_lexical", {"query": "迁移后新增内容"})["hits"]
        with sqlite3.connect(Path(before, "shiwei.db").as_uri() + "?mode=ro", uri=True) as db:
            assert db.execute("SELECT content FROM notes WHERE id=?", (note["id"],)).fetchone()[0] == note_text
    finally:
        restarted.close()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"
    result = {"synthetic": True, "protocol": "real JSONL subprocess", "modelCalls": 0,
              "passed": ["import", "notes", "conversation", "fts", "progress", "verified-switch", "restart", "new-writes-isolated", "original-preserved", "destination-preserved"],
              "oldDataDir": before, "newDataDir": after, "copiedFiles": migration["copiedFiles"]}
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
