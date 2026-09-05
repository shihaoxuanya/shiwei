from __future__ import annotations

from shiwei_ai.chat import ChatService
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.retrieval.context import BuiltContext, CitationContext
from shiwei_ai.chat.service import verified_stream


class FakeRetriever:
    def __init__(self, with_context: bool = True) -> None:
        self.with_context = with_context

    def retrieve(self, query: str):  # type: ignore[no-untyped-def]
        citations = (
            [
                CitationContext(
                    citation_id="S1",
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    source_filename="Oracle恢复记录.md",
                    source_path="C:/knowledge/Oracle恢复记录.md",
                    page_number=None,
                    heading_path="恢复后验证",
                    snippet="PDB 仍然处于 MOUNTED 状态。",
                )
            ]
            if self.with_context
            else []
        )
        return {
            "query": query,
            "lexicalHits": [],
            "semanticHits": [],
            "fusionResult": [],
            "context": BuiltContext("[S1]\ncontent: PDB MOUNTED", citations, 20),
        }


class FakeGateway(ModelGateway):
    def chat(self, messages, **options):  # type: ignore[no-untyped-def]
        assert "[S1]" in messages[-1]["content"]
        assert "temperature" not in options  # Not all vendors accept sampling overrides.
        return "你曾遇到 PDB 处于 MOUNTED 状态。[S1] 未知补充。[S99]"

    def stream_chat(self, messages, **options):  # type: ignore[no-untyped-def]
        del messages, options
        yield "流式回答"
        yield "。[S1]"

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(vectors=[[1.0] for _ in texts], model="fake", dimension=1)


def test_chat_keeps_only_verified_citations() -> None:
    result = ChatService(FakeRetriever(), FakeGateway()).ask("我遇到过什么问题？")  # type: ignore[arg-type]

    assert "[S1]" in result.answer
    assert "[S99]" not in result.answer
    assert [citation["citation_id"] for citation in result.citations] == ["S1"]


def test_chat_does_not_call_model_without_reliable_evidence() -> None:
    result = ChatService(FakeRetriever(with_context=False), FakeGateway()).ask("不存在的问题")  # type: ignore[arg-type]

    assert result.answer.startswith("没有找到可靠记录")
    assert result.citations == []


def test_chat_streams_tokens_then_keeps_verified_citation() -> None:
    tokens: list[str] = []
    result = ChatService(FakeRetriever(), FakeGateway()).ask(  # type: ignore[arg-type]
        "恢复问题",
        on_token=tokens.append,
    )

    assert tokens == ["流式回答", "。[S1]"]
    assert result.answer == "流式回答。[S1]"
    assert result.citations[0]["citation_id"] == "S1"


def test_stream_checks_split_citation_ids_before_emission():
    assert "".join(verified_stream(["结论。[", "S9", "9]。真实[", "S1", "]"], {"S1"})) == "结论。。真实[S1]"
    assert "".join(verified_stream(["通用回答[S", "1]"], set())) == "通用回答"
