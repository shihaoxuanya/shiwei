import time
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from shiwei_control.app import PASSWORDS, Settings, create_app, digest
from shiwei_control.storage import now
from test_releases import ValidStorage, artifact

ORIGIN = "https://control.example.test"
PASSWORD = " synthetic password with spaces "


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path / "api.db", ORIGIN, release_token_hash=digest("test-ci-only")), ValidStorage())
    with app.state.store.connection(write=True) as c:
        c.execute("INSERT INTO admins VALUES(?,?,?,?)", ("admin", "test@example.test", PASSWORDS.hash(PASSWORD), now()))
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def login(client):
    r = client.post("/api/auth/login", json={"email": "test@example.test", "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert r.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf"]}


def test_auth_csrf_and_logout(client):
    assert client.get("/api/admin/releases").status_code == 401
    assert client.post("/internal/releases/artifact-ready", json=artifact().model_dump()).status_code == 401
    headers = login(client)
    assert client.get("/api/admin/releases").status_code == 200
    assert client.post("/api/admin/releases", json={"version": "0.3.1"}).status_code == 403
    assert client.post("/api/admin/releases", json={"version": "0.3.1"}, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/admin/releases", json={"version": "0.3.1"}, headers=headers).status_code == 200
    assert client.post("/api/admin/logout", headers=headers).status_code == 200
    assert client.get("/api/admin/releases").status_code == 401


def test_cookies_and_password_whitespace(client):
    wrong = client.post("/api/auth/login", json={"email": "test@example.test", "password": PASSWORD.strip()}, headers={"Origin": ORIGIN})
    assert wrong.status_code == 401
    r = client.post("/api/auth/login", json={"email": "test@example.test", "password": PASSWORD}, headers={"Origin": ORIGIN})
    cookie = r.headers["set-cookie"]
    for part in ("__Host-shiwei_session", "Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert part in cookie
    assert "password" not in r.json() and "password_hash" not in r.json()


def test_origin_login_rate_limit_validation_no_leaks(client):
    data = {"email": "test@example.test", "password": "sensitive-test"}
    assert client.post("/api/auth/login", json=data).status_code == 403
    for _ in range(5):
        assert client.post("/api/auth/login", json=data, headers={"Origin": ORIGIN}).status_code == 401
    assert client.post("/api/auth/login", json=data, headers={"Origin": ORIGIN}).status_code == 429
    r = client.post("/api/auth/login", json={"email": "bad", "password": "sensitive-test"}, headers={"Origin": ORIGIN})
    assert r.status_code == 422 and "sensitive-test" not in r.text


def test_ready_public_check_pause_audit_and_no_installation_storage(client):
    r = client.post("/internal/releases/artifact-ready", json=artifact().model_dump(), headers={"Authorization": "Bearer test-ci-only"})
    assert r.status_code == 200 and r.json()["status"] == "READY"
    headers = login(client)
    row = r.json()
    query = "/api/v1/updates/check?current_version=0.3.0&channel=stable"
    identifier = str(uuid4())
    assert client.get(query, headers={"X-Installation-Id": identifier}).status_code == 204
    action = {"action": "publish", "revision": row["revision"], "confirm_version": "0.3.1", "reason": "集成测试"}
    r = client.post("/api/admin/releases/0.3.1/actions", headers=headers, json=action)
    assert r.status_code == 200
    row = r.json()
    check = client.get(query, headers={"X-Installation-Id": identifier})
    assert check.status_code == 200 and check.json()["version"] == "0.3.1"
    assert check.headers["cache-control"] == "no-store"
    assert "artifact_url" not in check.json() and check.json()["url"].startswith("https://github.com/")
    r = client.post("/api/admin/releases/0.3.1/actions", headers=headers, json={**action, "action": "pause", "revision": row["revision"]})
    assert r.status_code == 200
    assert client.get(query, headers={"X-Installation-Id": identifier}).status_code == 204
    detail = client.get("/api/admin/releases/0.3.1").json()
    assert len(detail["audit"]) == 3
    assert identifier not in str(detail)
    with client.app.state.store.connection() as c:
        dump = "\n".join(c.iterdump())
        assert identifier not in dump and PASSWORD not in dump


def test_limits_host_tls_and_docs(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/healthz", headers={"Host": "evil.test"}).status_code == 400
    assert client.get("http://control.example.test/healthz").status_code == 400
    assert client.post("/api/auth/login", content=b"x" * 32769).status_code == 413
    assert client.get("/api/v1/updates/check?current_version=bad").status_code == 400
    assert client.get("/api/v1/updates/check?current_version=0.3.0&channel=beta").status_code == 204
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/static/app.js").status_code == 200
    assert client.get("/admin/static/manage.py").status_code == 404
    assert "unsafe-inline" not in client.get("/admin").headers["content-security-policy"]


def test_expired_session(client):
    login(client)
    with client.app.state.store.connection(write=True) as c:
        c.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get("/api/admin/session").status_code == 401


@pytest.mark.parametrize("origin", ["http://public.example", "https://user:pass@example.test", "https://example.test/path", "https://example.test/?token=bad"])
def test_production_settings_reject_unsafe_origins(tmp_path, origin):
    with pytest.raises(ValueError):
        Settings(tmp_path / "test.db", origin)


def test_development_mode_cannot_disable_security_on_public_host(tmp_path):
    with pytest.raises(ValueError):
        Settings(tmp_path / "test.db", "https://public.example", development=True)
