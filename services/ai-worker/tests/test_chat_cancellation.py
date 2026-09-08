import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from shiwei_ai.worker import WorkerServer


def rpc(method, params=None, request_id="test"):
    return json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": request_id, "method": method, "params": params or {}})


def test_cancelled_queued_chat_never_saves_an_exchange(tmp_path):
    server = WorkerServer(tmp_path / "data")
    server.prepare_chat("queued")
    assert server.cancel_chat("queued")
    answer = json.loads(server.process_line(rpc("chat", {"query": "你好", "clientRequestId": "queued"})))
    assert answer["error"]["code"] == "CHAT_CANCELLED"
    assert server._list_conversations({}) == {"conversations": []}
    assert not server.cancel_chat("queued")
    server._get_importer().close()


def test_cancel_is_request_scoped_and_leaves_sources_alone(tmp_path):
    server = WorkerServer(tmp_path / "data")
    file = tmp_path / "保留.txt"
    file.write_text("这是取消对话不能删除的合成资料。", encoding="utf-8")
    server._get_importer().import_paths([str(file)])
    server.prepare_chat("current")
    assert not server.cancel_chat("another")
    result = json.loads(server.process_line(rpc("chat", {"query": "你好", "clientRequestId": "current"})))
    assert "result" in result
    assert len(server._list_sources({})["sources"]) == 1
    assert not server.cancel_chat("current")
    server._get_importer().close()


def test_real_jsonl_stops_stream_without_killing_worker_or_saving_late_reply(tmp_path):
    # Actual stdin reader + sole dispatcher, synthetic model, no network/key.
    script = """
import time
from shiwei_ai.models import ModelGateway
from shiwei_ai.worker import main
class Gateway(ModelGateway):
    def chat(self, messages, **kwargs): return '通用说明'
    def embed(self, texts): raise AssertionError('No embedding')
    def stream_chat(self, messages, **kwargs):
        for i in range(200):
            yield '这是通用解释。'
            time.sleep(0.02)
factory = main.WorkerServer
def isolated(**kwargs):
    server = factory(**kwargs)
    server._gateway = Gateway()
    return server
main.WorkerServer = isolated
main.main()
"""
    env = {**os.environ, "SHIWEI_DATA_DIR": str(tmp_path / "data"), "SHIWEI_TELEMETRY_DISABLED": "1", "PYTHONIOENCODING": "utf-8"}
    child = subprocess.Popen([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = queue.Queue()
    threading.Thread(target=lambda: [output.put(json.loads(line)) for line in child.stdout], daemon=True).start()
    try:
        child.stdin.write(rpc("chat", {"query": "什么是数据库增量迁移？", "stream": True, "clientRequestId": "running"}, "chat") + "\n")
        child.stdin.flush()
        while output.get(timeout=15).get("event") != "chat_token":
            pass
        start = time.monotonic()
        child.stdin.write(rpc("cancel_chat", {"clientRequestId": "running"}, "cancel") + "\n")
        child.stdin.flush()
        while True:
            item = output.get(timeout=5)
            if item.get("id") == "chat":
                assert item["error"]["code"] == "CHAT_CANCELLED"
                break
        assert time.monotonic() - start < 3
        child.stdin.write(rpc("list_conversations", request_id="history") + "\n")
        child.stdin.flush()
        while (item := output.get(timeout=5)).get("id") != "history":
            pass
        assert item["result"]["conversations"] == []
        assert child.poll() is None
    finally:
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0, child.stderr.read()


def test_explicit_shutdown_exits_cleanly_while_parent_keeps_stdin_open(tmp_path):
    child = subprocess.Popen([sys.executable, "-m", "shiwei_ai.worker.main"], cwd=Path(__file__).resolve().parents[1], env={**os.environ, "SHIWEI_DATA_DIR": str(tmp_path / "data")}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        child.stdin.write(rpc("shutdown") + "\n")
        child.stdin.flush()
        child.wait(timeout=10)
        assert child.returncode == 0, child.stderr.read()
        assert json.loads(child.stdout.readline())["result"]["status"] == "stopping"
    finally:
        child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
