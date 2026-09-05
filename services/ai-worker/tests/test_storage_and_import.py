from __future__ import annotations

from pathlib import Path

from shiwei_ai.ingestion import Importer
from shiwei_ai.storage import Database, RawStore


def test_sha256_and_raw_store_deduplicate_content(tmp_path: Path) -> None:
    source = tmp_path / "oracle.md"
    source.write_text("ORA-01034", encoding="utf-8")
    store = RawStore(tmp_path / "data" / "raw")

    content_hash = store.sha256(source)
    first = store.copy(source, content_hash)
    second = store.copy(source, content_hash)

    assert len(content_hash) == 64
    assert first == second
    assert first.read_text(encoding="utf-8") == "ORA-01034"


def test_import_folder_records_sources_and_skips_duplicates(tmp_path: Path) -> None:
    source_dir = tmp_path / "knowledge"
    source_dir.mkdir()
    (source_dir / "oracle.md").write_text("PDB remained MOUNTED", encoding="utf-8")
    (source_dir / "meeting.txt").write_text("迁移窗口 01:00", encoding="utf-8")
    (source_dir / "ignored.exe").write_bytes(b"not-supported")
    importer = Importer(tmp_path / "app-data")

    first = importer.import_paths([str(source_dir)])
    second = importer.import_paths([str(source_dir)])
    sources = importer.list_sources()
    importer.close()

    assert first["summary"] == {"imported": 2, "skipped": 0, "failed": 0}
    assert second["summary"] == {"imported": 0, "skipped": 2, "failed": 0}
    assert {source["filename"] for source in sources} == {"oracle.md", "meeting.txt"}
    assert all(Path(source["storedPath"]).is_file() for source in sources)


def test_database_enables_wal_foreign_keys_and_versions_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "shiwei.db")

    journal_mode = database.connection.execute("PRAGMA journal_mode").fetchone()[0]
    foreign_keys = database.connection.execute("PRAGMA foreign_keys").fetchone()[0]
    versions = database.connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    database.close()

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1
    assert [row[0] for row in versions] == [1, 2, 3, 4, 5, 6]


def test_interrupted_running_jobs_are_marked_failed(tmp_path: Path) -> None:
    database = Database(tmp_path / "shiwei.db")
    database.create_job("job-1", "IMPORT", {"paths": []})
    database.update_job(
        "job-1", status="running", progress=0.4, current_step="正在保存资料"
    )

    recovered = database.recover_interrupted_jobs()
    row = database.connection.execute(
        "SELECT status, error FROM jobs WHERE id = 'job-1'"
    ).fetchone()
    database.close()

    assert recovered == 1
    assert row["status"] == "failed"
    assert "上次退出" in row["error"]


def test_delete_source_removes_database_rows_indexes_and_managed_copy(tmp_path: Path) -> None:
    note = tmp_path / "oracle.md"
    note.write_text("ORA-01034 与 PDB 恢复记录", encoding="utf-8")
    importer = Importer(tmp_path / "app-data")
    report = importer.import_paths([str(note)])
    source_id = report["imported"][0]["sourceId"]
    stored_path = Path(importer.list_sources()[0]["storedPath"])

    importer.delete_source(source_id)

    assert importer.list_sources() == []
    assert not stored_path.exists()
    assert importer.database.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert importer.database.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert importer.database.connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
    importer.close()
