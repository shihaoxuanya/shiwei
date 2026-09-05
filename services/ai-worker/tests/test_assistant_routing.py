"""Routing must not create a back door around the relevance gate."""
from pathlib import Path

import pytest

from shiwei_ai.chat.assistant import Assistant
from shiwei_ai.ingestion import Importer
from shiwei_ai.notes.service import NoteService
from shiwei_ai.retrieval.context import ContextBuilder
from shiwei_ai.retrieval.hybrid import HybridRetriever


class MustNotGenerate:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **options):
        self.calls.append(messages)
        raise AssertionError("Rejected recall must not reach model planning or generation")


class GateSpy:
    def __init__(self, importer, result=None):
        self.database = importer.database
        self.queries = []
        self.result = result or {
            "lexicalHits": [], "semanticHits": [], "fusionResult": [],
            "context": ContextBuilder().build([]),
        }

    def retrieve(self, query):
        self.queries.append(query)
        return self.result

    def _hydrate_semantic_hits(self, candidates):
        raise AssertionError("A metadata match bypassed the retrieval gate")


@pytest.fixture
def routing_library(tmp_path: Path):
    importer = Importer(tmp_path / "library")
    notes = NoteService(importer.database, tmp_path / "library" / "parsed")
    note = notes.create()
    note = notes.update(note["id"], "2025年9月3日会议", "讨论Oracle至TiDB的数据迁移。")
    path = tmp_path / "Oracle考试日期.txt"
    path.write_text("OCP考试的截止日期是2026年11月28日。", encoding="utf-8")
    importer.import_paths([str(path)])
    yield importer, note
    importer.close()


@pytest.mark.parametrize("query", [
    "2025年9月3日我开会了吗？",
    "2025年9月3日会议内容是什么？",
    "我之前开了个会是在几号？",
    "2032年12月31日Oracle迁移会议讲了什么？",
    "之前那个Kubernetes扩容会议是哪天？",
])
def test_recall_keeps_original_query_even_when_metadata_matches(routing_library, query):
    importer, _ = routing_library
    retriever = GateSpy(importer)
    gateway = MustNotGenerate()
    result = Assistant(retriever, gateway).ask(query)
    assert retriever.queries == [query]
    assert result["answerKind"] == "not_found"
    assert result["sourceMatches"] == []
    assert result["citations"] == []
    assert gateway.calls == []


def test_answer_source_cards_come_only_from_selected_context(routing_library):
    importer, note = routing_library
    database = importer.database.connection
    note_chunk = database.execute(
        "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.source_id=?",
        (note["sourceId"],),
    ).fetchone()["id"]
    wrong_chunk = database.execute(
        "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id "
        "JOIN sources s ON s.id=d.source_id WHERE s.source_type='imported_file'"
    ).fetchone()["id"]
    hits = HybridRetriever(importer.database)._hydrate_semantic_hits([
        {"chunkId": note_chunk}, {"chunkId": wrong_chunk},
    ])
    retriever = GateSpy(importer, {
        "lexicalHits": hits, "semanticHits": [], "fusionResult": hits,
        "context": ContextBuilder(max_chunks=1).build(hits),
    })
    result = Assistant(retriever).ask("我讨论过Oracle迁移吗？")
    assert result["answerKind"] == "knowledge"
    assert [source["noteId"] for source in result["sourceMatches"]] == [note["id"]]
    assert len(result["citations"]) == 1


def test_generic_summary_with_exclusion_also_requires_original_query_gate(routing_library):
    importer, _ = routing_library
    retriever = GateSpy(importer)
    query = "总结不是Oracle的会议记录"
    result = Assistant(retriever).ask(query)
    assert retriever.queries == [query]
    assert not result["citations"]
    assert not result["sourceMatches"]
