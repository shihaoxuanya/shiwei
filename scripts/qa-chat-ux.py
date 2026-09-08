"""Dev-only UX server: real WorkerServer + synthetic documents + deterministic gateway.

No credentials, network model calls, or user data. Its single HTTP request thread
also owns SQLite. Responses stream as NDJSON to the dedicated development entry.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from shiwei_ai.models import ModelGateway, ModelGatewayError
from shiwei_ai.worker import WorkerServer


class FixtureGateway(ModelGateway):
    def chat(self, messages, **options):
        if any("以下是从用户本地知识库检索出的证据" in item["content"] for item in messages if item["role"] == "user"):
            return self._answer(messages)
        query = messages[-1]["content"]
        intent = "knowledge" if "黄总" in query else "general"
        return json.dumps({"intent": intent, "keywords": []})

    def stream_chat(self, messages, **options):
        question, _ = self._question_context(messages)
        if "错误测试" in question:
            raise ModelGatewayError("模型连接失败（隔离测试模拟），请重试")
        if "等待测试" in question:
            for _ in range(120):
                time.sleep(.2)
                yield "这是一段用于验证停止操作的合成回复。"
            return
        answer = self._answer(messages)
        for index in range(0, len(answer), 38):
            time.sleep(.15)
            yield answer[index:index + 38]

    @staticmethod
    def _question_context(messages):
        for message in reversed(messages):
            text = message["content"]
            if message["role"] == "user" and "以下是从用户本地知识库检索出的证据：\n\n" in text:
                question, context = text.split("以下是从用户本地知识库检索出的证据：\n\n", 1)
                context = context.rsplit("\n\n请直接回答当前问题。", 1)[0]
                return question.removeprefix("用户问题：\n").strip(), context
        return messages[-1]["content"], ""

    @classmethod
    def _answer(cls, messages):
        question, context = cls._question_context(messages)
        # These labels come only from the real ContextBuilder. Never hard-code
        # N1/S1: they map to the actual selected immutable chunk identities.
        blocks = re.findall(r"(?ms)^\[([NS]\d+)\]\n(.*?)(?=^\[[NS]\d+\]\n|\Z)", context)
        if blocks:
            citation, block = next(((cid, text) for cid, text in blocks
                                    if re.search(r"(?m)^(?:source|section):[^\n]*会议", text)), blocks[0])
            if "增量" in question:
                citation, block = next(((cid, text) for cid, text in blocks
                                        if re.search(r"增量[^\n。；]{0,20}?\d+\s*[～~]\s*\d+\s*天", text)), (citation, block))
            body = block.split("\n\ncontent:\n", 1)[-1].split("\n\n---", 1)[0].strip()
            suffix = f"[{citation}]"
            if "几套环境" in question:
                count = re.search(r"需要([一二三四五六七八九十\d]+)套环境", body)
                if count:
                    return f"记录中确认需要{count[1]}套环境。{suffix}"
            date = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", block.split("\n\ncontent:", 1)[0])
            if date and any(word in question for word in ("什么时候", "哪天", "几号", "日期")):
                return f"我这里记下的那场会议是{date[1]}。{suffix}"
            full = re.search(r"全量[^\n。；]{0,20}?(\d+\s*[～~]\s*\d+)\s*天", body)
            delta = re.search(r"增量[^\n。；]{0,20}?(\d+\s*[～~]\s*\d+)\s*天", body)
            if any(word in question for word in ("多久", "多长", "耗时", "几天")):
                selected = delta if "增量" in question else full
                if selected:
                    name = "增量追平" if "增量" in question else "单次全量迁移"
                    return f"会议明确记录：{name}预计需要{selected[1]} 天。{suffix}"
            if "会议" in block and full and delta:
                items = [f"单次全量迁移预计需要{full[1]} 天。", f"增量追平预计需要{delta[1]} 天。"]
                if "两轮" in body:
                    items.append("至少需要两轮完整生产级全量迁移，分别用于性能测试和生产使用。")
                if "测试环境" in body:
                    items.append("测试环境的数据量需要进一步确认。")
                if "TMS" in body and "OGG" in body:
                    items.append("增量同步考虑 TMS、OGG 方案。")
                return "这场会议主要讨论了 Oracle 至 TiDB 数据迁移方案：" + suffix + "\n\n" + "\n".join("- " + item for item in items)
            return "这份记录写到：\n\n" + body[:500] + suffix
        if "长回答测试" in question:
            return "## 合成阅读验收\n\n" + "\n\n".join(f"### 阅读要点 {index}\n\n阅读较早的记录时，新生成的文字不应强行滚到底部；输入框和来源入口需始终可达。这是隔离验收的模拟回复，不描述个人经历。" for index in range(1, 15))
        return "数据库增量迁移是持续捕获全量复制之后的新增、修改和删除，再将变化同步到目标数据库。\n\n这是隔离验收使用的模拟模型解释，不是个人经历。"

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
