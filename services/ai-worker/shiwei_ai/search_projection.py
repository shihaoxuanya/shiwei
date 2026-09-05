"""Deterministic, rebuildable retrieval text; source and note text stay untouched."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

import jieba

SEARCH_TEXT_VERSION = 1
_EXPLICIT_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?:年(?P<zh_month>\d{1,2})月(?P<zh_day>\d{1,2})日"
    r"|-(?P<month>\d{1,2})-(?P<day>\d{1,2}))(?!\d)"
)


def build_search_text(title: str, content: str) -> str:
    """One contract for embedding and FTS: title followed by unmodified body."""
    return "\n\n".join(part for part in (title.strip(), content) if part)


def extract_mentioned_dates(text: str) -> list[str]:
    """Only explicit, valid calendar dates; never infer a year or event date."""
    values: set[str] = set()
    for match in _EXPLICIT_DATE.finditer(text):
        try:
            value = date(
                int(match.group("year")),
                int(match.group("zh_month") or match.group("month")),
                int(match.group("zh_day") or match.group("day")),
            )
        except ValueError:
            continue
        values.add(value.isoformat())
    return sorted(values)


def dates_json(text: str) -> str:
    return json.dumps(extract_mentioned_dates(text), ensure_ascii=False)


def fts_text(text: str) -> str:
    # Search-mode segmentation preserves both long Chinese words and subwords.
    # No stop-word removal: retrieval decides which query terms carry meaning.
    return " ".join(word for word in jieba.cut_for_search(text, HMM=False) if word.strip())


def insert_chunk_fts(
    connection: sqlite3.Connection,
    *,
    chunk_id: str,
    document_id: str,
    search_text: str,
    title: str,
    filename: str,
    headings: str,
) -> None:
    fields = (search_text, title, filename, headings)
    for table, values in (
        ("chunks_fts", tuple(fts_text(field) for field in fields)),
        ("chunks_fts_trigram", fields),
    ):
        connection.execute(
            f"INSERT INTO {table}(chunk_id, document_id, chunk_content, document_title, filename, headings) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, document_id, *values),
        )


def backfill_search_projections(connection: sqlite3.Connection) -> None:
    """Called inside migration 6's transaction; preserve document/chunk IDs."""
    documents = connection.execute(
        "SELECT d.id, d.title, n.content AS note_content FROM documents d "
        "LEFT JOIN notes n ON n.source_id = d.source_id"
    ).fetchall()
    for document in documents:
        if document["note_content"] is not None:
            content = document["note_content"]
        else:
            sections = connection.execute(
                "SELECT content FROM sections WHERE document_id = ? ORDER BY ordinal",
                (document["id"],),
            ).fetchall()
            if not sections:
                sections = connection.execute(
                    "SELECT content FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                    (document["id"],),
                ).fetchall()
            content = "\n\n".join(section["content"] for section in sections)
        search_text = build_search_text(document["title"], content)
        connection.execute(
            "UPDATE documents SET search_text = ?, mentioned_dates = ? WHERE id = ?",
            (search_text, dates_json(search_text), document["id"]),
        )

    connection.execute("DELETE FROM chunks_fts")
    connection.execute("DELETE FROM chunks_fts_trigram")
    chunks = connection.execute(
        "SELECT c.id, c.document_id, c.content, c.heading_path, d.title, s.original_filename "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "JOIN sources s ON s.id = d.source_id ORDER BY c.document_id, c.chunk_index"
    )
    for chunk in chunks:
        search_text = build_search_text(chunk["title"], chunk["content"])
        connection.execute(
            "UPDATE chunks SET search_text = ?, mentioned_dates = ? WHERE id = ?",
            (search_text, dates_json(search_text), chunk["id"]),
        )
        insert_chunk_fts(
            connection,
            chunk_id=chunk["id"], document_id=chunk["document_id"],
            search_text=search_text, title=chunk["title"],
            filename=chunk["original_filename"], headings=chunk["heading_path"] or "",
        )
