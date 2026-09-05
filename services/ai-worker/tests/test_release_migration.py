"""Frozen 0.2.2 schema, synthetic user assets only. No production library access."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from shiwei_ai.storage import database as storage
from shiwei_ai.storage.database import Database


def old_library(tmp_path, release="0.2.2"):
    path = tmp_path / "shiwei.db"
    connection = sqlite3.connect(path)
    fixture = Path(__file__).parent / "fixtures"
    schema_file = "schema-0.2.2.json"
    if release == "0.3.0":
        manifest = json.loads((fixture / "release-0.3.0.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 6 and manifest["app_version"] == release
        schema_file = manifest["schema_fixture"]
    migrations = json.loads((fixture / schema_file).read_text(encoding="utf-8"))
    for version, sql in migrations:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations VALUES (?, '2026-09-05')", (version,))
        connection.commit()
    raw = tmp_path / "raw" / "synthetic.txt"
    raw.parent.mkdir()
    raw.write_text("这是升级前的原始文件，不能被更新程序修改。", encoding="utf-8")
    canonical = tmp_path / "parsed" / "note.json"
    canonical.parent.mkdir()
    canonical.write_text('{"title":"会议","body":"38~50天"}', encoding="utf-8")
    connection.execute("INSERT INTO sources(id,original_path,original_filename,stored_path,content_hash,size,imported_at,status,source_type) VALUES ('s','old','旧会议',?,'hash',123,'old','searchable','user_note')", (str(raw),))
    connection.execute("INSERT INTO notes(id,source_id,title,content,created_at,updated_at) VALUES ('n','s','2025年9月3日会议','单次迁移38~50天','old','old')")
    connection.execute("INSERT INTO documents(id,source_id,title,parser,parser_version,canonical_path,created_at,search_text,mentioned_dates) VALUES ('d','s','会议','user_note','1',?,'old','会议 38~50天','[\"2025-09-03\"]')", (str(canonical),))
    connection.execute("INSERT INTO chunks(id,document_id,content,chunk_index,token_count,created_at,search_text) VALUES ('c','d','38~50天',0,10,'old','会议 38~50天')")
    connection.execute("INSERT INTO chunks_fts VALUES ('c','d','会议 38~50天','会议','会议','')")
    connection.execute("INSERT INTO conversations VALUES ('chat','旧对话','old','old')")
    connection.execute("INSERT INTO messages(id,conversation_id,role,content,created_at,answer_kind) VALUES ('m','chat','assistant','单次38～50天[N1]','old','knowledge')")
    connection.execute("INSERT INTO citations(id,message_id,citation_id,document_id,chunk_id,source_filename,snippet) VALUES ('ref','m','N1','d','c','会议','38~50天')")
    connection.execute("INSERT INTO embedding_versions(id,provider,model,dimension,created_at,active,search_text_version) VALUES ('e','test','test',3,'old',1,1)")
    connection.commit()
    connection.close()
    return path, raw, canonical


@pytest.mark.parametrize("release", ["0.2.2", "0.3.0"])
def test_previous_release_upgrade_preserves_assets_notes_conversations_citations_and_indexes(tmp_path, release):
    path, raw, canonical = old_library(tmp_path, release)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in [raw, canonical]]
    for _ in range(2):
        database = Database(path)
        c = database.connection
        assert c.execute("SELECT content FROM notes WHERE id='n'").fetchone()[0] == "单次迁移38~50天"
        assert c.execute("SELECT content FROM messages WHERE id='m'").fetchone()[0] == "单次38～50天[N1]"
        assert c.execute("SELECT chunk_id FROM citations WHERE id='ref'").fetchone()[0] == 'c'
        assert c.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH '会议'").fetchone()[0] == 'c'
        assert c.execute("SELECT active FROM embedding_versions WHERE id='e'").fetchone()[0] == 1
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        database.close()
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in [raw, canonical]] == before


def test_failed_new_migration_rolls_back_and_keeps_original_data(tmp_path, monkeypatch):
    path, _, _ = old_library(tmp_path)
    monkeypatch.setattr(storage, 'MIGRATIONS', storage.MIGRATIONS + ((7, 'ALTER TABLE notes ADD COLUMN temporary_field TEXT; INSERT INTO nonexistent VALUES (1);'),))
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    c = sqlite3.connect(path)
    assert 'temporary_field' not in [r[1] for r in c.execute('PRAGMA table_info(notes)')]
    assert c.execute("SELECT content FROM notes").fetchone()[0] == '单次迁移38~50天'
    assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 6
    c.close()


def test_older_app_refuses_newer_database_without_migrating(tmp_path):
    path, _, _ = old_library(tmp_path)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO schema_migrations VALUES (999, 'future')")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match='较新版本'):
        Database(path)
    c = sqlite3.connect(path)
    assert c.execute("SELECT content FROM notes").fetchone()[0] == '单次迁移38~50天'
    c.close()
