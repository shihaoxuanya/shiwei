from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4
from uuid import UUID

from shiwei_ai.models import ModelGateway
from shiwei_ai.search_projection import SEARCH_TEXT_VERSION
from shiwei_ai.storage import Database
from shiwei_ai.storage.database import utc_now


TABLE_NAME = "chunk_embeddings"


class LanceVectorStore:
    """Rebuildable LanceDB index keyed by immutable SQLite chunk IDs."""

    def __init__(self, index_dir: Path) -> None:
        import lancedb

        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.connection = lancedb.connect(self.index_dir)

    def search(self, vector: list[float], limit: int = 20) -> list[dict[str, Any]]:
        if not vector or not self._exists():
            return []
        table = self.connection.open_table(TABLE_NAME)
        rows = (
            table.search(vector)
            .distance_type("cosine")
            .select(["chunk_id", "document_id", "embedding_version_id"])
            .limit(limit)
            .to_list()
        )
        return [
            {
                "chunkId": str(row["chunk_id"]),
                "documentId": str(row["document_id"]),
                "embeddingVersionId": str(row["embedding_version_id"]),
                "semanticScore": max(-1.0, min(1.0, 1.0 - float(row.get("_distance", 0.0)))),
                "semanticDistance": float(row.get("_distance", 0.0)),
                "similarityMetric": "cosine",
            }
            for row in rows
        ]

    def replace(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            if self._exists():
                self.connection.drop_table(TABLE_NAME)
            return
        self.connection.create_table(TABLE_NAME, data=rows, mode="overwrite")

    def replace_document(self, document_id: str, rows: list[dict[str, Any]]) -> None:
        UUID(document_id)
        if not self._exists():
            if rows:
                self.connection.create_table(TABLE_NAME, data=rows, mode="overwrite")
            return
        table = self.connection.open_table(TABLE_NAME)
        table.delete(f"document_id = '{document_id}'")
        if rows:
            table.add(rows)

    def delete_document(self, document_id: str) -> None:
        self.replace_document(document_id, [])

    def count(self) -> int:
        if not self._exists():
            return 0
        return int(self.connection.open_table(TABLE_NAME).count_rows())

    def _exists(self) -> bool:
        # Local LanceDB connections do not implement namespace-aware table_exists.
        return TABLE_NAME in self.connection.list_tables().tables


class EmbeddingIndexer:
    def __init__(
        self,
        database: Database,
        vector_store: LanceVectorStore,
        gateway: ModelGateway,
        *,
        provider: str = "openai_compatible",
        batch_size: int = 64,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.gateway = gateway
        self.provider = provider
        self.batch_size = batch_size

    def rebuild(self) -> dict[str, Any]:
        chunks = [dict(row) for row in self.database.connection.execute(
            "SELECT c.id, c.document_id, "
            "COALESCE(NULLIF(c.search_text, ''), d.title || char(10) || char(10) || c.content) AS search_text "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "ORDER BY c.document_id, c.chunk_index"
        ).fetchall()]
        if not chunks:
            self.vector_store.replace([])
            return {"indexed": 0, "model": None, "dimension": None}

        version_id = str(uuid4())
        vector_rows: list[dict[str, Any]] = []
        model: str | None = None
        dimension: int | None = None

        for batch in self._batches(chunks):
            result = self.gateway.embed([str(item["search_text"]) for item in batch])
            if dimension is not None and result.dimension != dimension:
                raise ValueError("Embedding 服务在同一次重建中返回了不同维度")
            model = result.model
            dimension = result.dimension
            for chunk, vector in zip(batch, result.vectors, strict=True):
                vector_rows.append(
                    {
                        "chunk_id": str(chunk["id"]),
                        "document_id": str(chunk["document_id"]),
                        "embedding_version_id": version_id,
                        "vector": vector,
                    }
                )

        self.vector_store.replace(vector_rows)
        with self.database.transaction() as connection:
            connection.execute("UPDATE embedding_versions SET active = 0")
            connection.execute(
                """
                INSERT INTO embedding_versions(id, provider, model, dimension, created_at, active, search_text_version)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (version_id, self.provider, model, dimension, utc_now(), SEARCH_TEXT_VERSION),
            )
        return {
            "indexed": len(vector_rows),
            "model": model,
            "dimension": dimension,
            "embeddingVersionId": version_id,
            "searchTextVersion": SEARCH_TEXT_VERSION,
        }

    def replace_document(self, document_id: str) -> dict[str, Any]:
        UUID(document_id)
        chunks = [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT c.id, c.document_id, "
                "COALESCE(NULLIF(c.search_text, ''), d.title || char(10) || char(10) || c.content) AS search_text "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.document_id = ? ORDER BY c.chunk_index",
                (document_id,),
            ).fetchall()
        ]
        active = self.database.connection.execute(
            """
            SELECT id, model, dimension, search_text_version FROM embedding_versions
            WHERE active = 1 ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        if active is None or not self.vector_store._exists() or active["search_text_version"] != SEARCH_TEXT_VERSION:
            return self.rebuild()
        if not chunks:
            self.vector_store.delete_document(document_id)
            return {"indexed": 0, "model": active["model"], "dimension": active["dimension"]}

        rows: list[dict[str, Any]] = []
        model: str | None = None
        dimension: int | None = None
        for batch in self._batches(chunks):
            result = self.gateway.embed([str(item["search_text"]) for item in batch])
            model = result.model
            dimension = result.dimension
            if model != active["model"] or dimension != active["dimension"]:
                return self.rebuild()
            rows.extend(
                {
                    "chunk_id": str(chunk["id"]),
                    "document_id": document_id,
                    "embedding_version_id": str(active["id"]),
                    "vector": vector,
                }
                for chunk, vector in zip(batch, result.vectors, strict=True)
            )
        self.vector_store.replace_document(document_id, rows)
        return {"indexed": len(rows), "model": model, "dimension": dimension}

    def _batches(self, items: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
        for start in range(0, len(items), self.batch_size):
            yield items[start : start + self.batch_size]
