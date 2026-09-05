"""Acceptance regressions for natural-language recall; no live provider or user data.

The semantic fixture deliberately ranks exam deadlines above the meeting.  Passing
therefore requires the retrieval layer to select evidence, not a helpful LLM.
"""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from shiwei_ai.chat.assistant import Assistant
from shiwei_ai.ingestion import Importer
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.notes.service import NoteService
from shiwei_ai.retrieval import HybridRetriever
from shiwei_ai.worker import WorkerServer


POSITIVE_QUERIES = (
    "2025年9月3日我开会了吗？",
    "2025年9月3日会议内容是什么？",
    "我之前开了个会是在几号？",
    "之前那个Oracle迁移的会议是哪天？",
    "我什么时候讨论过TiDB迁移？",
)
NEGATIVE_QUERIES = (
    "我之前讨论过Kubernetes扩容的会议是哪天？",
    "之前那个SAP采购的会议是哪天？",
    "我什么时候讨论过PostgreSQL采购？",
    "我之前那个财务预算会是哪天？",
    "我上次讨论员工调薪是什么时候？",
    "2032年12月31日Oracle迁移会议讲了什么？",
)
NOTE_TITLE = "2025年9月3日会议"
NOTE_CONTENT = (
    "本次会议围绕Oracle至TiDB数据迁移方案展开，明确了全量与增量迁移排期，"
    "探讨了应用适配测试环境的数据量需求及第三方增量同步工具选型。\n"
    "全量数据迁移预计耗时38至50天，增量追平需要7至14天。"
)


class RecallGateway(ModelGateway):
    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.chat_calls: list[list[dict[str, str]]] = []

    def embed(self, texts):
        self.embedded.extend(texts)
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in texts], model="fixture", dimension=2)

    def chat(self, messages, **options):
        self.chat_calls.append(list(messages))
        # An over-eager planner must not drop the unknown topic/date and revive
        # otherwise related evidence through a less restrictive second query.
        if "请求分类器" in messages[0]["content"]:
            return '{"intent":"knowledge","keywords":["Oracle","会议"]}'
        return "会议记录记载了迁移方案。[N1]"

    def stream_chat(self, messages, **options):
        yield self.chat(messages, **options)


class MisleadingSemanticIndex:
    def __init__(self, database) -> None:
        self.database = database
        self.calls = 0

    def search(self, vector, limit=20):
        self.calls += 1
        rows = self.database.connection.execute(
            "SELECT c.id, s.source_type, s.original_filename FROM chunks c "
            "JOIN documents d ON d.id=c.document_id JOIN sources s ON s.id=d.source_id "
            "ORDER BY CASE WHEN s.original_filename LIKE 'OCP%' THEN 0 "
            "WHEN s.source_type='imported_file' THEN 1 ELSE 2 END, s.original_filename"
        ).fetchall()
        return [
            {"chunkId": row["id"], "semanticScore": 0.99 if row["original_filename"].startswith("OCP") else 0.96 if row["source_type"] == "imported_file" else 0.72}
            for row in rows[:limit]
        ]


@pytest.fixture
def recall_library(tmp_path: Path):
    importer = Importer(tmp_path / "library")
    notes = NoteService(importer.database, tmp_path / "library" / "parsed")
    note = notes.create()
    note = notes.update(note["id"], NOTE_TITLE, NOTE_CONTENT)
    for number in range(10):
        path = tmp_path / f"OCP考试预约{number + 1}.txt"
        path.write_text(
            f"OCP考试订单{number + 1}的有效期限为2026年11月28日。"
            "082、083考试和赠送补考机会必须在截止日期之前使用。Oracle认证考试时间及日期安排。",
            encoding="utf-8",
        )
        importer.import_paths([str(path)])
    path = tmp_path / "会议室设备维护说明.txt"
    path.write_text("会议室投影仪和音响的维护方法。手册版本日期2024-02-01，联系电话12345。", encoding="utf-8")
    importer.import_paths([str(path)])
    yield importer, notes, note
    importer.close()


def retriever_for(importer, semantic: bool):
    gateway = RecallGateway() if semantic else None
    index = MisleadingSemanticIndex(importer.database) if semantic else None
    return HybridRetriever(importer.database, gateway=gateway, semantic_index=index), gateway, index


@pytest.mark.parametrize("semantic", [False, True], ids=["fts-only", "misleading-vectors"])
@pytest.mark.parametrize("query", POSITIVE_QUERIES)
def test_five_natural_recall_queries_rank_same_note_first(recall_library, semantic, query):
    importer, _, note = recall_library
    retriever, _, _ = retriever_for(importer, semantic)
    result = retriever.retrieve(query)
    assert result["fusionResult"], query
    assert result["fusionResult"][0]["noteId"] == note["id"], query
    assert result.context.citations[0].note_id == note["id"], query
    assert result.context.citations[0].citation_id == "N1"
    assert {item.note_id for item in result.context.citations} == {note["id"]}, "Distractors must not enter the answer context"
    assert len({hit["chunkId"] for hit in result["fusionResult"]}) == len(result["fusionResult"])


@pytest.mark.parametrize("semantic", [False, True], ids=["fts-only", "misleading-vectors"])
@pytest.mark.parametrize("query", NEGATIVE_QUERIES)
def test_absent_topic_or_explicit_date_abstains_even_with_high_vector_scores(recall_library, semantic, query):
    importer, _, _ = recall_library
    retriever, _, _ = retriever_for(importer, semantic)
    result = retriever.retrieve(query)
    assert result.context.citations == [], query
    assert result.context.text == ""
    assert result["fusionResult"] == []


@pytest.mark.parametrize("query", NEGATIVE_QUERIES)
def test_assistant_cannot_bypass_gate_via_filename_match_or_planner(recall_library, query):
    importer, _, _ = recall_library
    retriever, _, _ = retriever_for(importer, True)
    gateway = RecallGateway()
    response = Assistant(retriever, gateway).ask(query)
    assert response["answerKind"] == "not_found"
    assert response["citations"] == []
    assert not any("content:" in json.dumps(call, ensure_ascii=False) for call in gateway.chat_calls), "Rejected evidence reached answer generation"


def test_worker_recall_preserves_note_citation_in_saved_conversation(recall_library):
    importer, _, note = recall_library
    server = WorkerServer(importer.data_dir)
    server._importer = importer
    server._gateway = RecallGateway()
    for query in POSITIVE_QUERIES:
        response = server._chat({"query": query})
        assert response["answerKind"] == "knowledge", query
        assert response["citations"][0]["noteId"] == note["id"]
        assert response["citations"][0]["citationId"] == "N1"
        saved = server._get_conversation({"conversationId": response["conversationId"]})
        assert saved["conversation"]["messages"][-1]["citations"][0]["noteId"] == note["id"]
    for query in NEGATIVE_QUERIES:
        response = server._chat({"query": query})
        assert response["answerKind"] == "not_found", query
        assert not response["citations"]


def test_note_edit_replaces_date_and_delete_removes_all_recall(recall_library):
    importer, notes, note = recall_library
    retriever, _, _ = retriever_for(importer, True)
    notes.update(note["id"], "2025-10-06会议", NOTE_CONTENT)
    assert not retriever.retrieve("2025年9月3日Oracle迁移会议内容是什么？").context.citations
    result = retriever.retrieve("2025年10月6日Oracle迁移会议内容是什么？")
    assert result.context.citations[0].note_id == note["id"]
    assert notes.get(note["id"])["content"] == NOTE_CONTENT
    notes.delete(note["id"])
    for query in POSITIVE_QUERIES:
        assert not retriever.retrieve(query).context.citations


def test_title_and_body_are_fts_searchable_without_changing_body(recall_library):
    importer, notes, note = recall_library
    from shiwei_ai.ingestion.indexer import LexicalSearch

    lexical = LexicalSearch(importer.database)
    for query in (NOTE_TITLE, "TiDB", "同步工具选型"):
        assert any(hit["noteId"] == note["id"] for hit in lexical.search(query))
    assert notes.get(note["id"])["content"] == NOTE_CONTENT


def test_temporal_boost_does_not_borrow_date_from_another_meeting(recall_library):
    importer, notes, note = recall_library
    other = notes.create()
    notes.update(other["id"], "2025年9月8日财务预算会议", "讨论下一年的财务预算和费用报销规范。")
    retriever, _, _ = retriever_for(importer, True)
    result = retriever.retrieve("之前那个Oracle迁移的会议是哪天？")
    assert result["fusionResult"][0]["noteId"] == note["id"]
    assert {citation.note_id for citation in result.context.citations} == {note["id"]}


@pytest.mark.parametrize("query", [
    "2025年2月30日Oracle迁移会议讲了什么？",
    "9月4日Oracle迁移会议讲了什么？",
])
def test_invalid_or_absent_partial_date_cannot_be_dropped_by_rewrite(recall_library, query):
    importer, _, _ = recall_library
    retriever, _, _ = retriever_for(importer, True)
    assert retriever.retrieve(query).context.citations == []


def test_multi_query_trace_is_opt_in_and_does_not_expose_note_body(recall_library):
    importer, _, note = recall_library
    gateway = RecallGateway()
    index = MisleadingSemanticIndex(importer.database)
    plain = HybridRetriever(importer.database).retrieve(POSITIVE_QUERIES[2])
    assert "debugTrace" not in plain
    result = HybridRetriever(
        importer.database, gateway=gateway, semantic_index=index, debug=True
    ).retrieve(POSITIVE_QUERIES[2])
    trace = result["debugTrace"]
    assert trace["originalQuery"] == POSITIVE_QUERIES[2]
    rewrites = trace["rewrittenQueries"]
    assert 2 <= len(rewrites) <= 4
    queries = [trace["originalQuery"], *rewrites]
    assert len(queries) == len(set(queries))
    assert {run["query"] for run in trace["queryRuns"]} == set(queries)
    assert set(gateway.embedded) == set(queries)
    assert index.calls == len(queries)
    assert all("ftsHits" in run and "vectorHits" in run for run in trace["queryRuns"])
    assert trace["fusionResults"] and trace["selectedContext"]
    assert trace["abstained"] is False
    assert NOTE_CONTENT not in json.dumps(trace, ensure_ascii=False)
    assert result.context.citations[0].note_id == note["id"]


def test_developer_database_inspection_is_read_only_and_never_migrates(recall_library, tmp_path):
    module_path = Path(__file__).resolve().parents[3] / "scripts" / "evaluate-retrieval.py"
    spec = importlib.util.spec_from_file_location("shiwei_retrieval_evaluation", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    importer, _, note = recall_library
    database = module.ReadOnlyDatabase(importer.database.path)
    try:
        assert database.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database.connection.execute("DELETE FROM notes")
        result = module.make_retriever(database, semantic=False).retrieve(POSITIVE_QUERIES[2])
        assert result.context.citations[0].note_id == note["id"]
    finally:
        database.close()

    old_path = tmp_path / "old-schema.sqlite3"
    connection = sqlite3.connect(old_path)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
    connection.execute("INSERT INTO schema_migrations VALUES (5)")
    connection.commit()
    connection.close()
    before = old_path.read_bytes()
    with pytest.raises(ValueError, match="will not migrate"):
        module.ReadOnlyDatabase(old_path)
    assert old_path.read_bytes() == before
