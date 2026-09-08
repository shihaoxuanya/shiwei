"""Full local-library moves with synthetic content; no live provider or user DB."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.retrieval import LanceVectorStore
from shiwei_ai.worker import WorkerServer


def rpc(server, method, params=None):
    response = json.loads(server.process_line(json.dumps({
        "jsonrpc": "2.0", "protocol_version": "1.0", "id": "relocation-integration",
        "method": method, "params": params or {},
    })))
    assert "error" not in response, response
    return response["result"]


class SyntheticGateway(ModelGateway):
    def __init__(self):
        self.calls = []
        self.answer = "部署报告明确记录需要四套环境。[S1]"

    def chat(self, messages, **options):
        self.calls.append("chat")
        return self.answer

    def stream_chat(self, messages, **options):
        yield self.chat(messages, **options)

    def embed(self, texts):
        self.calls.append("embed")
        return EmbeddingResult(vectors=[[1.0, 0.5, 0.25] for _ in texts], model="migration-test", dimension=3)


def configure(server):
    gateway = SyntheticGateway()
    server._gateway = gateway
    server._provider_public = {"baseUrl": "https://synthetic.invalid/v1", "chatModel": "synthetic", "embeddingMode": "same", "embeddingModel": "migration-test"}
    return gateway


def state(connection, root):
    """Normalize only managed pointers, never text that happens to contain a path."""
    tables = ("schema_migrations", "sources", "documents", "sections", "chunks", "chunks_fts", "chunks_fts_trigram", "notes", "jobs", "conversations", "messages", "citations", "message_sources", "settings", "embedding_versions")
    result = {}
    for table in tables:
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        pointer = {"sources": "stored_path", "documents": "canonical_path"}.get(table)
        if pointer:
            for row in rows:
                if row[pointer]:
                    row[pointer] = str(Path(row[pointer]).relative_to(root))
        result[table] = rows
    return result


def data_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
            and path.name not in {".shiwei-library.lock", "shiwei.db", "shiwei.db-wal", "shiwei.db-shm", "shiwei.db-journal"}}


def vector_rows(root):
    store = LanceVectorStore(root / "index" / "lancedb")
    return sorted(store.connection.open_table("chunk_embeddings").search().limit(50000).to_list(), key=lambda row: row["chunk_id"])


def test_import_note_chat_fts_vectors_citations_restart_and_delete_after_move(tmp_path, monkeypatch):
    old = tmp_path / "旧资料库"
    parent = tmp_path / "新磁盘目录"
    parent.mkdir()
    pointer = tmp_path / "独立配置" / "library-location.json"
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(pointer))
    monkeypatch.setenv("SHIWEI_DATA_DIR", str(old))
    events = []
    server = WorkerServer(old, event_sink=events.append)
    restarted = None
    try:
        gateway = configure(server)
        literal_path = str(old / "raw" / "正文中的路径不能改.txt")
        original = tmp_path / "合成部署报告.txt"
        file_text = f"TiDB 部署报告明确确认需要四套环境。原始示例路径：{literal_path}。"
        original.write_text(file_text, encoding="utf-8")
        report = rpc(server, "import_paths", {"paths": [str(original)]})
        source_id = report["imported"][0]["sourceId"]
        note = rpc(server, "create_note")["note"]
        note_text = f"Oracle迁移到TiDB会议明确记录单次全量迁移38~50天，增量追平7~14天。示例路径：{literal_path}。"
        saved_note = rpc(server, "update_note", {"noteId": note["id"], "title": "2025年9月3日会议", "content": note_text})["note"]
        rpc(server, "index_note", {"noteId": note["id"], "revision": saved_note["updatedAt"]})
        file_answer = rpc(server, "chat", {"query": "合成部署报告里需要几套环境？"})
        assert file_answer["citations"] and file_answer["citations"][0]["sourceFilename"] == original.name
        gateway.answer = "会议明确记录单次全量迁移预计38～50 天。[N1]"
        note_answer = rpc(server, "chat", {"query": "2025年9月3日会议说单次全量迁移多久？"})
        assert note_answer["citations"] and note_answer["citations"][0]["noteId"] == note["id"]
        lookup_answer = rpc(server, "chat", {"query": "合成部署报告.txt在哪里？"})
        assert lookup_answer["sourceMatches"][0]["sourceId"] == source_id
        before_hits = rpc(server, "search_lexical", {"query": "Oracle 迁移"})["hits"]
        before_vectors = vector_rows(old)
        before_index = rpc(server, "index_status")
        assert before_index["needsRebuild"] is False
        before_state = state(server._get_importer().database.connection, old)
        before_hashes = data_hashes(old)
        gateway.calls.clear()

        result = rpc(server, "relocate_library", {"destinationParent": str(parent)})
        target = Path(result["dataDir"])
        assert result["previousDataDir"] == str(old) and result["retainedOriginal"] is True
        assert target.parent == parent and target != old
        assert json.loads(pointer.read_text(encoding="utf-8"))["dataDir"] == str(target)
        assert rpc(server, "worker_info")["dataDir"] == str(target)
        assert gateway.calls == [], "Relocation must not re-embed or resend source content"
        assert state(server._get_importer().database.connection, target) == before_state
        assert data_hashes(target) == before_hashes
        assert vector_rows(target) == before_vectors
        assert rpc(server, "index_status")["embeddingVersionId"] == before_index["embeddingVersionId"]
        assert rpc(server, "index_status")["needsRebuild"] is False
        assert {hit["chunkId"] for hit in rpc(server, "search_lexical", {"query": "Oracle 迁移"})["hits"]} == {hit["chunkId"] for hit in before_hits}
        assert rpc(server, "get_note", {"noteId": note["id"]})["note"]["content"] == note_text
        for answer in (file_answer, note_answer, lookup_answer):
            history = rpc(server, "get_conversation", {"conversationId": answer["conversationId"]})["conversation"]
            assert history["messages"][-1]["content"] == answer["answer"]
            for citation in history["messages"][-1]["citations"]:
                if citation["sourceType"] == "imported_file":
                    assert Path(citation["storedPath"]).is_relative_to(target)
                    assert citation["sourcePath"] == str(original)
                else:
                    assert citation["sourcePath"] == f"note://{note['id']}"
            for source in history["messages"][-1]["sourceMatches"]:
                if source["sourceType"] == "imported_file":
                    assert Path(source["storedPath"]).is_relative_to(target)
                    assert source["originalPath"] == str(original)
                else:
                    assert source["storedPath"] == ""
                    assert source["originalPath"] == ""
                    assert source["noteId"] == note["id"]
        assert {event["data"]["phase"] for event in events if event.get("event") == "library_migration_progress"} == {"preparing", "copying", "verifying", "switching"}
        server._shutdown({})

        # Starting from the saved pointer must win over the old fallback env.
        restarted = WorkerServer()
        configure(restarted)
        assert rpc(restarted, "worker_info")["dataDir"] == str(target)
        assert rpc(restarted, "get_note", {"noteId": note["id"]})["note"]["content"] == note_text
        assert rpc(restarted, "get_conversation", {"conversationId": file_answer["conversationId"]})["conversation"]["messages"][-1]["citations"][0]["chunkId"] == file_answer["citations"][0]["chunkId"]
        changed_note = note_text + "\n迁移后只在新库保存的编辑银杏。"
        rpc(restarted, "update_note", {"noteId": note["id"], "title": saved_note["title"], "content": changed_note})
        assert rpc(restarted, "get_note", {"noteId": note["id"]})["note"]["content"] == changed_note
        assert data_hashes(old) == before_hashes
        with sqlite3.connect(f"{(old / 'shiwei.db').as_uri()}?mode=ro", uri=True) as retained:
            assert state(retained, old) == before_state
        rpc(restarted, "delete_source", {"sourceId": source_id})
        rpc(restarted, "delete_note", {"noteId": note["id"]})
        assert rpc(restarted, "list_sources")["sources"] == []
        assert rpc(restarted, "list_notes")["notes"] == []
        assert original.read_text(encoding="utf-8") == file_text
        assert data_hashes(old) == before_hashes
        with sqlite3.connect(f"{(old / 'shiwei.db').as_uri()}?mode=ro", uri=True) as retained:
            assert state(retained, old) == before_state
    finally:
        server._shutdown({})
        if restarted is not None:
            restarted._shutdown({})


def test_pending_semantic_coverage_remains_pending_without_background_rebuild(tmp_path, monkeypatch):
    old = tmp_path / "old"
    parent = tmp_path / "destination"
    parent.mkdir()
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(tmp_path / "config.json"))
    server = WorkerServer(old)
    try:
        gateway = configure(server)
        original = tmp_path / "已理解.txt"
        original.write_text("这份合成文件已经有向量。", encoding="utf-8")
        rpc(server, "import_paths", {"paths": [str(original)]})
        note = rpc(server, "create_note")["note"]
        saved = rpc(server, "update_note", {"noteId": note["id"], "title": "尚未理解", "content": "本地笔记已经保存，但不要因迁移自动上传。"})["note"]
        assert saved["retrieval"]["semantic"] == "pending"
        before = rpc(server, "index_status")
        assert before["needsRebuild"] is True
        rows = vector_rows(old)
        gateway.calls.clear()
        target = Path(rpc(server, "relocate_library", {"destinationParent": str(parent)})["dataDir"])
        assert rpc(server, "get_note", {"noteId": note["id"]})["note"]["retrieval"]["semantic"] == "pending"
        after = rpc(server, "index_status")
        assert after["needsRebuild"] is True and after["embeddingVersionId"] == before["embeddingVersionId"]
        assert vector_rows(target) == rows and gateway.calls == []
    finally:
        server._shutdown({})


def test_another_worker_process_cannot_open_or_recover_jobs_in_a_leased_library(tmp_path):
    server = WorkerServer(tmp_path / "shared-library")
    try:
        database = server._get_importer().database
        database.create_job("lease-probe", "IMPORT", {"paths": ["synthetic-only"]})
        database.update_job("lease-probe", status="running", progress=0.2, current_step="synthetic in-progress")
        probe = """
from pathlib import Path
import sys
from shiwei_ai.ingestion import Importer
from shiwei_ai.storage.library_lock import LibraryLockError
try:
    importer = Importer(Path(sys.argv[1]))
except LibraryLockError:
    print('blocked-by-library-lease')
else:
    importer.close()
    raise SystemExit('ERROR: second writer opened library')
"""
        completed = subprocess.run([sys.executable, "-c", probe, str(server._get_importer().data_dir)], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=15)
        assert completed.returncode == 0 and completed.stdout.strip() == "blocked-by-library-lease", completed.stderr
        assert database.connection.execute("SELECT status FROM jobs WHERE id='lease-probe'").fetchone()[0] == "running"
    finally:
        server._shutdown({})


def test_stale_worker_cannot_move_retained_old_library_over_new_location_pointer(tmp_path, monkeypatch):
    old = tmp_path / "old"
    first_parent = tmp_path / "first-destination"
    second_parent = tmp_path / "second-destination"
    first_parent.mkdir()
    second_parent.mkdir()
    pointer = tmp_path / "config" / "library-location.json"
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(pointer))
    active = WorkerServer(old)
    stale = None
    try:
        note = rpc(active, "create_note")["note"]
        rpc(active, "update_note", {"noteId": note["id"], "title": "合成旧记录", "content": "旧位置保留此版本。"})
        result = rpc(active, "relocate_library", {"destinationParent": str(first_parent)})
        rpc(active, "update_note", {"noteId": note["id"], "title": "新库中的当前记录", "content": "新位置保存了后续编辑。"})
        pointer_before = pointer.read_bytes()
        # Simulates a stale process holding an old explicit location. It must
        # not replace the active pointer with a copy of the retained snapshot.
        stale = WorkerServer(old)
        response = json.loads(stale.process_line(json.dumps({
            "jsonrpc": "2.0", "protocol_version": "1.0", "id": "stale-relocation",
            "method": "relocate_library", "params": {"destinationParent": str(second_parent)},
        })))
        assert response["error"]["code"] == "LIBRARY_LOCATION_CHANGED"
        assert pointer.read_bytes() == pointer_before
        assert not list(second_parent.iterdir())
        assert rpc(active, "worker_info")["dataDir"] == result["dataDir"]
        assert rpc(active, "get_note", {"noteId": note["id"]})["note"]["content"] == "新位置保存了后续编辑。"
    finally:
        active._shutdown({})
        if stale is not None:
            stale._shutdown({})
