"""Synthetic full-RPC regressions; never read the user's history or providers."""
import json

import pytest

from shiwei_ai.chat.assistant import is_live_weather_request
from shiwei_ai.models import ModelGateway
from shiwei_ai.worker import WorkerServer


class BoundaryGateway(ModelGateway):
    def __init__(self):
        self.calls = []

    def chat(self, messages, **options):
        self.calls.append(messages)
        return "增量迁移是同步全量迁移后新增或变更的数据。"

    def stream_chat(self, messages, **options):
        yield self.chat(messages, **options)

    def embed(self, texts):
        raise AssertionError("These tests must not use an online embedding provider")


@pytest.fixture
def server(tmp_path):
    worker = WorkerServer(tmp_path / "data")
    for name, text in {
        "北京天气.txt": "北京今天气温为二十六摄氏度。此为过往的天气摘录。",
        "2025年9月3日会议.txt": "2025年9月3日会议讨论 Oracle 至 TiDB 迁移，单次38~50天，增量7~14天。",
    }.items():
        file = tmp_path / name
        file.write_text(text, encoding="utf-8")
        worker._get_importer().import_paths([str(file)])
    yield worker
    worker._get_importer().close()


def ask(server, query):
    response = json.loads(server.process_line(json.dumps({
        "jsonrpc": "2.0", "protocol_version": "1.0", "id": "boundary-test",
        "method": "chat", "params": {"query": query},
    })))
    assert "error" not in response, response
    return response["result"]


@pytest.mark.parametrize("query", ["今天北京多少度？", "现在北京天气怎么样？", "明天北京会下雨吗？"])
def test_live_weather_never_uses_old_evidence_or_model(server, query):
    gateway = BoundaryGateway()
    server._gateway = gateway
    answer = ask(server, query)
    assert answer["answerKind"] == "general"
    assert "没有实时天气查询能力" in answer["answer"]
    assert "核对" not in answer["answer"]
    assert "二十六" not in answer["answer"]
    assert answer["citations"] == [] and answer["sourceMatches"] == []
    assert gateway.calls == []
    # Check the persisted full record rather than inferring from a preview.
    history = server._get_conversation({"conversationId": answer["conversationId"]})["conversation"]
    assert history["messages"][-1]["content"] == answer["answer"]
    assert history["messages"][-1]["answerKind"] == "general"


def test_general_concept_needs_no_personal_record(server):
    gateway = BoundaryGateway()
    server._gateway = gateway
    answer = ask(server, "什么是数据库增量迁移？")
    assert answer["answerKind"] == "general"
    assert "增量迁移是" in answer["answer"]
    assert len(gateway.calls) == 1
    assert "2025年9月3日" not in json.dumps(gateway.calls, ensure_ascii=False)
    assert answer["citations"] == []


def test_personal_history_keeps_evidence_and_negative_guard(server):
    answer = ask(server, "我上次开会是什么时候？")
    assert answer["answerKind"] == "knowledge"
    assert {c["sourceFilename"] for c in answer["citations"]} == {"2025年9月3日会议.txt"}
    for query in ["我以前讨论过MongoDB迁移吗？", "我上次MongoDB迁移是什么时候？", "MongoDB迁移会上说了什么？"]:
        missing = ask(server, query)
        assert missing["answerKind"] == "not_found"
        assert "没有找到可靠记录" in missing["answer"]
        assert missing["citations"] == [] and missing["sourceMatches"] == []


@pytest.mark.parametrize("query", ["笔记里今天北京气温是多少？", "我上次在北京时天气怎么样？", "什么是天气预报？", "今天会议时间是几点？"])
def test_weather_boundary_does_not_steal_notes_or_unrelated_questions(query):
    assert not is_live_weather_request(query)
