from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shiwei_ai.ingestion.chunker import approximate_tokens
from shiwei_ai.text_fidelity import normalize_numeric_ranges


@dataclass(frozen=True)
class CitationContext:
    citation_id: str
    document_id: str
    chunk_id: str
    source_filename: str
    source_path: str | None
    page_number: int | None
    heading_path: str | None
    snippet: str
    sheet_name: str | None = None
    slide_number: int | None = None
    stored_path: str | None = None
    imported_at: str | None = None
    source_type: str = "imported_file"
    note_id: str | None = None
    note_created_at: str | None = None
    note_updated_at: str | None = None
    mentioned_dates: tuple[str, ...] = ()
    source_id: str | None = None
    original_url: str | None = None
    final_url: str | None = None
    captured_at: str | None = None
    body_hash: str | None = None


@dataclass(frozen=True)
class BuiltContext:
    text: str
    citations: list[CitationContext]
    token_count: int


class ContextBuilder:
    def __init__(self, token_budget: int = 6000, max_chunks: int = 12) -> None:
        self.token_budget = token_budget
        self.max_chunks = max_chunks

    def build(self, hits: list[dict[str, Any]]) -> BuiltContext:
        blocks: list[str] = []
        citations: list[CitationContext] = []
        seen_chunks: set[str] = set()
        seen_content: set[tuple[str, tuple[str, ...], str]] = set()
        used_tokens = 0

        file_number = 0
        note_number = 0
        for hit in hits:
            if len(citations) >= self.max_chunks:
                break
            chunk_id = str(hit["chunkId"])
            content = str(hit.get("content", "")).strip()
            # Identical prose can describe different meetings. Deduplicate
            # repeated chunks only within the same document/date evidence.
            content_key = (
                str(hit["documentId"]),
                tuple(sorted(set(hit.get("mentionedDates") or []))),
                " ".join(content.casefold().split()),
            )
            if not content or chunk_id in seen_chunks or content_key in seen_content:
                continue

            block_tokens = approximate_tokens(content)
            if citations and used_tokens + block_tokens > self.token_budget:
                continue

            if hit.get("sourceType") == "user_note":
                note_number += 1
                citation_id = f"N{note_number}"
            else:
                file_number += 1
                citation_id = f"S{file_number}"
            filename = str(hit.get("filename") or hit.get("documentTitle") or "未知来源")
            heading = hit.get("headingPath")
            page_number = hit.get("pageNumber")
            header_parts = [f"source: {filename}"]
            if hit.get("mentionedDates"):
                header_parts.append("explicit date mentions (not inferred event dates): " + ", ".join(hit["mentionedDates"]))
            if page_number:
                header_parts.append(f"page: {page_number}")
            if hit.get("sheetName"):
                header_parts.append(f"sheet: {hit['sheetName']}")
            if hit.get("slideNumber"):
                header_parts.append(f"slide: {hit['slideNumber']}")
            if heading:
                header_parts.append(f"section: {heading}")
            blocks.append(f"[{citation_id}]\n" + "\n".join(header_parts) + f"\n\ncontent:\n{normalize_numeric_ranges(content)}")
            citations.append(
                CitationContext(
                    citation_id=citation_id,
                    document_id=str(hit["documentId"]),
                    chunk_id=chunk_id,
                    source_filename=filename,
                    source_path=hit.get("sourcePath"),
                    stored_path=hit.get("storedPath"),
                    imported_at=hit.get("importedAt"),
                    source_type=str(hit.get("sourceType") or "imported_file"),
                    source_id=hit.get("sourceId"),
                    original_url=hit.get("originalUrl"),
                    final_url=hit.get("finalUrl"),
                    captured_at=hit.get("capturedAt"),
                    body_hash=hit.get("bodyHash"),
                    note_id=hit.get("noteId"),
                    note_created_at=hit.get("noteCreatedAt"),
                    note_updated_at=hit.get("noteUpdatedAt"),
                    mentioned_dates=tuple(hit.get("mentionedDates") or []),
                    page_number=page_number,
                    sheet_name=hit.get("sheetName"),
                    slide_number=hit.get("slideNumber"),
                    heading_path=heading,
                    snippet=content[:500],
                )
            )
            used_tokens += block_tokens
            seen_chunks.add(chunk_id)
            seen_content.add(content_key)

        return BuiltContext(text="\n\n---\n\n".join(blocks), citations=citations, token_count=used_tokens)
