from __future__ import annotations

from shiwei_ai.retrieval import ContextBuilder, reciprocal_rank_fusion


def hit(chunk_id: str, content: str, filename: str = "notes.md") -> dict[str, object]:
    return {
        "chunkId": chunk_id,
        "documentId": "doc-1",
        "content": content,
        "documentTitle": "恢复记录",
        "filename": filename,
        "headingPath": "恢复后验证",
        "sourcePath": f"C:/knowledge/{filename}",
        "pageNumber": 12,
    }


def test_rrf_rewards_results_found_by_both_channels() -> None:
    lexical = [hit("a", "ORA-01034"), hit("b", "归档日志")]
    semantic = [hit("b", "归档日志"), hit("a", "ORA-01034")]

    fused = reciprocal_rank_fusion(lexical, semantic, query="ORA-01034")

    assert {item["chunkId"] for item in fused[:2]} == {"a", "b"}
    assert fused[0]["debug"]["lexicalRank"] is not None
    assert fused[0]["debug"]["semanticRank"] is not None


def test_context_builder_deduplicates_and_assigns_stable_citations() -> None:
    repeated = hit("a-copy", "PDB 仍然处于 MOUNTED 状态。")
    results = [
        hit("a", "PDB 仍然处于 MOUNTED 状态。"),
        repeated,
        hit("b", "执行 ALTER PLUGGABLE DATABASE OPEN。", "solution.md"),
    ]

    context = ContextBuilder(token_budget=1000).build(results)

    assert [citation.citation_id for citation in context.citations] == ["S1", "S2"]
    assert "[S1]" in context.text and "[S2]" in context.text
    assert context.citations[0].page_number == 12


def test_context_builder_honors_budget_after_first_evidence() -> None:
    results = [hit("a", "证据一" * 30), hit("b", "证据二" * 200)]

    context = ContextBuilder(token_budget=50).build(results)

    assert len(context.citations) == 1


def test_context_keeps_identical_meeting_bodies_with_distinct_dates_and_sources() -> None:
    body = "本次会议讨论 Oracle 至 TiDB 迁移方案。"
    first = {
        **hit("meeting-chunk-1", body),
        "documentId": "meeting-doc-1", "sourceType": "user_note", "noteId": "note-1",
        "filename": "2025年9月3日会议", "mentionedDates": ["2025-09-03"],
    }
    second = {
        **hit("meeting-chunk-2", body),
        "documentId": "meeting-doc-2", "sourceType": "user_note", "noteId": "note-2",
        "filename": "2025年9月8日会议", "mentionedDates": ["2025-09-08"],
    }
    repeated_chunk = {**first, "chunkId": "meeting-chunk-1-copy"}

    context = ContextBuilder().build([first, repeated_chunk, second])

    assert [c.citation_id for c in context.citations] == ["N1", "N2"]
    assert [c.note_id for c in context.citations] == ["note-1", "note-2"]
    assert [c.mentioned_dates for c in context.citations] == [("2025-09-03",), ("2025-09-08",)]
    assert "2025年9月3日会议" in context.text and "2025年9月8日会议" in context.text


def test_fuzzy_recall_keeps_both_meeting_dates_for_disambiguation(tmp_path) -> None:
    from shiwei_ai.notes.service import NoteService
    from shiwei_ai.retrieval import HybridRetriever
    from shiwei_ai.storage import Database

    database = Database(tmp_path / "library.db")
    notes = NoteService(database, tmp_path / "parsed")
    note_ids = []
    for title in ("2025年9月3日会议", "2025年9月8日会议"):
        note = notes.create()
        notes.update(note["id"], title, "本次会议讨论Oracle至TiDB迁移方案。")
        note_ids.append(note["id"])

    result = HybridRetriever(database).retrieve("之前那个Oracle迁移的会议是哪天？")

    assert {c.note_id for c in result.context.citations} == set(note_ids)
    assert {c.citation_id for c in result.context.citations} == {"N1", "N2"}
    assert {c.mentioned_dates for c in result.context.citations} == {("2025-09-03",), ("2025-09-08",)}
    database.close()
