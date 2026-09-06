"""First-party, bounded metadata telemetry. No content store or individual profiles API.

The analytics DB is separate from release administration so its retention/capacity
cannot grow the release database. Only validated scalar columns are ever stored.
"""
import hashlib
import hmac
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator, model_validator

EVENTS = (
    "app_installed", "app_opened", "app_version", "import_started", "import_completed",
    "import_failed", "note_created", "question_asked", "retrieval_succeeded",
    "retrieval_abstained", "first_successful_recall", "citation_clicked", "update_available",
    "update_started", "update_completed", "update_failed", "app_error",
)
ERRORS = (
    "frontend_error", "unhandled_rejection", "worker_exited", "worker_timeout", "worker_error",
    "rust_panic", "update_network", "update_manifest", "update_signature", "update_download",
    "update_install", "update_worker_busy", "update_unknown", "import_error", "chat_error",
)
LOCAL = timezone(timedelta(hours=8))
RETENTION_DAYS = 90
MAX_EVENTS = 200_000
MAX_INSTALLATIONS = 20_000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Frame(Strict):
    module: Literal["frontend", "worker_client", "desktop", "updater"]
    line: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    column: Annotated[int, Field(ge=0, le=10_000_000)] | None = None


class Properties(Strict):
    file_count_bucket: Literal["1", "2-10", "11-100", "100+"] | None = None
    success_count: Annotated[int, Field(ge=0, le=100_000)] | None = None
    failure_count: Annotated[int, Field(ge=0, le=100_000)] | None = None
    duration_ms: Annotated[int, Field(ge=0, le=86_400_000)] | None = None
    conversation_mode: Literal["knowledge_first"] | None = None
    error_type: str | None = None
    frames: Annotated[list[Frame], Field(max_length=12)] | None = None

    @field_validator("error_type")
    @classmethod
    def error(cls, value):
        if value not in ERRORS:
            raise ValueError("Unknown error type")
        return value


class Event(Strict):
    schema_version: Literal[1]
    event_id: UUID4
    installation_id: UUID4
    event: str
    app_version: Annotated[str, Field(pattern=r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")]
    os: Literal["windows", "linux", "macos"]
    architecture: Literal["x86_64", "aarch64", "x86"]
    timestamp: Annotated[str, Field(max_length=40)]
    properties: Properties = Field(default_factory=Properties)

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_type(cls, value):
        if type(value) is not int:
            raise ValueError("Integer schema version required")
        return value

    # FastAPI validates a Python dictionary rather than JSON bytes. Only canonical UUIDs,
    # never arbitrary installation names, are accepted at the external boundary.
    @field_validator("event_id", "installation_id", mode="before")
    @classmethod
    def uuid(cls, value):
        from uuid import UUID
        if isinstance(value, str):
            parsed = UUID(value)
            if str(parsed) != value or parsed.version != 4:
                raise ValueError("Expected canonical random UUID")
            return parsed
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_format(cls, value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def event_fields(self):
        if self.event not in EVENTS:
            raise ValueError("Unknown event")
        allowed = set()
        if self.event.startswith("import_"):
            allowed = {"file_count_bucket", "success_count", "failure_count", "duration_ms"}
        if self.event == "question_asked":
            allowed = {"conversation_mode"}
        if self.event in ("app_error", "update_failed"):
            allowed = {"error_type", "frames"}
        if self.properties.model_fields_set - allowed:
            raise ValueError("Unexpected event metadata")
        return self


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS installations(
 installation_hash TEXT PRIMARY KEY, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 event_hash TEXT PRIMARY KEY, installation_hash TEXT NOT NULL,
 occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL, event TEXT NOT NULL,
 version TEXT NOT NULL, os TEXT NOT NULL, architecture TEXT NOT NULL,
 success_count INTEGER NOT NULL, failure_count INTEGER NOT NULL,
 duration_ms INTEGER, error_type TEXT, module TEXT);
CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS events_installation ON events(installation_hash,occurred_at);
INSERT OR IGNORE INTO schema_version VALUES(1);
"""


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def ratio(n, d):
    return round(n / d, 4) if d else None


class Analytics:
    def __init__(self, path: Path, salt: str, clock=time.time):
        self.path, self.salt, self.clock = path, salt.encode(), clock
        self.configured = len(salt) >= 32
        self.last_cleanup = 0
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            old = c.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
            if old and c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] > 1:
                raise RuntimeError("统计数据库版本较新，拒绝降级")
            c.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
            # Sensitive pseudonymous records must also be removed from freed SQLite pages.
            c.execute("PRAGMA secure_delete=ON")
        self.prune()

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=2)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA secure_delete=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def identity(self, domain, value):
        return hmac.new(self.salt, f"{domain}:{value}".encode(), hashlib.sha256).hexdigest()

    def prune(self):
        stamp = int(self.clock())
        with self.lock, self.connection() as c:
            c.execute("DELETE FROM events WHERE occurred_at<?", (stamp - RETENTION_DAYS * 86400,))
            c.execute("DELETE FROM installations WHERE last_seen<?", (stamp - RETENTION_DAYS * 86400,))
            c.commit()
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.last_cleanup = stamp

    def ingest(self, event: Event):
        if not self.configured:
            raise HTTPException(503, "统计服务未启用")
        received = int(self.clock())
        occurred = int(datetime.fromisoformat(event.timestamp.replace("Z", "+00:00")).timestamp())
        if not received - 86400 <= occurred <= received + 300:
            raise HTTPException(422, "事件时间超出允许范围")
        # Clamp a slightly fast client clock; never show observations from the future.
        occurred = min(occurred, received)
        if received - self.last_cleanup >= 3600:
            self.prune()
        install = self.identity("installation", event.installation_id)
        key = self.identity("event", f"{event.installation_id}:{event.event_id}")
        props = event.properties
        with self.lock, self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM events WHERE event_hash=?", (key,)).fetchone():
                return  # Idempotent delivery, including duplicate retries.
            if c.execute("SELECT count(*) FROM events WHERE installation_hash=? AND received_at>=?", (install, received - 86400)).fetchone()[0] >= 2000:
                raise HTTPException(429, "已达到本安装实例的统计上限")
            if c.execute("SELECT count(*) FROM events").fetchone()[0] >= MAX_EVENTS:
                raise HTTPException(503, "统计容量已满；不会影响版本服务")
            existing = c.execute("SELECT 1 FROM installations WHERE installation_hash=?", (install,)).fetchone()
            if not existing and c.execute("SELECT count(*) FROM installations").fetchone()[0] >= MAX_INSTALLATIONS:
                raise HTTPException(503, "统计容量已满；不会影响版本服务")
            c.execute("INSERT INTO installations VALUES(?,?,?) ON CONFLICT(installation_hash) DO UPDATE SET first_seen=min(first_seen,excluded.first_seen),last_seen=max(last_seen,excluded.last_seen)", (install, occurred, occurred))
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                key, install, occurred, received, event.event, event.app_version, event.os, event.architecture,
                props.success_count or 0, props.failure_count or 0, props.duration_ms, props.error_type,
                props.frames[0].module if props.frames else None,
            ))

    def summary(self, days=7):
        if days not in (7, 30):
            raise HTTPException(422, "仅支持最近 7 天或 30 天")
        stamp = int(self.clock())
        if stamp - self.last_cleanup >= 3600:
            self.prune()
        today = datetime.fromtimestamp(stamp, LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int((today - timedelta(days=days - 1)).timestamp())
        day_zero = int(today.timestamp())
        with self.connection() as c:
            # One read transaction makes every card share a consistent snapshot.
            c.execute("BEGIN")
            def scalar(sql, params=()):
                return c.execute(sql, params).fetchone()[0]
            counts = {r["event"]: r["n"] for r in c.execute("SELECT event,count(*) n FROM events WHERE occurred_at>=? GROUP BY event", (start,))}
            succeeded = counts.get("retrieval_succeeded", 0)
            abstained = counts.get("retrieval_abstained", 0)
            import_success = scalar("SELECT coalesce(sum(success_count),0) FROM events WHERE event='import_completed' AND occurred_at>=?", (start,))
            import_failed = scalar("SELECT coalesce(sum(failure_count),0) FROM events WHERE event='import_failed' AND occurred_at>=?", (start,))
            overview = {
                "new_installations": scalar("SELECT count(*) FROM installations WHERE first_seen>=?", (start,)),
                "active_installations": scalar("SELECT count(DISTINCT installation_hash) FROM events WHERE occurred_at>=?", (start,)),
                "questions": counts.get("question_asked", 0), "successful_recalls": succeeded, "abstentions": abstained,
                "recall_success_rate": ratio(succeeded, succeeded + abstained),
                "import_success_files": import_success, "import_failed_files": import_failed,
                "import_failure_rate": ratio(import_failed, import_failed + import_success),
                "errors": counts.get("app_error", 0), "citation_clicks": counts.get("citation_clicked", 0),
                "notes_created": counts.get("note_created", 0),
                "error_installations": scalar("SELECT count(DISTINCT installation_hash) FROM events WHERE event='app_error' AND occurred_at>=?", (start,)),
            }
            # Any allowed activity counts, not only process starts (apps may stay open overnight).
            for label, window in (("dau", 1), ("wau", 7), ("mau", 30)):
                overview[label] = scalar("SELECT count(DISTINCT installation_hash) FROM events WHERE occurred_at>=?", (day_zero - (window - 1) * 86400,))
            trend_rows = {r["date"]: dict(r) for r in c.execute("""SELECT date(occurred_at,'unixepoch','+8 hours') date,
                count(DISTINCT installation_hash) active, sum(event='question_asked') questions,
                sum(event='retrieval_succeeded') recalls, sum(event='retrieval_abstained') abstentions,
                sum(event='app_error') errors FROM events WHERE occurred_at>=? GROUP BY date""", (start,))}
            trend = []
            for n in range(days):
                day = (datetime.fromtimestamp(start, LOCAL) + timedelta(days=n)).date().isoformat()
                trend.append(trend_rows.get(day, {"date": day, "active": 0, "questions": 0, "recalls": 0, "abstentions": 0, "errors": 0}))
            versions = [dict(r) for r in c.execute("""SELECT version,count(*) installations FROM (
                SELECT version,row_number() OVER(PARTITION BY installation_hash ORDER BY occurred_at DESC,received_at DESC,rowid DESC) rank
                FROM events WHERE occurred_at>=?) WHERE rank=1 GROUP BY version ORDER BY installations DESC,version LIMIT 50""", (start,))]
            errors = [dict(r) for r in c.execute("""SELECT coalesce(error_type,'unknown') error_type,version,count(*) occurrences,
                count(DISTINCT installation_hash) installations FROM events WHERE event IN ('app_error','update_failed')
                AND occurred_at>=? GROUP BY error_type,version ORDER BY occurrences DESC LIMIT 50""", (start,))]
            retention = []
            for lag in (1, 7, 30):
                # A cohort matures only after the ENTIRE target day ends. Never display 0%
                # when it is not yet observable; same-installation, exact-day retention.
                cohort_start = start - lag * 86400
                eligible = scalar("SELECT count(*) FROM installations WHERE first_seen>=? AND first_seen<?", (cohort_start, day_zero - lag * 86400))
                returned = scalar("""SELECT count(*) FROM installations i WHERE first_seen>=? AND first_seen<? AND EXISTS (
                    SELECT 1 FROM events e WHERE e.installation_hash=i.installation_hash
                    AND date(e.occurred_at,'unixepoch','+8 hours')=date(i.first_seen,'unixepoch','+8 hours',?))""",
                    (cohort_start, day_zero - lag * 86400, f"+{lag} days"))
                retention.append({"day": lag, "eligible": eligible, "returned": returned, "rate": ratio(returned, eligible)})
            result = {
                "configured": self.configured, "window_days": days, "timezone": "Asia/Shanghai", "generated_at": iso(stamp),
                "last_received_at": iso(scalar("SELECT max(received_at) FROM events")),
                "storage": {"events": scalar("SELECT count(*) FROM events"), "capacity": MAX_EVENTS, "retention_days": RETENTION_DAYS},
                "overview": overview, "trend": trend, "versions": versions, "errors": errors, "retention": retention,
                "updates": {x: counts.get("update_" + x, 0) for x in ("available", "started", "completed", "failed")},
            }
        return result
