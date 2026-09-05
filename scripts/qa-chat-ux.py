"""Dev-only UX server: real WorkerServer + synthetic documents + deterministic gateway.

No credentials, network model calls, or user data. Its single HTTP request thread
also owns SQLite. Responses stream as NDJSON to the dedicated development entry.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from shiwei_ai.models import ModelGateway, ModelGatewayError
from shiwei_ai.worker import WorkerServer


class FixtureGateway(ModelGateway):
    def chat(self, messages, **options):
        query = messages[-1]["content"]
        intent = "knowledge" if "黄总" in query else "general"
        return json.dumps({"intent": intent, "keywords": []})

    def stream_chat(self, messages, **options):
        question = messages[-1]["content"]
        if "错误测试" in question:
            raise ModelGatewayError("模型连接失败（隔离测试模拟），请重试")
        if "等待测试" in question:
            time.sleep(18)
        if "几套环境" in question:
            if "实际最终确认需要五套环境" in question:
                yield "黄总最终确认需要五套环境。[N1]"
            else:
                yield "黄总最终确认需要四套环境。[N1]"
            return
        answer = "## 数据库恢复演练回顾\n\n这是一份**合成资料的测试回答**，用于检查长对话、滚动和来源展示，不是个人经历。[S1]\n\n"
        answer += "### 处理步骤\n\n1. 核对演练环境和备份时间。\n2. 检查 PDB 当前状态。\n3. 验证恢复结果并记录。[S1]\n\n"
        answer += "| 检查项 | 结果 |\n| --- | --- |\n| 测试环境 | 合成资料 |\n| PDB 状态 | 待人工确认 |\n\n```sql\nSELECT name, open_mode FROM v$pdbs;\n```\n\n"
        for n in range(1, 9):
            answer += f"### 回顾要点 {n}\n\n阅读较早的记录时，新生成的文字不应把视图强行拉到底部。输入框应始终可见，也可以先写好下一条问题。这段文字只用于界面验收。[S1]\n\n"
        for index in range(0, len(answer), 38):
            time.sleep(.15)
            yield answer[index:index + 38]

    def embed(self, texts):
        raise AssertionError("UX fixtures never enable embeddings")


def main():
    with tempfile.TemporaryDirectory(prefix="shiwei-chat-ux-") as directory:
        root = Path(directory)
        server = WorkerServer(root / "library")
        note = root / "记录.txt"
        note.write_text("合成测试资料。周五完成数据库恢复演练，检查 PDB 状态。", encoding="utf-8")
        from reportlab.pdfgen import canvas
        pdf = root / "YRR简历.pdf"
        doc = canvas.Canvas(str(pdf))
        doc.drawString(60, 770, "SYNTHETIC RESUME - QA ONLY")
        doc.drawString(60, 735, "Experience: database operations and recovery.")
        doc.save()
        server._get_importer().import_paths([str(note), str(pdf)])
        server._gateway = FixtureGateway()
        db = server._get_importer().database.connection
        for n in range(76):
            date = f"2026-09-03T12:{n // 60:02}:{n % 60:02}Z"
            identifier = f"fixture-{n:03}"
            title = f"合成验收话题 {n + 1}"
            content = "磁盘容量验收标记，只存在于这条较早的回复中。" if n == 0 else "这是一段隔离测试的历史回答，可继续提问。"
            db.execute("INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)", (identifier, title, date, date))
            db.execute("INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)", (str(uuid4()), identifier, content, date))
        db.commit()

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
                if not 0 < size <= 20000:
                    self.send_error(413)
                    return
                payload = json.loads(self.rfile.read(size))
                if payload.get("method") not in {"ping", "chat", "get_conversation", "delete_conversation", "list_conversations", "list_sources", "list_notes", "get_note", "create_note", "update_note", "delete_note", "provider_status", "index_status"}:
                    self.send_error(403)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:1420")
                self.end_headers()

                def emit(envelope):
                    self.wfile.write((json.dumps(envelope, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()

                server._event_sink = emit
                params = payload.get("params") or {}
                if payload["method"] == "chat":
                    params["stream"] = True
                result = server.process_line(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": str(uuid4()), "method": payload["method"], "params": params}))
                emit(json.loads(result))

        print("UX QA http://127.0.0.1:1422 — synthetic library and simulated gateway; no user data", flush=True)
        try:
            HTTPServer(("127.0.0.1", 1422), Handler).serve_forever()
        finally:
            server._get_importer().close()


if __name__ == "__main__":
    main()
