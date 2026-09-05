import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS admins(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id_hash TEXT PRIMARY KEY,admin_id TEXT NOT NULL REFERENCES admins(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS releases(
 id TEXT PRIMARY KEY,version TEXT NOT NULL,channel TEXT NOT NULL DEFAULT 'stable',
 status TEXT NOT NULL CHECK(status IN ('DRAFT','READY','ROLLOUT','PUBLISHED','PAUSED','REVOKED')),
 release_notes TEXT NOT NULL,artifact_url TEXT,signature TEXT,artifact_sha256 TEXT,artifact_size INTEGER,
 verified_at TEXT,published_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 rollout_percentage INTEGER NOT NULL DEFAULT 0 CHECK(rollout_percentage BETWEEN 0 AND 100),
 minimum_supported_version TEXT,maximum_supported_version TEXT,mandatory INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,UNIQUE(version,channel));
CREATE TABLE IF NOT EXISTS channel_targets(channel TEXT PRIMARY KEY,target_id TEXT REFERENCES releases(id));
CREATE TABLE IF NOT EXISTS admin_audit_logs(
 id TEXT PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,release_version TEXT,
 old_value TEXT,new_value TEXT,reason TEXT NOT NULL,timestamp TEXT NOT NULL);
INSERT OR IGNORE INTO schema_version VALUES(1);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            existing = c.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
            if existing and (c.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0] > 1):
                raise RuntimeError("控制面数据库版本较新，拒绝降级")
            c.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def audit(c, actor, action, release_version, old, new, reason):
        # Only explicit release policy snapshots, never requests, credentials or installation IDs.
        c.execute("INSERT INTO admin_audit_logs VALUES(?,?,?,?,?,?,?,?)", (
            str(uuid4()), actor, action, release_version,
            json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), reason, now()))
