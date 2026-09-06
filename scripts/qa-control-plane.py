"""Isolated loopback-only UI fixture. NEVER deploy or use with production data."""
import base64
import argparse
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import uvicorn
from shiwei_control.app import PASSWORDS, Settings, create_app
from shiwei_control.models import Artifact
from shiwei_control.storage import now
from shiwei_control.analytics import Event


class SyntheticStorage:
    def verify(self, artifact):
        # Small synthetic metadata, not an actual installer. UI QA only.
        pass


directory = Path(tempfile.mkdtemp(prefix="shiwei-admin-ui-qa-"))
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8923)
parser.add_argument("--empty", action="store_true")
args = parser.parse_args()
app = create_app(Settings(directory / "control.db", f"http://127.0.0.1:{args.port}", development=True,
                          analytics_salt="synthetic-local-ui-qa-000000000000000000"), SyntheticStorage())
with app.state.store.connection(write=True) as c:
    c.execute("INSERT INTO admins VALUES(?,?,?,?)", ("qa", "qa@example.test", PASSWORDS.hash("synthetic-ui-qa-only"), now()))
app.state.releases.ready(Artifact(version="0.3.1", artifact_url="https://github.com/shihaoxuanya/shiwei-releases/releases/download/v0.3.1/Shiwei_0.3.1_x64-setup.exe", signature=base64.b64encode(b"untrusted comment: synthetic UI fixture").decode(), sha256="a" * 64, size=409600000, release_notes="【隔离界面测试数据，非真实发行版】\n改进自然语言回忆与回答保真。\n修复数字区间显示，保持原始笔记不变。"))
if not args.empty:
    identifiers = [str(uuid4()) for _ in range(12)]
    base = datetime.now(timezone.utc)
    for day in range(35, -1, -1):
        at = base - timedelta(days=day)
        app.state.analytics.clock = lambda: at.timestamp()
        for n, identifier in enumerate(identifiers):
            if (day + n) % 3 == 0:
                continue
            events = [("app_opened", {}), ("question_asked", {}),
                      ("retrieval_succeeded" if n % 4 else "retrieval_abstained", {}),
                      ("import_completed", {"success_count": 3}), ("citation_clicked", {})]
            if n == 4:
                events.append(("app_error", {"error_type": "worker_timeout"}))
                events.append(("import_failed", {"failure_count": 1}))
            for event, properties in events:
                app.state.analytics.ingest(Event.model_validate({
                    "schema_version": 1, "event_id": str(uuid4()), "installation_id": identifier,
                    "event": event, "app_version": "0.3.1" if n < 9 else "0.3.0", "os": "windows",
                    "architecture": "x86_64", "timestamp": at.isoformat(), "properties": properties,
                }))
    import time
    app.state.analytics.clock = time.time
uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
