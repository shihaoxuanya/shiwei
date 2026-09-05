"""Release-only control plane; never opens the local knowledge database."""
import hashlib
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .artifacts import GitHubReleaseProvider
from .models import Action, Artifact, Draft, Login
from .releases import Releases, find
from .storage import Store, now

PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
STATIC = Path(__file__).parent / "static"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Settings:
    database: Path
    origin: str
    release_token_hash: str = ""
    repository: str = "shihaoxuanya/shiwei-releases"
    public_key: str = ""
    development: bool = False
    analytics_dashboard: str = ""

    def __post_init__(self):
        url = urlsplit(self.origin)
        local = self.development and url.hostname in ("localhost", "127.0.0.1") and url.scheme == "http"
        if self.development and url.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("开发模式仅允许回环地址")
        if (url.scheme != "https" and not local) or not url.hostname or url.path not in ("", "/") or url.query or url.fragment or url.username or url.password:
            raise ValueError("需配置 HTTPS Origin；仅显式本地开发允许 HTTP")
        self.origin = self.origin.rstrip("/")
        if self.release_token_hash and not re.fullmatch(r"[a-f0-9]{64}", self.release_token_hash):
            raise ValueError("发布令牌配置必须为 SHA256")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", self.repository):
            raise ValueError("无效发布仓库")
        if self.analytics_dashboard:
            u = urlsplit(self.analytics_dashboard)
            if u.scheme != "https" or not u.hostname or u.username or u.password:
                raise ValueError("指标入口需要 HTTPS")

    @classmethod
    def environment(cls):
        return cls(Path(os.environ.get("SHIWEI_CONTROL_DB", "data/control.db")),
                   os.environ.get("SHIWEI_CONTROL_ORIGIN", "https://zhishimanghe.com"),
                   os.environ.get("SHIWEI_RELEASE_TOKEN_SHA256", ""),
                   os.environ.get("SHIWEI_RELEASE_REPOSITORY", "shihaoxuanya/shiwei-releases"),
                   os.environ.get("SHIWEI_UPDATER_PUBLIC_KEY", ""),
                   os.environ.get("SHIWEI_CONTROL_DEV") == "1",
                   os.environ.get("SHIWEI_ANALYTICS_DASHBOARD", ""))


class Limiter:
    """Single-process MVP limiter; bounded, ephemeral keys, no request logging."""
    def __init__(self):
        self.items, self.lock = OrderedDict(), threading.Lock()

    def allow(self, key, limit, period):
        with self.lock:
            stamp = time.monotonic()
            start, count = self.items.pop(key, (stamp, 0))
            if stamp - start >= period:
                start, count = stamp, 0
            self.items[key] = (start, count + 1)
            if len(self.items) > 10000:
                self.items.popitem(last=False)
            return count < limit


def create_app(settings=None, provider=None):
    settings = settings or Settings.environment()
    store = Store(settings.database)
    releases = Releases(store, provider or GitHubReleaseProvider(settings.repository, settings.public_key))
    limiter, verifying, hashing = Limiter(), threading.BoundedSemaphore(1), threading.BoundedSemaphore(2)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    app.state.store, app.state.releases = store, releases
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname])
    cookie = "shiwei_session" if settings.development else "__Host-shiwei_session"

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if not settings.development and request.url.scheme != "https":
            return JSONResponse({"detail": "仅支持 HTTPS"}, status_code=400)
        key = digest(request.client.host if request.client else "unknown")
        if not limiter.allow("ip:" + key, 120, 60):
            return JSONResponse({"detail": "请求过于频繁"}, 429, headers={"Retry-After": "60"})
        if request.method not in ("GET", "HEAD"):
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 32768:
                    return JSONResponse({"detail": "请求过大"}, 413)
            request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse({"detail": "服务暂不可用，请稍后重试"}, 503)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic errors can contain submitted passwords/tokens. Never serialize them.
        return JSONResponse({"detail": "参数格式不正确或包含不允许的字段"}, 422)

    def same_origin(request):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "请求来源不匹配")

    def session(request: Request):
        token = request.cookies.get(cookie, "")
        with store.connection() as c:
            row = c.execute("SELECT s.*,a.email FROM sessions s JOIN admins a ON a.id=s.admin_id WHERE s.id_hash=? AND s.expires>?", (digest(token), time.time())).fetchone()
        if row is None:
            raise HTTPException(401, "请先登录")
        if request.method not in ("GET", "HEAD"):
            same_origin(request)
            if not secrets.compare_digest(row["csrf"], request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "会话校验失败，请刷新后重试")
        return dict(row)

    def ci_auth(request: Request):
        value = request.headers.get("authorization", "")
        if not settings.release_token_hash or not value.startswith("Bearer ") or not secrets.compare_digest(digest(value[7:]), settings.release_token_hash):
            raise HTTPException(401, "发布身份验证失败")

    admin = APIRouter(prefix="/api/admin", dependencies=[Depends(session)])
    internal = APIRouter(prefix="/internal/releases", dependencies=[Depends(ci_auth)])

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(data: Login, request: Request, response: Response):
        same_origin(request)
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow("login:" + digest(ip), 5, 300) or not limiter.allow("account:" + digest(data.email), 10, 300):
            raise HTTPException(429, "登录尝试过多，请稍后再试", headers={"Retry-After": "300"})
        if not hashing.acquire(blocking=False):
            raise HTTPException(503, "登录繁忙，请稍后再试")
        try:
            with store.connection() as c:
                row = c.execute("SELECT * FROM admins WHERE email=?", (data.email,)).fetchone()
            try:
                valid = PASSWORDS.verify(row["password_hash"] if row else DUMMY_HASH, data.password)
            except VerificationError:
                valid = False
            if not valid or row is None:
                raise HTTPException(401, "邮箱或密码不正确")
            rehash = PASSWORDS.hash(data.password) if PASSWORDS.check_needs_rehash(row["password_hash"]) else None
        finally:
            hashing.release()
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with store.connection(write=True) as c:
            c.execute("DELETE FROM sessions WHERE expires<? OR admin_id=?", (time.time(), row["id"]))
            c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), row["id"], csrf, time.time() + 28800))
            if rehash:
                c.execute("UPDATE admins SET password_hash=? WHERE id=?", (rehash, row["id"]))
            store.audit(c, row["id"], "login", None, None, None, "管理员登录")
        response.set_cookie(cookie, token, max_age=28800, secure=not settings.development, httponly=True, samesite="strict", path="/")
        return {"email": row["email"], "csrf": csrf}

    @admin.get("/session")
    def current_session(s=Depends(session)):
        return {"email": s["email"], "csrf": s["csrf"]}

    @admin.post("/logout")
    def logout(response: Response, s=Depends(session)):
        with store.connection(write=True) as c:
            c.execute("DELETE FROM sessions WHERE id_hash=?", (s["id_hash"],))
        response.delete_cookie(cookie, path="/", secure=not settings.development, httponly=True, samesite="strict")
        return {"ok": True}

    @admin.get("/releases")
    def listing():
        with store.connection() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM releases ORDER BY created_at DESC LIMIT 200")]
            target = c.execute("SELECT target_id FROM channel_targets WHERE channel='stable'").fetchone()
        return {"releases": rows, "target_id": target[0] if target else None, "analytics_dashboard": settings.analytics_dashboard or None}

    @admin.post("/releases")
    def draft(data: Draft, s=Depends(session)):
        return releases.draft(data, s["admin_id"])

    @admin.get("/releases/{v}")
    def detail(v: str):
        with store.connection() as c:
            row = find(c, v)
            audits = [dict(r) for r in c.execute("SELECT * FROM admin_audit_logs WHERE release_version=? ORDER BY timestamp DESC LIMIT 100", (v,))]
        return {"release": row, "audit": audits}

    @admin.post("/releases/{v}/actions")
    def action(v: str, data: Action, s=Depends(session)):
        return releases.change(v, data, s["admin_id"])

    @internal.post("/artifact-ready")
    def ready(data: Artifact):
        if not verifying.acquire(blocking=False):
            raise HTTPException(503, "正在验证其他资源，请稍后重试", headers={"Retry-After": "60"})
        try:
            return releases.ready(data)
        finally:
            verifying.release()

    @app.get("/api/v1/updates/check")
    def check(request: Request, current_version: str, channel: str = "stable"):
        if channel != "stable":
            return Response(status_code=204)
        try:
            result = releases.check(current_version, request.headers.get("x-installation-id", ""))
        except ValueError:
            raise HTTPException(400, "版本或安装标识格式不正确") from None
        return result if result else Response(status_code=204)

    @app.get("/")
    @app.get("/admin")
    def shell():
        return Response((STATIC / "index.html").read_text(encoding="utf-8"), media_type="text/html")

    @app.get("/admin/static/{name}")
    def asset(name: str):
        types = {"app.js": "text/javascript", "style.css": "text/css"}
        if name not in types:
            raise HTTPException(404)
        return Response((STATIC / name).read_text(encoding="utf-8"), media_type=types[name])

    app.include_router(admin)
    app.include_router(internal)
    return app
