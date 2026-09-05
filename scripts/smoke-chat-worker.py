"""Temporary-library integration/visual QA; never reads the user's library or credentials."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RECALL_QUERIES = (
    "2025年9月3日我开会了吗？",
    "2025年9月3日会议内容是什么？",
    "我之前开了个会是在几号？",
    "之前那个Oracle迁移的会议是哪天？",
    "我什么时候讨论过TiDB迁移？",
)
ABSENT_RECALL_QUERIES = (
    "我之前讨论过Kubernetes扩容的会议是哪天？",
    "之前那个SAP采购的会议是哪天？",
    "我什么时候讨论过PostgreSQL采购？",
    "我之前那个财务预算会是哪天？",
    "我上次讨论员工调薪是什么时候？",
    "2032年12月31日Oracle迁移会议讲了什么？",
    "2025年2月30日Oracle迁移会议讲了什么？",
    "9月4日Oracle迁移会议讲了什么？",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="shiwei-chat-qa-") as tmp:
        directory = Path(tmp)
        from reportlab.pdfgen import canvas
        pdf = directory / "YRR简历.pdf"
        doc = canvas.Canvas(str(pdf))
        doc.setFont("Helvetica", 16)
        doc.drawString(60, 770, "SYNTHETIC RESUME - QA ONLY")
        doc.drawString(60, 735, "Experience: database operations and recovery.")
        doc.save()
        note = directory / "记录.txt"
        note.write_text("测试记录：周五完成数据库恢复演练，检查 PDB 状态。", encoding="utf-8")
        command = [str(args.worker.resolve())] if args.worker else [str(ROOT / "services/ai-worker/.venv/Scripts/python.exe"), "-m", "shiwei_ai.worker.main"]
        env = {**os.environ, "SHIWEI_DATA_DIR": str(directory / "library"), "TEMP": str(directory), "TMP": str(directory), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", cwd=ROOT / "services/ai-worker", env=env)
        sequence = 0

        def rpc(method, params=None):
            nonlocal sequence
            sequence += 1
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": str(sequence), "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            events = []
            while line := process.stdout.readline():
                envelope = json.loads(line)
                if "event" in envelope:
                    events.append(envelope)
                    continue
                if "error" in envelope:
                    raise RuntimeError(envelope["error"]["message"])
                return {"result": envelope["result"], "events": events}
            raise RuntimeError("Worker exited")

        try:
            version = rpc("ping")["result"]["workerVersion"]
            assert version == (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
            assert rpc("import_paths", {"paths": [str(pdf), str(note)]})["result"]["summary"]["imported"] == 2
            for query in ["简历", "我的简历在哪", "YRR简历.pdf"]:
                answer = rpc("chat", {"query": query})["result"]
                assert answer["answerKind"] == "source_lookup"
                assert answer["sourceMatches"][0]["filename"] == pdf.name
                assert Path(answer["sourceMatches"][0]["originalPath"]).is_file()
                assert Path(answer["sourceMatches"][0]["storedPath"]).is_file()
                assert not answer["citations"]
            answer = rpc("chat", {"query": "记录里有什么内容"})["result"]
            assert answer["sourceMatches"][0]["filename"] == note.name
            summary = rpc("chat", {"query": "帮我总结一下", "conversationId": answer["conversationId"]})["result"]
            assert {c["sourceFilename"] for c in summary["citations"]} == {note.name}
            assert rpc("chat", {"query": "你好"})["result"]["answerKind"] == "general"
            assert rpc("chat", {"query": "我的火星旅行经历"})["result"]["answerKind"] == "not_found"
            history = rpc("get_conversation", {"conversationId": answer["conversationId"]})["result"]["conversation"]
            assert history["messages"][-1]["sourceMatches"][0]["filename"] == note.name
            found = rpc("list_conversations", {"query": "帮我总结一下"})["result"]["conversations"]
            assert len(found) == 1 and found[0]["id"] == answer["conversationId"]
            assert found[0]["preview"]
            assert rpc("list_conversations", {"offset": 100})["result"]["conversations"] == []
            assert rpc("list_conversations", {"query": "%_"})["result"]["conversations"] == []
            created_note = rpc("create_note")["result"]["note"]
            saved_note = rpc("update_note", {"noteId": created_note["id"], "title": "TiDB会议记录", "content": "黄总最终确认不同阶段一共需要四套环境，而不是五套。"})["result"]["note"]
            assert saved_note["chunkCount"] == 1
            note_hits = rpc("search_lexical", {"query": "黄总 四套环境"})["result"]["hits"]
            assert note_hits[0]["sourceType"] == "user_note"
            note_answer = rpc("chat", {"query": "黄总之前确认需要几套环境？"})["result"]
            assert note_answer["citations"][0]["citationId"] == "N1"
            assert note_answer["citations"][0]["noteId"] == created_note["id"]
            rpc("update_note", {"noteId": created_note["id"], "title": "TiDB会议记录", "content": "实际最终确认需要五套环境。"})
            assert all("四套" not in hit["content"] for hit in rpc("search_lexical", {"query": "四套环境"})["result"]["hits"])
            assert rpc("search_lexical", {"query": "五套环境"})["result"]["hits"]
            rpc("delete_note", {"noteId": created_note["id"]})
            assert rpc("search_lexical", {"query": "五套环境"})["result"]["hits"] == []
            rpc("delete_conversation", {"conversationId": note_answer["conversationId"]})
            assert rpc("list_sources")["result"]["sources"]

            # Reproduce the release-blocking fuzzy recall case using only this
            # temporary library. A configured model must never be required here.
            assert rpc("provider_status")["result"]["configured"] is False
            meeting = rpc("create_note")["result"]["note"]
            meeting_content = (
                "本次会议围绕Oracle至TiDB数据迁移方案展开，明确了全量与增量迁移排期，"
                "探讨了应用适配测试环境的数据量需求及第三方增量同步工具选型。"
            )
            rpc("update_note", {"noteId": meeting["id"], "title": "2025年9月3日会议", "content": meeting_content})
            distractors = []
            for number in range(3):
                distractor = directory / f"OCP考试预约{number + 1}.txt"
                distractor.write_text(
                    f"Oracle OCP考试订单{number + 1}的有效期限为2026年11月28日。"
                    "082、083考试和赠送补考机会必须在截止日期之前使用。考试时间和日期安排。",
                    encoding="utf-8",
                )
                distractors.append(str(distractor))
            assert rpc("import_paths", {"paths": distractors})["result"]["summary"]["imported"] == 3
            for query in RECALL_QUERIES:
                recalled = rpc("chat", {"query": query})["result"]
                assert recalled["answerKind"] == "knowledge", (query, recalled)
                assert recalled["notice"] == "provider_not_configured", query
                assert recalled["citations"] and {c["noteId"] for c in recalled["citations"]} == {meeting["id"]}, query
                assert recalled["citations"][0]["citationId"] == "N1", query
                assert {source["noteId"] for source in recalled["sourceMatches"]} == {meeting["id"]}, query
                saved = rpc("get_conversation", {"conversationId": recalled["conversationId"]})["result"]["conversation"]
                saved_reply = saved["messages"][-1]
                assert saved_reply["answerKind"] == "knowledge", query
                assert saved_reply["citations"][0]["citationId"] == "N1", query
                assert {citation["noteId"] for citation in saved_reply["citations"]} == {meeting["id"]}, query
            for query in ABSENT_RECALL_QUERIES:
                rejected = rpc("chat", {"query": query})["result"]
                assert rejected["answerKind"] == "not_found", (query, rejected)
                assert rejected["citations"] == [] and rejected["sourceMatches"] == [], query
                saved = rpc("get_conversation", {"conversationId": rejected["conversationId"]})["result"]["conversation"]
                assert saved["messages"][-1]["citations"] == [], query
            assert rpc("get_note", {"noteId": meeting["id"]})["result"]["note"]["content"] == meeting_content
            print(json.dumps({"status": "passed", "workerVersion": version, "syntheticPdfRecall": True, "sourcePathsExist": True, "followupScoped": True, "historyPersisted": True, "notesUnifiedRetrieval": True, "noteReindexAndDelete": True, "conversationDelete": True, "fuzzyRecallPassed": len(RECALL_QUERIES), "fuzzyAbstentionPassed": len(ABSENT_RECALL_QUERIES), "noteCitationHistory": True, "realProviderConfigured": False, "userLibraryOpened": False}), flush=True)
            if args.serve:
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
                        if size > 16000:
                            self.send_error(413)
                            return
                        payload = json.loads(self.rfile.read(size))
                        method = payload.get("method")
                        if method not in {"chat", "list_conversations", "get_conversation", "ping"}:
                            self.send_error(403)
                            return
                        response = rpc(method, payload.get("params"))
                        self.send_response(200)
                        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:1420")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
                print("Synthetic-library QA bridge: http://127.0.0.1:1421", flush=True)
                HTTPServer(("127.0.0.1", 1421), Handler).serve_forever()
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
