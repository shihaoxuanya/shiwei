"""Synthetic public snapshots through the real SQLite/FTS/chat/move pipeline."""
import base64
import json
from dataclasses import replace
from pathlib import Path
import pytest

from shiwei_ai.ingestion import Importer
from shiwei_ai.ingestion.web_fetch import WebSnapshot, WebImportError, normalize_public_url
from shiwei_ai.ingestion.web_extract import extract_document, document_body_hash
from shiwei_ai.models import ModelGateway
from shiwei_ai.worker import WorkerServer


def snapshot(url="https://example.com/article", body="星河迁移会议明确记录单次全量迁移38~50天，增量追平7~14天。"):
    raw = ("<html><head><title>星河迁移会议</title></head><body><nav>导航噪声</nav>"
           "<article><h1>星河迁移会议</h1><p>" + body + "</p>"
           "<pre>print('安全代码')</pre><script>fetch('https://evil.invalid/')</script></article></body></html>").encode()
    doc = extract_document(raw)
    return WebSnapshot(normalize_public_url(url), "https://example.com/article", "2026-09-10T02:00:00+00:00", raw, doc, document_body_hash(doc))


def rpc(server, method, params=None):
    result = json.loads(server.process_line(json.dumps({"id": "test", "jsonrpc": "2.0", "protocol_version": "1.0", "method": method, "params": params or {}})))
    assert "error" not in result, result
    return result["result"]


class SyntheticModel(ModelGateway):
    def chat(self, messages, **options):
        # Select only a context whose contents really support the source fact.
        import re
        context = messages[-1]["content"]
        match = re.search(r"\[(S\d+)\]\n(?:(?!\[S\d+\]).)*38～50", context, re.S)
        assert match, context
        return f"星河迁移会议记录：单次全量迁移38～50天，增量追平7～14天。[{match[1]}]"
    def stream_chat(self, messages, **options):
        yield self.chat(messages, **options)
    def embed(self, texts):
        raise AssertionError("This suite is local lexical recall only")


def test_capture_inert_archive_dedup_changed_content_distinct_aliases(tmp_path, monkeypatch):
    importer = Importer(tmp_path / "库")
    current = snapshot()
    monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", lambda url: replace(current, original_url=normalize_public_url(url)))
    try:
        first = importer.import_url("https://example.com/article#top")
        source_id = first["imported"][0]["sourceId"]
        again = importer.import_url("https://EXAMPLE.com:443/article#next")
        assert again["skipped"][0]["sourceId"] == source_id
        old = importer.list_sources()[0]
        archive = json.loads(Path(old["storedPath"]).read_text(encoding="utf-8"))
        assert Path(old["storedPath"]).suffix == ".websnapshot"
        assert base64.b64decode(archive["rawHtmlBase64"]) == current.raw_html
        saved = importer.get_web_snapshot(source_id)
        assert "<script>" not in json.dumps(saved) and "evil.invalid" not in json.dumps(saved)
        assert "导航噪声" not in json.dumps(saved, ensure_ascii=False)
        assert saved["chunks"] and all(0 <= x["sectionIndex"] < len(saved["sections"]) for x in saved["chunks"])
        alias = importer.import_url("https://example.com/alias")
        assert alias["imported"][0]["sourceId"] != source_id
        current = snapshot(body="星河迁移新版本：单次迁移50~60天，增量追平7~14天。")
        changed = importer.import_url("https://example.com/article")
        assert changed["imported"][0]["sourceId"] != source_id
        assert importer.get_web_snapshot(source_id) == saved
        assert len(importer.list_sources()) == 3
        assert all(s["sourceType"] == "web_page" and s["originalPath"] == "" for s in importer.list_sources())
    finally:
        importer.close()


def test_offline_restart_recall_citations_reindex_migrate_delete_preserves_other_memory(tmp_path, monkeypatch):
    old = tmp_path / "旧库"
    parent = tmp_path / "目标"
    parent.mkdir()
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(tmp_path / "pointer.json"))
    monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", lambda url: snapshot(url))
    server = WorkerServer(old)
    report = rpc(server, "import_url", {"url": "https://example.com/article"})
    source_id = report["imported"][0]["sourceId"]
    note = rpc(server, "create_note")["note"]
    rpc(server, "update_note", {"noteId": note["id"], "title": "其他笔记", "content": "无关原始内容必须保留。"})
    original = tmp_path / "中文原件.txt"
    original.write_text("原始文件不可更改，原始文件不可删除。", encoding="utf-8")
    rpc(server, "import_paths", {"paths": [str(original)]})
    before = rpc(server, "get_web_snapshot", {"sourceId": source_id})
    raw_path = Path(next(x for x in rpc(server, "list_sources")["sources"] if x["id"] == source_id)["storedPath"])
    raw_bytes = raw_path.read_bytes()
    rpc(server, "shutdown")
    def no_network(*args, **kwargs):
        raise AssertionError("Offline local workflow must not fetch")
    monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", no_network)
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    server = WorkerServer(old)
    try:
        assert rpc(server, "get_web_snapshot", {"sourceId": source_id}) == before
        server._gateway = SyntheticModel()
        server._provider_public = {"embeddingMode": "none"}
        hits = rpc(server, "search_lexical", {"query": "星河迁移", "sourceType": "web_page"})["hits"]
        assert hits and {x["sourceId"] for x in hits} == {source_id}
        answer = rpc(server, "chat", {"query": "星河迁移会议全量迁移多久？"})
        assert "38～50 天" in answer["answer"] and answer["citations"]
        citation = answer["citations"][0]
        assert citation["sourceType"] == "web_page" and citation["sourceId"] == source_id
        assert citation["pageNumber"] is None and citation["citationId"].startswith("S")
        assert citation["originalUrl"] == "https://example.com/article"
        old_citations = answer["citations"]
        rpc(server, "reindex_source", {"sourceId": source_id})
        assert raw_path.read_bytes() == raw_bytes
        history = rpc(server, "get_conversation", {"conversationId": answer["conversationId"]})["conversation"]
        assert history["messages"][-1]["citations"] == old_citations
        moved = rpc(server, "relocate_library", {"destinationParent": str(parent)})
        after = rpc(server, "get_web_snapshot", {"sourceId": source_id})
        assert after == before
        moved_path = Path(next(x for x in rpc(server, "list_sources")["sources"] if x["id"] == source_id)["storedPath"])
        assert moved_path != raw_path and moved_path.read_bytes() == raw_bytes
        rpc(server, "delete_source", {"sourceId": source_id})
        assert not moved_path.exists() and raw_path.read_bytes() == raw_bytes
        assert original.is_file() and rpc(server, "get_note", {"noteId": note["id"]})["note"]["content"] == "无关原始内容必须保留。"
        assert not rpc(server, "search_lexical", {"query": "星河迁移", "sourceType": "web_page"})["hits"]
    finally:
        rpc(server, "shutdown")


@pytest.mark.parametrize("error", [WebImportError("网页读取超时，请稍后重试"), RuntimeError("private-body https://private.invalid?secret=key")])
def test_fetch_failure_retry_and_zero_content_logs(tmp_path, monkeypatch, caplog, error):
    server = WorkerServer(tmp_path / "库")
    def fail(url):
        raise error
    monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", fail)
    try:
        report = rpc(server, "import_url", {"url": "https://example.com/article"})
        assert report["failed"][0]["inputKind"] == "url"
        assert "private-body" not in str(report) and "secret=key" not in str(report)
        assert not rpc(server, "list_sources")["sources"]
        assert "example.com" not in caplog.text and "private-body" not in caplog.text
        monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", lambda url: snapshot(url))
        assert rpc(server, "import_url", {"url": "https://example.com/article"})["summary"]["imported"] == 1
    finally:
        rpc(server, "shutdown")


def test_upgrade_v6_preserves_existing_sources(tmp_path):
    import sqlite3
    from shiwei_ai.storage.database import MIGRATIONS, Database
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    for version, sql in MIGRATIONS:
        if version >= 7:
            break
        con.executescript(sql)
        con.execute("INSERT INTO schema_migrations VALUES (?, 'old')", (version,))
    con.execute("INSERT INTO sources(id,original_path,original_filename,stored_path,content_hash,size,imported_at,status) VALUES ('old','original.txt','original.txt','raw.txt','abc',3,'old','searchable')")
    con.commit()
    con.close()
    db = Database(path)
    try:
        rows = db.list_sources()
        assert rows[0]["id"] == "old" and rows[0]["source_type"] == "imported_file" and rows[0]["original_url"] is None
        assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def test_optional_vectors_use_web_title_body_and_hydrate_snapshot_identity(tmp_path, monkeypatch):
    from shiwei_ai.models import EmbeddingResult
    from shiwei_ai.retrieval import EmbeddingIndexer, HybridRetriever, LanceVectorStore
    class RecordingModel(SyntheticModel):
        def __init__(self):
            self.inputs = []
        def embed(self, texts):
            self.inputs.extend(texts)
            return EmbeddingResult(vectors=[[1.0, 0.5, 0.2] for _ in texts], model="synthetic-web", dimension=3)
    importer = Importer(tmp_path / "库")
    monkeypatch.setattr("shiwei_ai.ingestion.importer.fetch_snapshot", lambda url: snapshot(url))
    try:
        source = importer.import_url("https://example.com/article")["imported"][0]["sourceId"]
        model = RecordingModel()
        store = LanceVectorStore(tmp_path / "vectors")
        EmbeddingIndexer(importer.database, store, model).rebuild()
        assert any("星河迁移会议" in item and "38~50" in item for item in model.inputs)
        assert all("<script>" not in item and "evil.invalid" not in item for item in model.inputs)
        retrieval = HybridRetriever(importer.database, gateway=model, semantic_index=store).retrieve("星河迁移会议全量迁移多久？")
        assert retrieval["semanticHits"] and retrieval.context.citations
        assert all(hit["sourceId"] == source and hit["sourceType"] == "web_page" and hit["capturedAt"] for hit in retrieval["semanticHits"])
        assert all(citation.source_id == source and citation.original_url == "https://example.com/article" and citation.page_number is None for citation in retrieval.context.citations)
    finally:
        importer.close()
