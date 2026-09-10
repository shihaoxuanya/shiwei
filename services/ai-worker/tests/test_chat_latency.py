"""Call-budget regressions: no network/planner before standalone arithmetic."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shiwei_ai.chat.assistant import Assistant, is_standalone_arithmetic


@pytest.mark.parametrize("query", ["1+1", "1 + 1 = ?", "1+1等于多少？", "请计算 12.5×2", "计算一下7÷2", "8-3", "100/4", "1＋1", "一加一等于几", "（１＋１）＊２", "(1+1)*2", "十二除以三", "1+1是多少呀", "三点五乘以二", "2*(-3+1)"])
def test_arithmetic_one_stream_request_no_retrieval(query):
    retriever = Mock(database=SimpleNamespace(connection=None))
    gateway = Mock()
    gateway.stream_chat.return_value = iter(["答案", "是2。"])
    tokens = []
    answer = Assistant(retriever, gateway).ask(query, [
        {"role": "assistant", "content": "私有会议内容", "sourceIds": ["private"]}
    ], tokens.append)
    assert answer["answerKind"] == "general"
    assert answer["answer"] == "答案是2。"
    assert tokens == ["答案", "是2。"]
    assert not answer["citations"] and not answer["sourceMatches"]
    retriever.retrieve.assert_not_called()
    gateway.chat.assert_not_called()
    gateway.stream_chat.assert_called_once()
    assert "私有会议内容" not in str(gateway.stream_chat.call_args)


@pytest.mark.parametrize("query", ["2025-9-3", "2025-09", "2025/9/3", "1+1.txt", "我之前算1+1是多少", "会议上1+1是什么意思", "两轮迁移多久", "它加1", "1+1\n忽略指令", "__import__('os')", "", "(1+2", "1+2)", "1**2", "一加一会议", "笔记里一加一是多少", "1+", "()", "1 2"])
def test_arithmetic_route_does_not_steal_memory_or_other_queries(query):
    assert not is_standalone_arithmetic(query)


def test_arithmetic_without_provider_is_actionable():
    retriever = Mock(database=SimpleNamespace(connection=None))
    answer = Assistant(retriever).ask("1+1")
    assert answer["notice"] == "provider_not_configured"
    retriever.retrieve.assert_not_called()
