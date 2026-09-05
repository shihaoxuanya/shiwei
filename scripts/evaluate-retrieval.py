"""Developer-only retrieval evaluation and explicit, local debug trace.

No --db: builds a temporary synthetic library, evaluates the five acceptance
queries and six negatives using lexical-only and deliberately misleading fake
vectors. --query prints one trace in that fixture. --db PATH --query QUERY opens
an existing schema-6+ database read-only, with no migrations and no model/API
calls. Source bodies are omitted unless --include-content is explicitly passed.
Nothing is persisted outside the temporary synthetic fixture.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ai-worker"))

from shiwei_ai.ingestion import Importer
from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.notes.service import NoteService
from shiwei_ai.retrieval import HybridRetriever

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


class FixtureGateway(ModelGateway):
    def chat(self, messages, **options):
        raise AssertionError("Retrieval evaluation never calls answer generation")

    def stream_chat(self, messages, **options):
        raise AssertionError("Retrieval evaluation never calls answer generation")
        yield ""  # pragma: no cover

    def embed(self, texts):
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in texts], model="fixture", dimension=2)


class FixtureIndex:
    def __init__(self, database):
        self.database = database

    def search(self, vector, limit=20):
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


@contextmanager
def synthetic_library():
    with tempfile.TemporaryDirectory(prefix="shiwei-retrieval-eval-") as temporary:
        path = Path(temporary)
        importer = Importer(path / "library")
        try:
            notes = NoteService(importer.database, path / "library" / "parsed")
            note = notes.create()
            note = notes.update(
                note["id"], "2025年9月3日会议",
                "本次会议围绕Oracle至TiDB数据迁移方案展开，明确了全量与增量迁移排期，"
                "探讨了应用适配测试环境的数据量需求及第三方增量同步工具选型。\n"
                "全量数据迁移预计耗时38至50天，增量追平需要7至14天。",
            )
            for number in range(10):
                source = path / f"OCP考试预约{number + 1}.txt"
                source.write_text(
                    f"OCP考试订单{number + 1}的有效期限为2026年11月28日。"
                    "082、083考试和赠送补考机会必须在截止日期之前使用。Oracle认证考试时间及日期安排。",
                    encoding="utf-8",
                )
                importer.import_paths([str(source)])
            source = path / "会议室设备维护说明.txt"
            source.write_text("会议室投影仪和音响的维护方法。手册版本日期2024-02-01，联系电话12345。", encoding="utf-8")
            importer.import_paths([str(source)])
            yield importer.database, note["id"]
        finally:
            importer.close()


class ReadOnlyDatabase:
    """Deliberately does not inherit Database: its constructor runs migrations."""

    def __init__(self, path: Path):
        self.path = path.resolve(strict=True)
        if not self.path.is_file():
            raise ValueError("--db must name a database file")
        self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only = ON")
        try:
            version = self.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if not version or version < 6:
                raise ValueError("Database needs retrieval schema 6. This read-only tool will not migrate it.")
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()


def make_retriever(database, *, semantic: bool, include_content: bool = False):
    return HybridRetriever(
        database,
        gateway=FixtureGateway() if semantic else None,
        semantic_index=FixtureIndex(database) if semantic else None,
        debug=True,
        debug_include_content=include_content,
    )


def evaluate(database, note_id: str) -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for mode, semantic in (("fts_only", False), ("misleading_vectors", True)):
        retriever = make_retriever(database, semantic=semantic)
        cases = []
        for query in POSITIVE_QUERIES:
            result = retriever.retrieve(query)
            source_ids = list(dict.fromkeys(hit.get("noteId") or hit.get("sourceId") for hit in result["fusionResult"]))
            rank = source_ids.index(note_id) + 1 if note_id in source_ids else None
            citations = result.context.citations
            correct = bool(citations) and all(c.note_id == note_id for c in citations) and citations[0].citation_id == "N1"
            candidate_recalled = any(hit.get("noteId") == note_id for hit in [*result["lexicalHits"], *result["semanticHits"]])
            cases.append({"query": query, "expected": "target_note", "candidateRecalled": candidate_recalled, "rank": rank, "selectedChunks": len(citations), "citationCorrect": correct})
        negatives = []
        for query in NEGATIVE_QUERIES:
            result = retriever.retrieve(query)
            negatives.append({"query": query, "expected": "abstain", "abstained": not result.context.citations and not result.context.text})
        count = len(cases)
        metrics = {
            "candidateRecall": sum(c["candidateRecalled"] for c in cases) / count,
            "recallAt1": sum(c["rank"] == 1 for c in cases) / count,
            "recallAt5": sum(c["rank"] is not None and c["rank"] <= 5 for c in cases) / count,
            "recallAt12": sum(c["rank"] is not None and c["rank"] <= 12 for c in cases) / count,
            "MRR": sum(1.0 / c["rank"] if c["rank"] else 0.0 for c in cases) / count,
            "citationCorrectness": sum(c["citationCorrect"] for c in cases) / count,
            "negativeAbstention": sum(c["abstained"] for c in negatives) / len(negatives),
        }
        modes[mode] = {"metrics": metrics, "positiveCases": cases, "negativeCases": negatives}
    return {"dataset": "synthetic: 1 meeting note + 10 OCP deadlines + 1 equipment manual", "modelCalls": "no live model; deterministic fake embeddings only", "modes": modes}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Existing schema-6+ SQLite database; opened read-only, no migrations")
    parser.add_argument("--query", help="Print one development retrieval trace instead of synthetic evaluation metrics")
    parser.add_argument("--semantic-fixture", action="store_true", help="Use deliberately misleading fake vectors with a synthetic --query")
    parser.add_argument("--include-content", action="store_true", help="Explicitly include source snippets in stdout trace")
    args = parser.parse_args()
    if args.db and (not args.query or args.semantic_fixture):
        parser.error("--db requires --query and cannot use --semantic-fixture")
    if args.include_content and not args.query:
        parser.error("--include-content requires --query")
    try:
        if args.db:
            database = ReadOnlyDatabase(args.db)
            try:
                output = make_retriever(database, semantic=False, include_content=args.include_content).retrieve(args.query)["debugTrace"]
            finally:
                database.close()
        else:
            with synthetic_library() as (database, note_id):
                output = make_retriever(database, semantic=args.semantic_fixture, include_content=args.include_content).retrieve(args.query)["debugTrace"] if args.query else evaluate(database, note_id)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not args.query:
            return 0 if all(value == 1.0 for lane in output["modes"].values() for value in lane["metrics"].values()) else 1
        return 0
    except (ValueError, sqlite3.Error, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
