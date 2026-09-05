from __future__ import annotations

import re
from dataclasses import dataclass

from shiwei_ai.ingestion.canonical import CanonicalDocument, CanonicalSection


@dataclass(frozen=True)
class ChunkDraft:
    content: str
    chunk_index: int
    heading_path: str | None
    token_count: int
    page_number: int | None
    sheet_name: str | None
    slide_number: int | None
    section_index: int


def approximate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = len(text) - cjk
    return cjk + max(1, non_cjk // 4)


class StructureAwareChunker:
    def __init__(self, target_tokens: int = 500, overlap_tokens: int = 60) -> None:
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, document: CanonicalDocument) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        chunk_index = 0
        for section_index, section in enumerate(document.sections):
            content = section.plain_text().strip()
            if not content:
                continue
            for piece in self._split_section(content):
                drafts.append(
                    ChunkDraft(
                        content=piece,
                        chunk_index=chunk_index,
                        heading_path=" > ".join(section.heading_path) or None,
                        token_count=approximate_tokens(piece),
                        page_number=section.page_number,
                        sheet_name=section.sheet_name,
                        slide_number=section.slide_number,
                        section_index=section_index,
                    )
                )
                chunk_index += 1
        return drafts

    def _split_section(self, content: str) -> list[str]:
        if approximate_tokens(content) <= self.target_tokens:
            return [content]

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
        if len(paragraphs) == 1:
            paragraphs = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", content) if part.strip()]

        chunks: list[str] = []
        current: list[str] = []
        for paragraph in paragraphs:
            candidate = "\n\n".join([*current, paragraph])
            if current and approximate_tokens(candidate) > self.target_tokens:
                completed = "\n\n".join(current)
                chunks.append(completed)
                current = [self._tail_overlap(completed), paragraph]
            else:
                current.append(paragraph)
        if current:
            chunks.append("\n\n".join(part for part in current if part))
        return chunks

    def _tail_overlap(self, text: str) -> str:
        if self.overlap_tokens <= 0:
            return ""
        target_characters = self.overlap_tokens * 2
        return text[-target_characters:].lstrip()
