from __future__ import annotations

from pathlib import Path

from shiwei_ai.ingestion import Importer
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.retrieval import EmbeddingIndexer, LanceVectorStore


class TinyGateway(ModelGateway):
    def chat(self, messages, **options):  # type: ignore[no-untyped-def]
        return ""

    def stream_chat(self, messages, **options):  # type: ignore[no-untyped-def]
        yield ""

    def embed(self, texts):  # type: ignore[no-untyped-def]
        vectors = []
        for text in texts:
            value = 1.0 if "PDB" in text else 0.0
            vectors.append([value, 1.0 - value, 0.25])
        return EmbeddingResult(vectors=vectors, model="tiny-test", dimension=3)


def test_rebuild_and_semantic_search(tmp_path: Path) -> None:
    source = tmp_path / "oracle.md"
    source.write_text("# 恢复记录\nOracle 恢复后 PDB 处于 MOUNTED，需要 OPEN。", encoding="utf-8")
    importer = Importer(tmp_path / "data")
    importer.import_paths([str(source)])

    store = LanceVectorStore(tmp_path / "data" / "index" / "lancedb")
    report = EmbeddingIndexer(importer.database, store, TinyGateway()).rebuild()

    assert report["indexed"] == 1
    assert report["dimension"] == 3
    assert store.count() == 1
    hits = store.search([1.0, 0.0, 0.25], limit=5)
    assert len(hits) == 1
    assert hits[0]["chunkId"]
    assert hits[0]["semanticScore"] > 0.9
    active = importer.database.connection.execute(
        "SELECT model, dimension, active FROM embedding_versions WHERE active = 1"
    ).fetchone()
    assert tuple(active) == ("tiny-test", 3, 1)
    importer.close()
