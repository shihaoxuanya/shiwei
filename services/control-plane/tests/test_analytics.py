from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shiwei_control.analytics import Analytics, Event
from shiwei_control.app import PASSWORDS, Settings, create_app
from shiwei_control.storage import now

ORIGIN = "https://control.example.test"
BASE = datetime(2026, 9, 6, 4, tzinfo=timezone.utc)


def payload(event="app_opened", identifier=None, at=BASE, **properties):
    return {"schema_version": 1, "event_id": str(uuid4()), "installation_id": identifier or str(uuid4()),
            "event": event, "timestamp": at.isoformat(), "app_version": "0.3.1", "os": "windows",
            "architecture": "x86_64", "properties": properties}


@pytest.fixture
def analytics(tmp_path):
    return Analytics(tmp_path / "analytics.db", "synthetic-test-salt-not-a-secret-0000", clock=lambda: BASE.timestamp())


def deliver(service, event="app_opened", identifier=None, at=BASE, **props):
    service.clock = lambda: at.timestamp()
    data = payload(event, identifier, at, **props)
    service.ingest(Event.model_validate(data))
    return data


def test_idempotent_hashed_storage_no_input_payload_or_ip(analytics):
    identifier = str(uuid4())
    data = deliver(analytics, "app_error", identifier, error_type="frontend_error", frames=[{"module": "frontend", "line": 123}])
    analytics.ingest(Event.model_validate(data))
    with analytics.connection() as c:
        dump = "\n".join(c.iterdump())
        assert identifier not in dump and data["event_id"] not in dump
        assert "synthetic-test-salt" not in dump and '"line"' not in dump
        assert c.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert identifier not in str(analytics.summary())


@pytest.mark.parametrize("changes", [
    {"query": "private question"}, {"installation_id": "my-real-name"}, {"event": "private question"},
    {"os": "C:/private/file"}, {"app_version": "0.3.1 private"}, {"schema_version": 2}, {"schema_version": True},
    {"timestamp": "2026-09-06"}, {"event_id": "00000000-0000-0000-0000-000000000000"},
    {"properties": {"filename": "secret.pdf"}}, {"properties": {"success_count": 5}},
])
def test_reject_unknown_or_sensitive_fields(changes):
    with pytest.raises(ValidationError):
        Event.model_validate({**payload(), **changes})


@pytest.mark.parametrize("props", [
    {"error_type": "raw exception text"}, {"frames": [{"module": "C:/private"}]},
    {"frames": [{"module": "frontend", "path": "private"}]}, {"frames": [{"module": "frontend"}] * 13},
])
def test_error_allowlist(props):
    with pytest.raises(ValidationError):
        Event.model_validate(payload("app_error", **props))


@pytest.mark.parametrize("props", [
    {"success_count": -1}, {"success_count": True}, {"success_count": "3"},
    {"failure_count": 100001}, {"duration_ms": 86400001}, {"file_count_bucket": "my files"},
])
def test_numeric_bounds(props):
    with pytest.raises(ValidationError):
        Event.model_validate(payload("import_completed", **props))


def test_import_partial_jobs_not_double_counted_and_retrieval_denominator(analytics):
    identifier = str(uuid4())
    deliver(analytics, "import_completed", identifier, success_count=3, failure_count=1)
    deliver(analytics, "import_failed", identifier, success_count=3, failure_count=1)
    for _ in range(3):
        deliver(analytics, "retrieval_succeeded", identifier)
    deliver(analytics, "retrieval_abstained", identifier)
    for _ in range(5):
        deliver(analytics, "question_asked", identifier, conversation_mode="knowledge_first")
    overview = analytics.summary()["overview"]
    assert overview["import_success_files"] == 3 and overview["import_failed_files"] == 1
    assert overview["import_failure_rate"] == .25 and overview["recall_success_rate"] == .75
    assert overview["active_installations"] == 1 and overview["new_installations"] == 1


def test_daily_windows_latest_version_and_zero_filling(analytics):
    identifier = str(uuid4())
    deliver(analytics, identifier=identifier, at=BASE - timedelta(days=10))
    deliver(analytics, identifier=identifier, at=BASE - timedelta(days=3))
    newer = payload(identifier=identifier, at=BASE)
    newer["app_version"] = "0.4.0"
    analytics.clock = lambda: BASE.timestamp()
    analytics.ingest(Event.model_validate(newer))
    result = analytics.summary()
    assert result["versions"] == [{"version": "0.4.0", "installations": 1}]
    assert len(result["trend"]) == 7 and result["trend"][-1]["active"] == 1
    assert result["trend"][0]["active"] == 0
    assert result["overview"]["new_installations"] == 0
    assert result["overview"]["dau"] == result["overview"]["wau"] == result["overview"]["mau"] == 1


def test_retention_maturity_exact_day_and_no_data_not_zero(analytics):
    identifier = str(uuid4())
    deliver(analytics, identifier=identifier, at=BASE - timedelta(days=32))
    deliver(analytics, identifier=identifier, at=BASE - timedelta(days=2))
    deliver(analytics, identifier=str(uuid4()), at=BASE)
    rows = analytics.summary()["retention"]
    assert rows[2] == {"day": 30, "eligible": 1, "returned": 1, "rate": 1}
    assert rows[1]["eligible"] == 0 and rows[1]["rate"] is None
    empty = Analytics(analytics.path.with_name("empty.db"), "synthetic-salt-00000000000000000000")
    assert empty.summary()["overview"]["recall_success_rate"] is None
    assert empty.summary()["last_received_at"] is None


def test_timezone_midnight_and_duplicates(analytics):
    identifier = str(uuid4())
    # 16:00 UTC is midnight in Shanghai.
    at = datetime(2026, 9, 5, 15, 59, tzinfo=timezone.utc)
    deliver(analytics, identifier=identifier, at=at)
    deliver(analytics, identifier=identifier, at=at + timedelta(minutes=2))
    analytics.clock = lambda: BASE.timestamp()
    trend = analytics.summary()["trend"]
    assert trend[-2]["active"] == 1 and trend[-1]["active"] == 1


def test_retention_capacity_and_expired_timestamp(analytics, monkeypatch):
    deliver(analytics, at=BASE - timedelta(days=91))
    analytics.clock = lambda: BASE.timestamp()
    analytics.prune()
    assert analytics.summary()["storage"]["events"] == 0
    with analytics.connection() as c:
        assert c.execute("SELECT count(*) FROM installations").fetchone()[0] == 0
    for at in (BASE - timedelta(days=2), BASE + timedelta(minutes=10)):
        with pytest.raises(HTTPException) as exc:
            analytics.ingest(Event.model_validate(payload(at=at)))
        assert exc.value.status_code == 422
    monkeypatch.setattr("shiwei_control.analytics.MAX_EVENTS", 1)
    deliver(analytics)
    with pytest.raises(HTTPException) as exc:
        deliver(analytics)
    assert exc.value.status_code == 503


def test_ingestion_auth_dashboard_and_release_storage_separation(tmp_path):
    app = create_app(Settings(tmp_path / "control.db", ORIGIN, analytics_salt="synthetic-test-00000000000000000000"))
    with app.state.store.connection(write=True) as c:
        c.execute("INSERT INTO admins VALUES(?,?,?,?)", ("test", "qa@example.test", PASSWORDS.hash("synthetic-ui-qa-only"), now()))
    with TestClient(app, base_url=ORIGIN) as client:
        data = payload(at=datetime.now(timezone.utc))
        assert client.get("/api/admin/metrics").status_code == 401
        assert client.post("/api/v1/telemetry/events", json=data).status_code == 204
        assert client.post("/api/v1/telemetry/events", json=data, headers={"Origin": ORIGIN}).status_code == 403
        assert client.post("/api/v1/telemetry/events", json={**data, "question": "sensitive"}).status_code == 422
        assert client.post("/api/v1/telemetry/events", content=b"x" * 4097).status_code == 413
        login = client.post("/api/auth/login", json={"email": "qa@example.test", "password": "synthetic-ui-qa-only"}, headers={"Origin": ORIGIN})
        assert login.status_code == 200
        assert client.get("/api/admin/metrics").json()["overview"]["dau"] == 1
        assert client.get("/api/admin/metrics?days=365").status_code == 422
        assert client.get("/api/admin/events").status_code == 404  # No profiles/raw events API.
        assert client.get("/api/admin/releases").status_code == 200
        with app.state.store.connection() as c:
            assert "events" not in "\n".join(c.iterdump())


def test_disabled_service_and_analytics_failure_do_not_break_releases(tmp_path):
    path = tmp_path / "control.db"
    app = create_app(Settings(path, ORIGIN))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/v1/telemetry/events", json=payload(at=datetime.now(timezone.utc))).status_code == 503
        assert client.get("/healthz").status_code == 200
    # A newer telemetry schema fails closed, not the whole release service.
    with app.state.analytics.connection() as c:
        c.execute("INSERT INTO schema_version VALUES(999)")
    again = create_app(Settings(path, ORIGIN))
    assert again.state.analytics is None
    with TestClient(again, base_url=ORIGIN) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/updates/check?current_version=0.3.0", headers={"X-Installation-Id": str(uuid4())}).status_code == 204
