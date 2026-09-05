from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from shiwei_ai.ingestion.canonical import CanonicalBlock, CanonicalDocument, CanonicalSection
from shiwei_ai.ingestion.chunker import StructureAwareChunker
from shiwei_ai.search_projection import build_search_text, dates_json, insert_chunk_fts
from shiwei_ai.storage import Database
from shiwei_ai.storage.database import utc_now


class NoteService:
    """Owns note truth and its rebuildable document/FTS projection."""

    def __init__(self, database: Database, parsed_dir: Path) -> None:
        self.database = database
        self.parsed_dir = parsed_dir
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.chunker = StructureAwareChunker()

    def list(self, query: str = "") -> list[dict[str, Any]]:
        query = query.strip()
        if len(query) > 200:
            raise ValueError("笔记搜索不能超过 200 字")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.database.connection.execute(
            """
            SELECT id, source_id, title, content, created_at, updated_at
            FROM notes
            WHERE deleted_at IS NULL
              AND (? = '' OR title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
            ORDER BY updated_at DESC, rowid DESC
            LIMIT 2000
            """,
            (query, pattern, pattern),
        ).fetchall()
        return [self._serialize(row) for row in rows]

    def get(self, note_id: str) -> dict[str, Any]:
        row = self.database.connection.execute(
            """
            SELECT id, source_id, title, content, created_at, updated_at
            FROM notes WHERE id = ? AND deleted_at IS NULL
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            raise ValueError("没有找到这条笔记")
        return self._serialize(row)

    def create(self) -> dict[str, Any]:
        note_id = str(uuid4())
        source_id = str(uuid4())
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                  id, original_path, original_filename, stored_path, content_hash,
                  size, mime_type, created_at, imported_at, status, error, source_type
                ) VALUES (?, ?, ?, '', ?, 0, 'text/plain', ?, ?, 'processing', NULL, 'user_note')
                """,
                (
                    source_id,
                    f"note://{note_id}",
                    "无标题笔记",
                    hashlib.sha256(f"note:{note_id}".encode()).hexdigest(),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO notes(id, source_id, title, content, created_at, updated_at)
                VALUES (?, ?, '', '', ?, ?)
                """,
                (note_id, source_id, now, now),
            )
        return self.get(note_id)

    def update(self, note_id: str, title: str, content: str) -> dict[str, Any]:
        title = title.strip()
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if len(title) > 200:
            raise ValueError("笔记标题不能超过 200 字")
        if len(content) > 200_000:
            raise ValueError("单条笔记正文不能超过 20 万字")
        row = self.database.connection.execute(
            "SELECT id, source_id, created_at FROM notes WHERE id = ? AND deleted_at IS NULL",
            (note_id,),
        ).fetchone()
        if row is None:
            raise ValueError("没有找到这条笔记")

        source_id = str(row["source_id"])
        previous_document = self.database.connection.execute(
            "SELECT id FROM documents WHERE source_id = ?", (source_id,)
        ).fetchone()
        display_title = self.display_title(title, content)
        now = utc_now()
        document_id, chunk_count = self._replace_projection(
            note_id=note_id,
            source_id=source_id,
            raw_title=title,
            title=display_title,
            content=content,
            updated_at=now,
        )
        result = self.get(note_id)
        result.update(
            {
                "documentId": document_id,
                "previousDocumentId": previous_document["id"] if previous_document else None,
                "chunkCount": chunk_count,
            }
        )
        return result

    def delete(self, note_id: str) -> dict[str, Any]:
        row = self.database.connection.execute(
            """
            SELECT n.source_id, d.id AS document_id, d.canonical_path
            FROM notes n
            LEFT JOIN documents d ON d.source_id = n.source_id
            WHERE n.id = ? AND n.deleted_at IS NULL
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            raise ValueError("没有找到这条笔记")
        with self.database.transaction() as connection:
            if row["document_id"]:
                connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (row["document_id"],))
                connection.execute("DELETE FROM chunks_fts_trigram WHERE document_id = ?", (row["document_id"],))
            connection.execute(
                "DELETE FROM sources WHERE id = ? AND source_type = 'user_note'",
                (row["source_id"],),
            )
        if row["canonical_path"]:
            Path(row["canonical_path"]).unlink(missing_ok=True)
        return {
            "noteId": note_id,
            "sourceId": row["source_id"],
            "documentId": row["document_id"],
            "deleted": True,
        }

    def _replace_projection(
        self,
        *,
        note_id: str,
        source_id: str,
        raw_title: str,
        title: str,
        content: str,
        updated_at: str,
    ) -> tuple[str | None, int]:
        existing = self.database.connection.execute(
            "SELECT id, canonical_path FROM documents WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        document_id = str(existing["id"]) if existing else str(uuid4())
        canonical_path = self.parsed_dir / f"note-{source_id}.json"
        searchable_text = content.strip() or (title if title != "无标题笔记" else "")
        if not searchable_text:
            with self.database.transaction() as connection:
                if existing:
                    connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
                    connection.execute("DELETE FROM chunks_fts_trigram WHERE document_id = ?", (document_id,))
                    connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
                self._update_note_truth(
                    connection,
                    note_id=note_id,
                    source_id=source_id,
                    raw_title=raw_title,
                    display_title=title,
                    content=content,
                    updated_at=updated_at,
                    status="processing",
                )
            canonical_path.unlink(missing_ok=True)
            return None, 0

        document = CanonicalDocument(
            title=title,
            parser="user_note",
            parser_version="1",
            language="zh-CN",
            sections=[
                CanonicalSection(
                    heading=title,
                    heading_path=[title] if title != "无标题笔记" else [],
                    blocks=[CanonicalBlock(kind="paragraph", text=searchable_text)],
                )
            ],
        )
        drafts = self.chunker.chunk(document)
        document_search_text = build_search_text(title, content)
        temporary = canonical_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(canonical_path)

        with self.database.transaction() as connection:
            if existing:
                connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
                connection.execute("DELETE FROM chunks_fts_trigram WHERE document_id = ?", (document_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            connection.execute(
                """
                INSERT INTO documents(
                  id, source_id, title, parser, parser_version,
                  canonical_path, language, created_at, search_text, mentioned_dates
                ) VALUES (?, ?, ?, 'user_note', '1', ?, 'zh-CN', ?, ?, ?)
                """,
                (document_id, source_id, title, str(canonical_path), updated_at,
                 document_search_text, dates_json(document_search_text)),
            )
            section_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO sections(
                  id, document_id, parent_id, kind, heading, heading_path,
                  ordinal, page_number, sheet_name, slide_number, content
                ) VALUES (?, ?, NULL, 'section', ?, ?, 0, NULL, NULL, NULL, ?)
                """,
                (section_id, document_id, title, title if title != "无标题笔记" else None, searchable_text),
            )
            for draft in drafts:
                chunk_id = str(uuid4())
                chunk_search_text = build_search_text(title, draft.content)
                connection.execute(
                    """
                    INSERT INTO chunks(
                      id, document_id, section_id, content, chunk_index,
                      page_number, heading_path, token_count, created_at,
                      sheet_name, slide_number, search_text, mentioned_dates
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        section_id,
                        draft.content,
                        draft.chunk_index,
                        draft.heading_path,
                        draft.token_count,
                        updated_at,
                        chunk_search_text,
                        dates_json(chunk_search_text),
                    ),
                )
                insert_chunk_fts(
                    connection,
                    chunk_id=chunk_id, document_id=document_id,
                    search_text=chunk_search_text, title=title, filename=title,
                    headings=draft.heading_path or "",
                )
            self._update_note_truth(
                connection,
                note_id=note_id,
                source_id=source_id,
                raw_title=raw_title,
                display_title=title,
                content=content,
                updated_at=updated_at,
                status="searchable",
            )
        return document_id, len(drafts)

    @staticmethod
    def _update_note_truth(
        connection: Any,
        *,
        note_id: str,
        source_id: str,
        raw_title: str,
        display_title: str,
        content: str,
        updated_at: str,
        status: str,
    ) -> None:
        connection.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (raw_title, content, updated_at, note_id),
        )
        connection.execute(
            """
            UPDATE sources
            SET original_filename = ?, size = ?, status = ?, error = NULL
            WHERE id = ? AND source_type = 'user_note'
            """,
            (display_title, len(content.encode("utf-8")), status, source_id),
        )

    @staticmethod
    def display_title(title: str, content: str) -> str:
        if title.strip():
            return title.strip()
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        return first_line[:32] or "无标题笔记"

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "sourceId": row["source_id"],
            "title": row["title"],
            "content": row["content"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "displayTitle": NoteService.display_title(row["title"], row["content"]),
        }
