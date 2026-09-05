"""Isolated loopback-only UI fixture. NEVER deploy or use with production data."""
import base64
import tempfile
from pathlib import Path
import uvicorn
from shiwei_control.app import PASSWORDS, Settings, create_app
from shiwei_control.models import Artifact
from shiwei_control.storage import now


class SyntheticStorage:
    def verify(self, artifact):
        # Small synthetic metadata, not an actual installer. UI QA only.
        pass


directory = Path(tempfile.mkdtemp(prefix="shiwei-admin-ui-qa-"))
app = create_app(Settings(directory / "control.db", "http://127.0.0.1:8923", development=True), SyntheticStorage())
with app.state.store.connection(write=True) as c:
    c.execute("INSERT INTO admins VALUES(?,?,?,?)", ("qa", "qa@example.test", PASSWORDS.hash("synthetic-ui-qa-only"), now()))
app.state.releases.ready(Artifact(version="0.3.1", artifact_url="https://github.com/shihaoxuanya/shiwei-releases/releases/download/v0.3.1/Shiwei_0.3.1_x64-setup.exe", signature=base64.b64encode(b"untrusted comment: synthetic UI fixture").decode(), sha256="a" * 64, size=409600000, release_notes="【隔离界面测试数据，非真实发行版】\n改进自然语言回忆与回答保真。\n修复数字区间显示，保持原始笔记不变。"))
uvicorn.run(app, host="127.0.0.1", port=8923, access_log=False, log_level="warning")
