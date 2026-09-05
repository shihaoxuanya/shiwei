"""Answer fidelity: storage truth, evidence boundaries and final presentation."""
import json
from pathlib import Path
import pytest

from shiwei_ai.notes.service import NoteService
from shiwei_ai.storage import Database
from shiwei_ai.retrieval import HybridRetriever
from shiwei_ai.chat.service import ChatService
from shiwei_ai.models import ModelGateway

BODY = "单次全量迁移预计耗时38~50天，增量追平需7~14天。至少需要两轮完整生产级全量迁移。本次会议讨论Oracle至TiDB迁移。"


class EchoGateway(ModelGateway):
    def __init__(self, answer=BODY + "[N1]"):
        self.answer = answer
        self.messages = []

    def chat(self, messages, **options):
        self.messages = messages
        return self.answer

    def stream_chat(self, messages, **options):
        for char in self.chat(messages, **options):
            yield char

    def embed(self, texts):
        raise AssertionError("Fidelity fixture must not upload embeddings")


def test_original_ranges_survive_every_storage_and_retrieval_stage(tmp_path):
    db = Database(tmp_path / "library.db")
    notes = NoteService(db, tmp_path / "parsed")
    note = notes.create()
    notes.update(note["id"], "2025年9月3日会议", BODY)
    doc = db.connection.execute("SELECT * FROM documents").fetchone()
    chunk = db.connection.execute("SELECT * FROM chunks").fetchone()
    canonical = Path(doc["canonical_path"]).read_text(encoding="utf-8")
    for text in (notes.get(note["id"])["content"], canonical, chunk["content"], chunk["search_text"], doc["search_text"]):
        assert "38~50" in text and "7~14" in text
    for table in ("chunks_fts", "chunks_fts_trigram"):
        indexed = db.connection.execute(f"SELECT chunk_content FROM {table}").fetchone()[0]
        assert "38~50" in indexed.replace(" ", "") and "7~14" in indexed.replace(" ", "")
    retriever = HybridRetriever(db)
    retrieval = retriever.retrieve("全量迁移大概多久？")
    assert "38~50" in retrieval["fusionResult"][0]["content"]
    assert "38～50 天" in retrieval.context.text and "7～14 天" in retrieval.context.text
    gateway = EchoGateway()
    answer = ChatService(retriever, gateway).ask("全量迁移大概多久？", retrieval=retrieval)
    assert "38～50 天" in gateway.messages[-1]["content"]
    assert "38～50 天" in answer.answer and "7～14 天" in answer.answer
    assert notes.get(note["id"])["content"] == BODY
    db.close()


def test_embedding_input_preserves_original_ranges_and_title(tmp_path):
    from shiwei_ai.models import EmbeddingResult
    from shiwei_ai.retrieval import EmbeddingIndexer, LanceVectorStore
    db = Database(tmp_path / 'library.db')
    notes = NoteService(db, tmp_path / 'parsed')
    note = notes.create()
    notes.update(note['id'], '2025年9月3日会议', BODY)
    class RecordingGateway(EchoGateway):
        inputs = []
        def embed(self, texts):
            self.inputs.extend(texts)
            return EmbeddingResult(vectors=[[1.0, 0.5, 0.2] for _ in texts], model='recording', dimension=3)
    gateway = RecordingGateway()
    EmbeddingIndexer(db, LanceVectorStore(tmp_path / 'vectors'), gateway).rebuild()
    assert gateway.inputs == ['2025年9月3日会议\n\n' + BODY]
    assert notes.get(note['id'])['content'] == BODY
    db.close()


@pytest.fixture
def library(tmp_path):
    from shiwei_ai.ingestion import Importer
    importer = Importer(tmp_path / "library")
    notes = NoteService(importer.database, tmp_path / "library" / "parsed")
    note = notes.create()
    notes.update(note["id"], "2025年9月3日会议", BODY)
    for name, body in (
        ("TiDB系统部署报告.txt", "TiDB迁移部署报告：记录TiDB服务器安装、数据库参数和部署拓扑。该报告不记录会议决策，会议内容请另见会议笔记。"),
        ("OCP考试预约.txt", "Oracle OCP考试截止日期2026年11月28日，考试预约需要联系服务商。"),
    ):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        importer.import_paths([str(path)])
    yield importer.database, note
    importer.close()


@pytest.mark.parametrize("query,raw,expected", [
    ("全量迁移大概多久？", "单次全量迁移预计38~50天。[N1]", "38～50 天"),
    ("增量追平多久？", "增量追平需7~14天。[N1]", "7～14 天"),
    ("那次会议说全量迁移要多久？", "原文记录单次全量迁移需38～50天，至少两轮。[N1]", "38～50 天"),
    ("两轮大概要多久？", "原文记录单次38～50天，至少两轮。[N1]\n\n如果两轮完全串行且每轮相同，简单计算约76～100天；这不是会议原文直接给出的项目总工期。", "76～100 天"),
    ("我上次开会是什么时候？", "你记录的这场会议是2025年9月3日。[N1]", "2025年9月3日"),
    ("之前讨论Oracle迁移的会是哪天？", "你记录的这场会议是2025年9月3日。[N1]", "2025年9月3日"),
])
def test_answer_fidelity_source_fact_calculation_and_recall(library, query, raw, expected):
    from shiwei_ai.chat.assistant import Assistant
    database, note = library
    gateway = EchoGateway(raw)
    result = Assistant(HybridRetriever(database), gateway).ask(query)
    assert result["answerKind"] == "knowledge"
    assert expected in result["answer"]
    assert "3850天" not in result["answer"] and "714天" not in result["answer"]
    assert result["citations"][0]["note_id"] == note["id"]
    assert "Derived Inference" in gateway.messages[0]["content"]


@pytest.mark.parametrize("query", [
    "我以前讨论过MongoDB迁移吗？", "我上次MongoDB迁移是什么时候？",
    "MongoDB迁移会上说了什么？", "我之前有没有讨论过MongoDB迁移？",
])
def test_mongodb_negative_never_reaches_answer_generation(library, query):
    from shiwei_ai.chat.assistant import Assistant
    database, _ = library
    gateway = EchoGateway()
    result = Assistant(HybridRetriever(database), gateway).ask(query)
    assert result["answerKind"] == "not_found"
    assert "没有找到可靠记录" in result["answer"]
    assert result["citations"] == [] and result["sourceMatches"] == []
    assert gateway.messages == []


def test_meeting_evidence_excludes_related_deployment_report(library):
    database, note = library
    result = HybridRetriever(database, debug=True).retrieve("之前TiDB迁移会上说了什么？")
    assert {c.note_id for c in result.context.citations} == {note["id"]}
    report = next(h for h in result["debugTrace"]["fusionResults"] if h["filename"] == "TiDB系统部署报告.txt")
    assert report["selected_as_evidence"] is False
    assert report["evidence_reason"] == "topic_related_but_not_event_evidence"
    assert "TiDB系统部署报告" not in result.context.text
    # A request for related technical material must not be treated as a meeting.
    related = HybridRetriever(database).retrieve("还有哪些TiDB相关资料？")
    assert any(h["filename"] == "TiDB系统部署报告.txt" for h in related["fusionResult"])


@pytest.mark.parametrize("raw", [
    "会议说全量迁移需要3850天，增量需714天。[N1]",
    "会议说整个项目总工期76～100天。[N1]",
    "单次38～50天。[N1]\n\n如果两轮完全串行，简单计算76～110天；这不是原文直接结论。",
])
def test_unsafe_numbers_are_not_streamed_or_silently_presented_as_facts(library, raw):
    database, _ = library
    tokens = []
    result = ChatService(HybridRetriever(database), EchoGateway(raw)).ask("那次会议说全量迁移要多久？", on_token=tokens.append)
    assert "3850天" not in result.answer and "714天" not in result.answer
    assert "76～100" not in result.answer and "76～110" not in result.answer
    assert "38～50 天" in result.answer and "7～14 天" in result.answer
    assert "".join(tokens) == result.answer


def test_one_repair_can_produce_marked_calculation(library):
    database, _ = library
    class RepairGateway(EchoGateway):
        calls = 0
        def chat(self, messages, **options):
            self.calls += 1
            if self.calls == 1:
                return "会议说总工期76~100天。[N1]"
            return "原文单次38～50天、至少两轮。[N1]\n\n如果两轮完全串行，简单计算约76～100天；这不是原文直接给出的项目总工期。"
    gateway = RepairGateway()
    result = ChatService(HybridRetriever(database), gateway).ask("两轮大概要多久？")
    assert gateway.calls == 2
    assert "76～100 天" in result.answer and "不是原文" in result.answer


def test_note_dates_survive_worker_response_and_history(library):
    from shiwei_ai.worker import WorkerServer
    database, note = library
    server = WorkerServer(database.path.parent)
    result = server._chat({"query": "我上次开会是什么时候？"})
    citation = result["citations"][0]
    assert citation["noteId"] == note["id"] and citation["mentionedDates"] == ["2025-09-03"]
    saved = server._get_conversation({"conversationId": result["conversationId"]})
    assert saved["conversation"]["messages"][-1]["citations"][0]["mentionedDates"] == ["2025-09-03"]
    server._get_importer().close()
