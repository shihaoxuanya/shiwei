from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from shiwei_ai.ingestion.canonical import CanonicalDocument
from shiwei_ai.ingestion.chunker import StructureAwareChunker
from shiwei_ai.ingestion.parser import DocumentParser
from shiwei_ai.storage.database import Database, utc_now
from shiwei_ai.search_terms import search_terms, like_pattern
from shiwei_ai.search_projection import build_search_text, dates_json, insert_chunk_fts


class DocumentIndexer:
    def __init__(self, database: Database, parsed_dir: Path) -> None:
        self.database = database
        self.parsed_dir = parsed_dir
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.parser = DocumentParser()
        self.chunker = StructureAwareChunker()

    def index_source(self, source_id: str, stored_path: Path, filename: str, *, document: CanonicalDocument | None = None, progress_sink=None) -> dict[str, Any]:
        try:
            if document is None:
                document = self.parser.parse(stored_path, filename, progress_sink=progress_sink) if progress_sink is not None else self.parser.parse(stored_path, filename)
            drafts = self.chunker.chunk(document)
            if not drafts:
                raise ValueError("文档中没有可建立索引的正文")
            canonical_path = self._write_canonical(source_id, document)
            document_id = str(uuid4())
            document_search_text = build_search_text(
                document.title, "\n\n".join(section.plain_text() for section in document.sections)
            )

            with self.database.transaction() as connection:
                source = connection.execute("SELECT source_type FROM sources WHERE id=?", (source_id,)).fetchone()
                is_snapshot = source is not None and source["source_type"] == "web_page"
                old_chunks = {}
                saved_citations = []
                existing = connection.execute(
                    "SELECT id FROM documents WHERE source_id = ?", (source_id,)
                ).fetchone()
                if existing is not None:
                    if is_snapshot:
                        # Immutable snapshots retain reference identities across local reindex.
                        document_id = existing["id"]
                        old_chunks = {(row["chunk_index"], row["content"]): row["id"] for row in connection.execute("SELECT id,chunk_index,content FROM chunks WHERE document_id=?", (document_id,))}
                        saved_citations = [dict(row) for row in connection.execute("SELECT * FROM citations WHERE document_id=?", (document_id,))]
                    connection.execute(
                        "DELETE FROM chunks_fts WHERE document_id = ?", (existing["id"],)
                    )
                    connection.execute(
                        "DELETE FROM chunks_fts_trigram WHERE document_id = ?",
                        (existing["id"],),
                    )
                    connection.execute("DELETE FROM documents WHERE id = ?", (existing["id"],))

                connection.execute(
                    """
                    INSERT INTO documents(
                      id, source_id, title, parser, parser_version,
                      canonical_path, language, created_at, search_text, mentioned_dates
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        source_id,
                        document.title,
                        document.parser,
                        document.parser_version,
                        str(canonical_path),
                        document.language,
                        utc_now(),
                        document_search_text,
                        dates_json(document_search_text),
                    ),
                )

                section_ids: list[str] = []
                for ordinal, section in enumerate(document.sections):
                    section_id = str(uuid4())
                    section_ids.append(section_id)
                    connection.execute(
                        """
                        INSERT INTO sections(
                          id, document_id, parent_id, kind, heading, heading_path,
                          ordinal, page_number, sheet_name, slide_number, content
                        ) VALUES (?, ?, NULL, 'section', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            section_id,
                            document_id,
                            section.heading,
                            " > ".join(section.heading_path) or None,
                            ordinal,
                            section.page_number,
                            section.sheet_name,
                            section.slide_number,
                            section.plain_text(),
                        ),
                    )

                for draft in drafts:
                    chunk_id = old_chunks.get((draft.chunk_index, draft.content), str(uuid4()))
                    section_id = section_ids[draft.section_index]
                    chunk_search_text = build_search_text(document.title, draft.content)
                    values = (
                        chunk_id,
                        document_id,
                        section_id,
                        draft.content,
                        draft.chunk_index,
                        draft.page_number,
                        draft.heading_path,
                        draft.token_count,
                        utc_now(),
                    )
                    connection.execute(
                        """
                        INSERT INTO chunks(
                          id, document_id, section_id, content, chunk_index,
                          page_number, heading_path, token_count, created_at,
                          sheet_name, slide_number, search_text, mentioned_dates
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*values, draft.sheet_name, draft.slide_number, chunk_search_text,
                         dates_json(chunk_search_text)),
                    )
                    insert_chunk_fts(
                        connection,
                        chunk_id=chunk_id, document_id=document_id,
                        search_text=chunk_search_text, title=document.title,
                        filename=filename, headings=draft.heading_path or "",
                    )
                for citation in saved_citations:
                    if connection.execute("SELECT 1 FROM chunks WHERE id=?", (citation["chunk_id"],)).fetchone():
                        columns = list(citation)
                        connection.execute(f"INSERT INTO citations({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [citation[key] for key in columns])
                connection.execute(
                    "UPDATE sources SET status = 'searchable', error = NULL WHERE id = ?",
                    (source_id,),
                )

            return {"documentId": document_id, "chunkCount": len(drafts)}
        except Exception as error:
            self.database.update_source_status(source_id, "failed", str(error))
            raise

    def _write_canonical(self, source_id: str, document: CanonicalDocument) -> Path:
        destination = self.parsed_dir / f"{source_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination


class LexicalSearch:
    def __init__(self, database: Database) -> None:
        self.database = database

    def search(self, query: str, limit: int = 20, *, source_type: str | None = None) -> list[dict[str, Any]]:
        if source_type not in {None, "imported_file", "user_note", "web_page"}:
            raise ValueError("无效的资料类型")
        normalized = query.strip()
        terms = search_terms(normalized)
        if not terms:
            return []

        hits: dict[str, dict[str, Any]] = {}
        for table, match_query in (
            ("chunks_fts", self._unicode_query(" ".join(terms))),
            ("chunks_fts_trigram", self._unicode_query(" ".join(t for t in terms if len(t) >= 3))),
        ):
            source_filter = (
                f"AND EXISTS (SELECT 1 FROM documents scope_doc JOIN sources scope_source "
                f"ON scope_source.id=scope_doc.source_id WHERE scope_doc.id={table}.document_id "
                "AND scope_source.source_type=?)"
            ) if source_type is not None else ""
            try:
                rows = self.database.connection.execute(
                    f"""
                    SELECT chunk_id, document_id, chunk_content, document_title,
                           filename, headings, bm25({table}) AS rank
                    FROM {table}
                    WHERE {table} MATCH ? {source_filter}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match_query, *([source_type] if source_type is not None else []), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

            for position, row in enumerate(rows, start=1):
                chunk_id = row["chunk_id"]
                current = hits.get(chunk_id)
                score = 1.0 / (60 + position)
                if current is None:
                    hits[chunk_id] = {
                        "chunkId": chunk_id,
                        "documentId": row["document_id"],
                        "content": row["chunk_content"],
                        "documentTitle": row["document_title"],
                        "filename": row["filename"],
                        "headingPath": row["headings"] or None,
                        "lexicalScore": score,
                        "matchedBy": [table],
                    }
                else:
                    current["lexicalScore"] += score
                    current["matchedBy"].append(table)

        # Old indexes remain valid: substring metadata queries also cover two-character Chinese.
        conditions = " OR ".join("(s.original_filename LIKE ? ESCAPE '\\' OR d.title LIKE ? ESCAPE '\\')" for _ in terms)
        patterns = [value for term in terms for value in (like_pattern(term), like_pattern(term))]
        source_filter = "AND s.source_type=?" if source_type is not None else ""
        metadata_rows = self.database.connection.execute(
            f"SELECT c.id AS chunk_id, c.document_id, c.content AS chunk_content, d.title AS document_title, s.original_filename AS filename, c.heading_path AS headings FROM sources s JOIN documents d ON d.source_id=s.id JOIN chunks c ON c.document_id=d.id WHERE ({conditions}) {source_filter} ORDER BY c.chunk_index LIMIT 128",
            [*patterns, *([source_type] if source_type is not None else [])],
        ).fetchall()
        for row in metadata_rows:
            filename = row["filename"].lower()
            stem = Path(filename).stem
            score = 0.3 if filename in normalized.lower() else 0.2 if stem in normalized.lower() else 0.1
            hits[row["chunk_id"]] = {"chunkId": row["chunk_id"], "documentId": row["document_id"], "content": row["chunk_content"], "documentTitle": row["document_title"], "filename": row["filename"], "headingPath": row["headings"], "lexicalScore": score, "matchedBy": ["metadata"]}
        if not hits:
            # Bounded fallback for legacy unicode/trigram indexes, only on an FTS miss.
            conditions = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in terms)
            candidates = (
                "SELECT c.id, c.document_id, substr(c.content,1,12000) AS content, c.heading_path "
                "FROM chunks c JOIN documents sd ON sd.id=c.document_id JOIN sources ss ON ss.id=sd.source_id "
                "WHERE ss.source_type=? ORDER BY c.rowid DESC LIMIT 50000"
            ) if source_type is not None else "SELECT id, document_id, substr(content,1,12000) AS content, heading_path FROM chunks ORDER BY rowid DESC LIMIT 50000"
            rows = self.database.connection.execute(
                f"WITH candidates AS MATERIALIZED ({candidates}) SELECT c.id AS chunk_id, c.document_id, c.content, c.heading_path, d.title, s.original_filename FROM candidates c JOIN documents d ON d.id=c.document_id JOIN sources s ON s.id=d.source_id WHERE ({conditions}) LIMIT ?",
                (*([source_type] if source_type is not None else []), *[like_pattern(t) for t in terms], limit),
            ).fetchall()
            for row in rows:
                hits[row["chunk_id"]] = {"chunkId": row["chunk_id"], "documentId": row["document_id"], "content": row["content"], "documentTitle": row["title"], "filename": row["original_filename"], "headingPath": row["heading_path"], "lexicalScore": 0.01, "matchedBy": ["bounded_substring"]}
        ranked = sorted(hits.values(), key=lambda item: item["lexicalScore"], reverse=True)[:limit]
        for hit in ranked:
            metadata = self.database.connection.execute(
                """
                SELECT c.page_number, c.sheet_name, c.slide_number, c.heading_path,
                       c.content, c.search_text, c.mentioned_dates, d.title,
                       s.original_filename,
                       s.original_path, s.stored_path,
                       s.imported_at, s.id AS source_id, s.source_type,
                       s.original_url, s.final_url, s.captured_at, s.body_hash,
                       n.id AS note_id, n.created_at AS note_created_at,
                       n.updated_at AS note_updated_at
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                JOIN sources s ON s.id = d.source_id
                LEFT JOIN notes n ON n.source_id = s.id
                WHERE c.id = ?
                """,
                (hit["chunkId"],),
            ).fetchone()
            if metadata is not None:
                # FTS stores tokenized derived search text, never source snippets.
                hit["content"] = metadata["content"]
                hit["documentTitle"] = metadata["title"]
                hit["filename"] = metadata["original_filename"]
                hit["searchText"] = metadata["search_text"]
                hit["mentionedDates"] = json.loads(metadata["mentioned_dates"] or "[]")
                hit["pageNumber"] = metadata["page_number"]
                hit["sheetName"] = metadata["sheet_name"]
                hit["slideNumber"] = metadata["slide_number"]
                hit["headingPath"] = metadata["heading_path"] or hit["headingPath"]
                hit["sourcePath"] = metadata["original_path"]
                hit["storedPath"] = metadata["stored_path"]
                hit["importedAt"] = metadata["imported_at"]
                hit["sourceId"] = metadata["source_id"]
                hit["sourceType"] = metadata["source_type"]
                hit["originalUrl"] = metadata["original_url"]
                hit["finalUrl"] = metadata["final_url"]
                hit["capturedAt"] = metadata["captured_at"]
                hit["bodyHash"] = metadata["body_hash"]
                hit["noteId"] = metadata["note_id"]
                hit["noteCreatedAt"] = metadata["note_created_at"]
                hit["noteUpdatedAt"] = metadata["note_updated_at"]
        return ranked

    @staticmethod
    def _quoted(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'

    def _unicode_query(self, value: str) -> str:
        terms = [term for term in value.split() if term]
        if len(terms) <= 1:
            return self._quoted(value)
        return " OR ".join(self._quoted(term) for term in terms)
