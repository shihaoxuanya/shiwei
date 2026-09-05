from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        );

        CREATE TABLE sources (
          id TEXT PRIMARY KEY,
          original_path TEXT NOT NULL,
          original_filename TEXT NOT NULL,
          stored_path TEXT NOT NULL,
          content_hash TEXT NOT NULL UNIQUE,
          size INTEGER NOT NULL,
          mime_type TEXT,
          created_at TEXT,
          imported_at TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT
        );
        CREATE INDEX idx_sources_imported_at ON sources(imported_at DESC);
        CREATE INDEX idx_sources_filename ON sources(original_filename);

        CREATE TABLE documents (
          id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          parser TEXT NOT NULL,
          parser_version TEXT NOT NULL,
          canonical_path TEXT NOT NULL,
          language TEXT,
          created_at TEXT NOT NULL
        );

        CREATE TABLE sections (
          id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          parent_id TEXT REFERENCES sections(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,
          heading TEXT,
          heading_path TEXT,
          ordinal INTEGER NOT NULL,
          page_number INTEGER,
          sheet_name TEXT,
          slide_number INTEGER,
          content TEXT NOT NULL
        );
        CREATE INDEX idx_sections_document ON sections(document_id, ordinal);

        CREATE TABLE chunks (
          id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          section_id TEXT REFERENCES sections(id) ON DELETE SET NULL,
          content TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          page_number INTEGER,
          heading_path TEXT,
          token_count INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(document_id, chunk_index)
        );
        CREATE INDEX idx_chunks_document ON chunks(document_id, chunk_index);

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
          chunk_id UNINDEXED,
          document_id UNINDEXED,
          chunk_content,
          document_title,
          filename,
          headings,
          tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TABLE jobs (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          current_step TEXT,
          payload TEXT,
          error TEXT,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT
        );
        CREATE INDEX idx_jobs_created ON jobs(created_at DESC);

        CREATE TABLE conversations (
          id TEXT PRIMARY KEY,
          title TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL,
          error TEXT
        );

        CREATE TABLE citations (
          id TEXT PRIMARY KEY,
          message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          citation_id TEXT NOT NULL,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
          source_filename TEXT NOT NULL,
          page_number INTEGER,
          heading_path TEXT,
          snippet TEXT NOT NULL
        );

        CREATE TABLE settings (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE embedding_versions (
          id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          dimension INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 0
        );
        """,
    ),
    (
        2,
        """
        CREATE VIRTUAL TABLE chunks_fts_trigram USING fts5(
          chunk_id UNINDEXED,
          document_id UNINDEXED,
          chunk_content,
          document_title,
          filename,
          headings,
          tokenize='trigram'
        );
        """,
    ),
    (
        3,
        """
        ALTER TABLE chunks ADD COLUMN sheet_name TEXT;
        ALTER TABLE chunks ADD COLUMN slide_number INTEGER;
        ALTER TABLE citations ADD COLUMN sheet_name TEXT;
        ALTER TABLE citations ADD COLUMN slide_number INTEGER;
        """,
    ),
    (
        4,
        """
        ALTER TABLE messages ADD COLUMN answer_kind TEXT;
        ALTER TABLE messages ADD COLUMN mode TEXT;
        ALTER TABLE messages ADD COLUMN notice TEXT;
        CREATE TABLE message_sources (
          message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL,
          PRIMARY KEY(message_id, source_id)
        );
        CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
        """,
    ),
    (
        5,
        """
        ALTER TABLE sources ADD COLUMN source_type TEXT NOT NULL DEFAULT 'imported_file';
        CREATE INDEX idx_sources_type_imported ON sources(source_type, imported_at DESC);

        CREATE TABLE notes (
          id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
          title TEXT NOT NULL DEFAULT '',
          content TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          deleted_at TEXT
        );
        CREATE INDEX idx_notes_updated_at ON notes(updated_at DESC);
        """,
    ),
    (
        6,
        """
        ALTER TABLE documents ADD COLUMN search_text TEXT NOT NULL DEFAULT '';
        ALTER TABLE documents ADD COLUMN mentioned_dates TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE chunks ADD COLUMN search_text TEXT NOT NULL DEFAULT '';
        ALTER TABLE chunks ADD COLUMN mentioned_dates TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE embedding_versions ADD COLUMN search_text_version INTEGER NOT NULL DEFAULT 0;
        """,
    ),
)


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        try:
            self._migrate()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            int(row["version"])
            for row in self.connection.execute("SELECT version FROM schema_migrations")
        }
        if applied and max(applied) > max(item[0] for item in MIGRATIONS):
            raise RuntimeError("资料库由较新版本的拾微创建，请使用相同或更新版本；不会降级或修改你的资料。")
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self.transaction() as connection:
                # executescript otherwise commits DDL before the migration marker.
                # Explicit BEGIN makes a failed/interrupted upgrade rollback as one unit.
                connection.executescript("BEGIN IMMEDIATE;\n" + sql)
                if version == 6:
                    from shiwei_ai.search_projection import backfill_search_projections

                    backfill_search_projections(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )

    def recover_interrupted_jobs(self) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = '应用上次退出时任务仍在运行，请重试',
                    finished_at = ?
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
        return cursor.rowcount

    def create_job(self, job_id: str, job_type: str, payload: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs(id, type, status, progress, current_step, payload, created_at)
                VALUES (?, ?, 'pending', 0, '等待开始', ?, ?)
                """,
                (job_id, job_type, json.dumps(payload, ensure_ascii=False), utc_now()),
            )

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        progress: float,
        current_step: str,
        error: str | None = None,
    ) -> None:
        started_at = utc_now() if status == "running" else None
        finished_at = utc_now() if status in {"success", "failed", "cancelled"} else None
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, current_step = ?, error = ?,
                    started_at = COALESCE(started_at, ?), finished_at = ?
                WHERE id = ?
                """,
                (status, progress, current_step, error, started_at, finished_at, job_id),
            )

    def source_by_hash(self, content_hash: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sources WHERE content_hash = ?", (content_hash,)
        ).fetchone()

    def insert_source(self, values: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                  id, original_path, original_filename, stored_path, content_hash,
                  size, mime_type, created_at, imported_at, status, error
                ) VALUES (
                  :id, :original_path, :original_filename, :stored_path, :content_hash,
                  :size, :mime_type, :created_at, :imported_at, :status, :error
                )
                """,
                values,
            )

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, original_path, original_filename, stored_path, content_hash,
                   size, mime_type, imported_at, status, error, source_type
            FROM sources
            WHERE source_type = 'imported_file'
            ORDER BY imported_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def update_source_status(self, source_id: str, status: str, error: str | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE sources SET status = ?, error = ? WHERE id = ?",
                (status, error, source_id),
            )
