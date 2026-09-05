from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shiwei_ai.ingestion import Importer
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.notes.service import NoteService
from shiwei_ai.retrieval import EmbeddingIndexer, LanceVectorStore
from shiwei_ai.search_projection import SEARCH_TEXT_VERSION, build_search_text, extract_mentioned_dates
from shiwei_ai.storage.database import Database, MIGRATIONS
from shiwei_ai.worker import WorkerServer


def test_dates_are_explicit_valid_unique_calendar_dates() -> None:
    assert extract_mentioned_dates(
        "2025年9月3日会议，2025-09-03 复核；2024-2-29 可用。"
        "2025年2月29日、2025-13-01 无效；9月4日和下周一缺少年份。"
    ) == ["2024-02-29", "2025-09-03"]
    assert extract_mentioned_dates("20250903 12345-09-03 2025-09-033") == []


def test_note_projection_indexes_title_and_keeps_note_body_exactly(tmp_path: Path) -> None:
    database = Database(tmp_path / "notes.db")
    service = NoteService(database, tmp_path / "parsed")
    note = service.create()
    body = "  讨论 Oracle 至 TiDB 数据迁移。\n正文没有会议日期。  "
    service.update(note["id"], "2025年9月3日会议", body)
    connection = database.connection
    document = connection.execute("SELECT * FROM documents").fetchone()
    chunk = connection.execute("SELECT * FROM chunks").fetchone()
    assert service.get(note["id"])["content"] == body
    assert document["search_text"] == build_search_text("2025年9月3日会议", body)
    assert chunk["search_text"].startswith("2025年9月3日会议\n\n")
    assert json.loads(document["mentioned_dates"]) == ["2025-09-03"]
    assert json.loads(chunk["mentioned_dates"]) == ["2025-09-03"]
    assert "2025年9月3日" not in chunk["content"]
    assert connection.execute(
        "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH '会议'"
    ).fetchone()[0] == chunk["id"]
    assert connection.execute(
        "SELECT chunk_id FROM chunks_fts_trigram WHERE chunks_fts_trigram MATCH '2025年9月3日'"
    ).fetchone()[0] == chunk["id"]
    # Editing removes superseded metadata, without retaining old indexed dates.
    service.update(note["id"], "迁移会议", "确认日期为 2025-09-04。")
    document = connection.execute("SELECT * FROM documents").fetchone()
    assert json.loads(document["mentioned_dates"]) == ["2025-09-04"]
    assert "2025年9月3日" not in document["search_text"]
    service.delete(note["id"])
    assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
    database.close()


def test_import_projection_extracts_body_date_and_preserves_raw_file(tmp_path: Path) -> None:
    path = tmp_path / "项目日程.txt"
    body = "数据库迁移测试已约定在 2025-09-07，其他事件没有日期。"
    path.write_text(body, encoding="utf-8")
    importer = Importer(tmp_path / "data")
    importer.import_paths([str(path)])
    document = importer.database.connection.execute("SELECT * FROM documents").fetchone()
    chunk = importer.database.connection.execute("SELECT * FROM chunks").fetchone()
    assert path.read_text(encoding="utf-8") == body
    assert body in document["search_text"]
    assert document["title"] in chunk["search_text"]
    assert json.loads(document["mentioned_dates"]) == ["2025-09-07"]
    assert json.loads(chunk["mentioned_dates"]) == ["2025-09-07"]
    importer.close()


def create_v5_library(path: Path) -> None:
    connection = sqlite3.connect(path)
    for version, sql in MIGRATIONS[:5]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations VALUES (?, 'old')", (version,))
        connection.commit()
    connection.execute(
        "INSERT INTO sources(id, original_path, original_filename, stored_path, content_hash, size, imported_at, status, source_type) "
        "VALUES ('source', 'note://note', '2025年9月3日会议', '', 'hash', 24, 'old', 'searchable', 'user_note')"
    )
    connection.execute(
        "INSERT INTO notes VALUES ('note', 'source', '2025年9月3日会议', 'Oracle 至 TiDB 迁移。', 'old', 'old', NULL)"
    )
    connection.execute(
        "INSERT INTO documents(id,source_id,title,parser,parser_version,canonical_path,created_at) "
        "VALUES ('document', 'source', '2025年9月3日会议', 'user_note', '1', 'unchanged.json', 'old')"
    )
    connection.execute(
        "INSERT INTO chunks(id,document_id,content,chunk_index,token_count,created_at) "
        "VALUES ('chunk', 'document', 'Oracle 至 TiDB 迁移。', 0, 10, 'old')"
    )
    connection.execute(
        "INSERT INTO embedding_versions VALUES ('vectors-v0', 'fake', 'fake', 3, 'old', 1)"
    )
    connection.commit()
    connection.close()


def test_v5_upgrade_backfills_without_changing_truth_ids_or_old_vectors(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    create_v5_library(path)
    database = Database(path)
    connection = database.connection
    note = connection.execute("SELECT * FROM notes").fetchone()
    chunk = connection.execute("SELECT * FROM chunks").fetchone()
    assert note["content"] == chunk["content"] == "Oracle 至 TiDB 迁移。"
    assert (chunk["id"], chunk["document_id"]) == ("chunk", "document")
    assert chunk["search_text"] == "2025年9月3日会议\n\nOracle 至 TiDB 迁移。"
    assert json.loads(chunk["mentioned_dates"]) == ["2025-09-03"]
    assert connection.execute("SELECT canonical_path FROM documents").fetchone()[0] == "unchanged.json"
    assert connection.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH '会议'").fetchone()[0] == "chunk"
    assert tuple(connection.execute("SELECT active,search_text_version FROM embedding_versions").fetchone()) == (1, 0)
    database.close()
    reopened = Database(path)
    assert reopened.connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 1
    reopened.close()


def test_failed_projection_migration_rolls_back_schema_and_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "library.db"
    create_v5_library(path)

    def fail_backfill(connection):
        connection.execute("UPDATE chunks SET search_text = 'partial'")
        raise RuntimeError("interrupted backfill")

    monkeypatch.setattr("shiwei_ai.search_projection.backfill_search_projections", fail_backfill)
    with pytest.raises(RuntimeError, match="interrupted backfill"):
        Database(path)
    connection = sqlite3.connect(path)
    assert "search_text" not in [row[1] for row in connection.execute("PRAGMA table_info(chunks)")]
    assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 5
    assert connection.execute("SELECT content FROM notes").fetchone()[0] == "Oracle 至 TiDB 迁移。"
    connection.close()


class RecordingEmbeddingGateway(ModelGateway):
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def chat(self, messages, **options):
        raise AssertionError("No answer generation is required for projection tests")

    def stream_chat(self, messages, **options):
        raise AssertionError("No answer generation is required for projection tests")

    def embed(self, texts):
        self.inputs.extend(texts)
        return EmbeddingResult(vectors=[[1.0, 0.5, 0.2] for _ in texts], model="recording", dimension=3)


def test_embedding_uses_search_text_and_marks_version(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "shiwei.db")
    notes = NoteService(database, tmp_path / "parsed")
    note = notes.create()
    notes.update(note["id"], "2025年9月3日会议", "Oracle 至 TiDB 迁移。")
    gateway = RecordingEmbeddingGateway()
    store = LanceVectorStore(tmp_path / "index")
    report = EmbeddingIndexer(database, store, gateway).rebuild()
    assert report["searchTextVersion"] == SEARCH_TEXT_VERSION
    assert gateway.inputs == ["2025年9月3日会议\n\nOracle 至 TiDB 迁移。"]
    assert database.connection.execute("SELECT search_text_version FROM embedding_versions WHERE active=1").fetchone()[0] == SEARCH_TEXT_VERSION
    hit = store.search([1.0, 0.5, 0.2])[0]
    assert hit["similarityMetric"] == "cosine"
    assert hit["semanticScore"] == pytest.approx(1.0, abs=1e-6)
    assert hit["semanticDistance"] == pytest.approx(0.0, abs=1e-6)
    database.close()


def test_stale_embedding_is_disabled_without_upload_on_retrieval(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    create_v5_library(data / "shiwei.db")
    gateway = RecordingEmbeddingGateway()
    server = WorkerServer(data)
    server._gateway = gateway
    server._provider_public = {"baseUrl": "fake", "embeddingModel": "fake", "embeddingMode": "same"}
    retriever = server._retriever()
    assert retriever.gateway is None
    assert gateway.inputs == []
    assert server._index_status({})["needsRebuild"] is True
    server._get_importer().close()
