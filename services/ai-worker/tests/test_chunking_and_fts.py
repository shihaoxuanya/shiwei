from __future__ import annotations

from pathlib import Path

from shiwei_ai.ingestion.canonical import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
)
from shiwei_ai.ingestion.chunker import StructureAwareChunker
from shiwei_ai.ingestion.indexer import LexicalSearch
from shiwei_ai.ingestion import Importer


def test_structure_aware_chunking_preserves_heading_context() -> None:
    document = CanonicalDocument(
        title="恢复记录",
        parser="test",
        parser_version="1",
        sections=[
            CanonicalSection(
                heading="恢复后验证",
                heading_path=["Oracle", "恢复后验证"],
                blocks=[
                    CanonicalBlock(
                        kind="paragraph",
                        text="数据库恢复完成。" * 120 + "PDB 仍然处于 MOUNTED 状态。",
                    )
                ],
            )
        ],
    )

    chunks = StructureAwareChunker(target_tokens=80, overlap_tokens=10).chunk(document)

    assert len(chunks) > 1
    assert all(chunk.heading_path == "Oracle > 恢复后验证" for chunk in chunks)
    assert chunks[-1].content.endswith("PDB 仍然处于 MOUNTED 状态。")


def test_imported_markdown_is_searchable_by_exact_error_code(tmp_path: Path) -> None:
    source = tmp_path / "Oracle恢复记录.md"
    source.write_text(
        "# 恢复后验证\n\n恢复完成后出现 ORA-01034，随后检查实例状态。",
        encoding="utf-8",
    )
    importer = Importer(tmp_path / "app-data")

    report = importer.import_paths([str(source)])
    hits = LexicalSearch(importer.database).search("ORA-01034")
    sources = importer.list_sources()
    importer.close()

    assert report["summary"]["imported"] == 1
    assert hits[0]["filename"] == "Oracle恢复记录.md"
    assert "ORA-01034" in hits[0]["content"]
    assert sources[0]["status"] == "searchable"


def test_chinese_substring_search_uses_trigram_index(tmp_path: Path) -> None:
    source = tmp_path / "meeting.txt"
    source.write_text("正式迁移窗口安排在周六凌晨一点。", encoding="utf-8")
    importer = Importer(tmp_path / "app-data")
    importer.import_paths([str(source)])

    hits = LexicalSearch(importer.database).search("迁移窗口")
    importer.close()

    assert hits
    assert hits[0]["filename"] == "meeting.txt"
