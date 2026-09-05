"""Verify a database upgrade only on an ephemeral SQLite backup.

The source is opened with mode=ro and query_only. Only the copied temporary
database is passed to Database (which runs migrations). This script neither
reads provider settings/credentials nor constructs a model gateway. It prints
summary counts, hashes, and retrieval outcomes, never stored body text or paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ai-worker"))

from shiwei_ai.retrieval import HybridRetriever
from shiwei_ai.storage import Database

QUERIES = (
    "2025年9月3日我开会了吗？",
    "2025年9月3日会议内容是什么？",
    "我之前开了个会是在几号？",
    "之前那个Oracle迁移的会议是哪天？",
    "我什么时候讨论过TiDB迁移？",
)
TRUTH_TABLES = ("notes", "sources", "documents", "sections", "chunks")
DERIVED_COLUMNS = {"search_text", "mentioned_dates"}


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0)


def truth_summary(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    summaries = {}
    for table in TRUTH_TABLES:
        columns = [
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            if row["name"] not in DERIVED_COLUMNS
        ]
        if not columns:
            raise ValueError("Required original-data table is absent")
        # Only fixed, schema-owned tables and original columns are selected.
        # Provider settings are intentionally excluded from the inspection.
        selected = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        digest = hashlib.sha256()
        identifiers = hashlib.sha256()
        count = 0
        for row in connection.execute(f"SELECT {selected} FROM {table} ORDER BY id"):
            digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            identifiers.update(str(row["id"]).encode("utf-8") + b"\n")
            count += 1
        summaries[table] = {"count": count, "originalRowsSha256": digest.hexdigest(), "idsSha256": identifiers.hexdigest()}
    return summaries


def verify_upgrade(source_path: Path, expected_title: str) -> dict[str, Any]:
    resolved = source_path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Source must be a SQLite database file")
    source = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only = ON")
    try:
        source_version_before = schema_version(source)
        if source_version_before < 5:
            raise ValueError("This note recall verification requires schema 5 or later")
        source_before = truth_summary(source)
        with tempfile.TemporaryDirectory(prefix="shiwei-upgrade-verification-") as temporary:
            snapshot_path = Path(temporary) / "snapshot.sqlite3"
            if snapshot_path.resolve() == resolved:
                raise ValueError("Snapshot must never replace the source")
            snapshot = sqlite3.connect(snapshot_path)
            snapshot.row_factory = sqlite3.Row
            try:
                source.backup(snapshot)
                snapshot_before = truth_summary(snapshot)
                snapshot_version_before = schema_version(snapshot)
            finally:
                snapshot.close()

            # The sole Database constructor is deliberately below the temporary
            # backup boundary. Migration cannot touch source canonical files.
            migrated = Database(snapshot_path)
            try:
                snapshot_after = truth_summary(migrated.connection)
                snapshot_version_after = schema_version(migrated.connection)
                target_rows = migrated.connection.execute(
                    "SELECT id FROM notes WHERE title=? AND deleted_at IS NULL", (expected_title,)
                ).fetchall()
                if len(target_rows) != 1:
                    raise ValueError("Expected exactly one target note in snapshot")
                target_id = target_rows[0]["id"]
                retriever = HybridRetriever(migrated)
                cases = []
                for query in QUERIES:
                    result = retriever.retrieve(query)
                    ranked = list(dict.fromkeys(hit.get("noteId") or hit.get("sourceId") for hit in result["fusionResult"]))
                    rank = ranked.index(target_id) + 1 if target_id in ranked else None
                    citations = result.context.citations
                    cases.append({
                        "query": query,
                        "targetRank": rank,
                        "targetIsFirstCitation": bool(citations and citations[0].note_id == target_id),
                        "firstCitationId": citations[0].citation_id if citations else None,
                        "selectedChunkCount": len(citations),
                    })
            finally:
                migrated.close()

        source_after = truth_summary(source)
        source_version_after = schema_version(source)
        preserved = {
            table: {
                "rowCountBefore": snapshot_before[table]["count"],
                "rowCountAfter": snapshot_after[table]["count"],
                "originalRowsSha256Before": snapshot_before[table]["originalRowsSha256"],
                "originalRowsSha256After": snapshot_after[table]["originalRowsSha256"],
                "originalRowsUnchanged": snapshot_before[table] == snapshot_after[table],
                "idsUnchanged": snapshot_before[table]["idsSha256"] == snapshot_after[table]["idsSha256"],
            }
            for table in TRUTH_TABLES
        }
        result = {
            "sourceAccess": "SQLite mode=ro + PRAGMA query_only=ON; backup only",
            "sourceSchemaVersionBefore": source_version_before,
            "sourceSchemaVersionAfter": source_version_after,
            "sourceTruthUnchangedDuringVerification": source_before == source_after,
            "snapshotWasConsistentWithSource": source_before == snapshot_before,
            "snapshotSchemaVersionBefore": snapshot_version_before,
            "snapshotSchemaVersionAfter": snapshot_version_after,
            "snapshotDestroyedAfterVerification": True,
            "originalDataPreservation": preserved,
            "retrievalMode": "FTS-only; no embeddings or answer generation",
            "providerSettingsRead": False,
            "externalCalls": 0,
            "queries": cases,
        }
        result["passed"] = (
            result["sourceTruthUnchangedDuringVerification"]
            and result["snapshotWasConsistentWithSource"]
            and source_version_before == source_version_after
            and snapshot_version_after >= 6
            and all(item["originalRowsUnchanged"] and item["idsUnchanged"] for item in preserved.values())
            and all(case["targetRank"] == 1 and case["targetIsFirstCitation"] and case["firstCitationId"] == "N1" for case in cases)
        )
        return result
    finally:
        source.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-title", default="2025年9月3日会议")
    args = parser.parse_args()
    try:
        result = verify_upgrade(args.db, args.expected_title)
    except (ValueError, OSError, sqlite3.Error) as error:
        # Never echo provider data, SQL values, filenames or source text in errors.
        print(json.dumps({"passed": False, "errorType": type(error).__name__, "error": "Upgrade verification failed; source remains read-only."}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
