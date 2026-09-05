from __future__ import annotations

from pathlib import Path

from shiwei_ai.ingestion import Importer
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.retrieval import HybridRetriever


class FakeGateway(ModelGateway):
    def chat(self, messages, **options):  # type: ignore[no-untyped-def]
        del messages, options
        return ""

    def stream_chat(self, messages, **options):  # type: ignore[no-untyped-def]
        del messages, options
        yield ""

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in texts], model="fake", dimension=2)


class FakeSemanticIndex:
    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id

    def search(self, vector: list[float], limit: int = 20):  # type: ignore[no-untyped-def]
        assert vector == [1.0, 0.0]
        assert limit == 20
        return [{"chunkId": self.chunk_id, "semanticScore": 0.93}]


def test_hybrid_retrieval_exposes_each_debug_stage(tmp_path: Path) -> None:
    source = tmp_path / "oracle.md"
    source.write_text("# 恢复\n\n恢复后 PDB 仍然处于 MOUNTED 状态。", encoding="utf-8")
    importer = Importer(tmp_path / "app-data")
    importer.import_paths([str(source)])
    chunk_id = importer.database.connection.execute("SELECT id FROM chunks").fetchone()[0]

    result = HybridRetriever(
        importer.database,
        gateway=FakeGateway(),
        semantic_index=FakeSemanticIndex(chunk_id),
    ).retrieve("数据库恢复以后子库打不开")
    importer.close()

    assert result["query"] == "数据库恢复以后子库打不开"
    assert result["semanticHits"][0]["chunkId"] == chunk_id
    assert result["fusionResult"][0]["chunkId"] == chunk_id
    assert result.context.citations[0].citation_id == "S1"


def test_vector_index_failure_preserves_local_recall(tmp_path: Path) -> None:
    source = tmp_path / "oracle.md"
    source.write_text("数据库恢复排障：Oracle PDB 状态异常。", encoding="utf-8")
    importer = Importer(tmp_path / "app-data")
    importer.import_paths([str(source)])

    class BrokenIndex:
        def search(self, vector, limit=20):
            raise OSError("index file is unavailable")

    result = HybridRetriever(importer.database, gateway=FakeGateway(), semantic_index=BrokenIndex(), debug=True).retrieve("Oracle数据库恢复")
    assert result.context.citations
    assert result["debugTrace"]["semanticError"] == "vector_index_unavailable"
    importer.close()


def test_embedding_failure_preserves_local_recall(tmp_path: Path) -> None:
    from shiwei_ai.models import ModelGatewayError
    source = tmp_path / "oracle.md"
    source.write_text("数据库恢复排障：Oracle PDB 状态异常。", encoding="utf-8")
    importer = Importer(tmp_path / "app-data")
    importer.import_paths([str(source)])

    class OfflineGateway(FakeGateway):
        def embed(self, texts):
            raise ModelGatewayError("network unavailable")

    result = HybridRetriever(importer.database, gateway=OfflineGateway(), semantic_index=FakeSemanticIndex("unused"), debug=True).retrieve("Oracle数据库恢复")
    assert result.context.citations
    assert result["debugTrace"]["semanticError"] == "embedding_unavailable"
    importer.close()
