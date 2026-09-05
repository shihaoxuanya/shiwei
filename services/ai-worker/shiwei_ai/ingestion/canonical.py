from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CanonicalBlock(BaseModel):
    kind: Literal["paragraph", "heading", "list", "table", "code", "image_text"]
    text: str
    page_number: int | None = None
    sheet_name: str | None = None
    slide_number: int | None = None
    source_offset: int | None = None


class CanonicalSection(BaseModel):
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    level: int = 1
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    page_number: int | None = None
    sheet_name: str | None = None
    slide_number: int | None = None

    def plain_text(self) -> str:
        return "\n\n".join(block.text.strip() for block in self.blocks if block.text.strip())


class CanonicalDocument(BaseModel):
    title: str
    parser: str
    parser_version: str
    language: str | None = None
    sections: list[CanonicalSection]
