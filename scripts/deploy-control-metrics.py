"""Run as root on the existing dedicated Ubuntu host, with a verified source directory.

No credentials in arguments/output. This preserves the current release DB, admin,
environment, old source tree and a recoverable configuration backup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def run(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw)


def proxy_privacy_probe(adapted):
    with tempfile.TemporaryDirectory(prefix="shiwei-proxy-privacy-") as directory:
        root = Path(directory)
        with socket.socket() as socket_: 
            socket_.bind(("127.0.0.1", 0))
            port = socket_.getsockname()[1]
        config = {"admin": {"disabled": True}, "logging": adapted["logging"], "apps": {"http": {"servers": {"qa": {
            "listen": [f"127.0.0.1:{port}"], "routes": [{"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "127.0.0.1:9"}]}]}]
        }}}}}
        config_path, log_path = root / "config.json", root / "runtime.log"
        config_path.write_text(json.dumps(config))
        with log_path.open("w") as log:
            process = subprocess.Popen(["caddy", "run", "--config", str(config_path)], stdout=log, stderr=log,
                                       env={**os.environ, "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data")})
            try:
                for _ in range(30):
                    try:
                        request = urllib.request.Request(f"http://127.0.0.1:{port}/synthetic-private-uri", headers={"X-Installation-Id": "synthetic-private-identifier"})
                        urllib.request.urlopen(request, timeout=1)
                    except urllib.error.HTTPError as error:
                        if error.code == 502:
                            break
                        raise
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("Isolated proxy did not become ready")
            finally:
                process.terminate()
                process.wait(timeout=5)
        logs = log_path.read_text()
        assert '"status":502' in logs or '"status": 502' in logs, "No actual proxy error was exercised"
        assert "synthetic-private-uri" not in logs and "synthetic-private-identifier" not in logs
        assert '"request"' not in logs, "Request metadata leaked into runtime logs"
    print("Isolated reverse-proxy failure: request metadata redacted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo on the existing dedicated server")
    source = Path(args.source).resolve(strict=True)
    if source.parent != Path("/opt/shiwei-control/releases") or not (source / "shiwei_control/analytics.py").is_file():
        raise RuntimeError("Unexpected deployment source")
    root = Path("/opt/shiwei-control")
    current = root / "current"
    previous = current.resolve(strict=True)
    if previous.parent != root / "releases":
        raise RuntimeError("Unexpected current release")
    adapted = json.loads(run("caddy", "adapt", "--config", str(source / "deploy/Caddyfile"), "--adapter", "caddyfile").stdout)
    run("caddy", "validate", "--config", str(source / "deploy/Caddyfile"), "--adapter", "caddyfile")
    proxy_privacy_probe(adapted)
    # A fresh, isolated DB is used by every test. No production account/event fixtures.
    result = run(str(root / "test-venv/bin/python"), "-m", "pytest", str(source / "tests"), "-q", cwd=source,
                 env={**os.environ, "PYTHONPATH": str(source)})
    print(result.stdout.strip())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = Path("/var/lib/shiwei-control/backups") / ("pre-metrics-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    env_file = Path("/etc/shiwei-control.env")
    caddy_file = Path("/etc/caddy/Caddyfile")
    for file, name in ((env_file, "control.env"), (caddy_file, "Caddyfile")):
        shutil.copyfile(file, backup / name)
        os.chmod(backup / name, 0o600)
    with sqlite3.connect("file:/var/lib/shiwei-control/control.db?mode=ro", uri=True) as src, sqlite3.connect(backup / "control.db") as dest:
        src.backup(dest)
        expected_admins = src.execute("SELECT count(*) FROM admins").fetchone()[0]
        expected_releases = src.execute("SELECT count(*) FROM releases").fetchone()[0]
    os.chmod(backup / "control.db", 0o600)
    config = env_file.read_text()
    values = dict(line.split("=", 1) for line in config.splitlines() if line and not line.startswith("#") and "=" in line)
    if not values.get("SHIWEI_ANALYTICS_SALT"):
        # Root-only generation; never print the salt or place it in the deployed source.
        lines = [line for line in config.splitlines() if not line.startswith("SHIWEI_ANALYTICS_SALT=")]
        lines.append("SHIWEI_ANALYTICS_SALT=" + secrets.token_hex(32))
        env_file.write_text("\n".join(lines) + "\n")
        os.chmod(env_file, 0o600)
    try:
        run(str(root / "tools/uv"), "pip", "install", "--python", str(root / "venv/bin/python"), "--no-deps", "--offline", "-e", str(source))
        next_link = root / ("next-" + stamp)
        next_link.symlink_to(source, target_is_directory=True)
        next_link.replace(current)
        shutil.copyfile(source / "deploy/Caddyfile", caddy_file)
        run("systemctl", "restart", "shiwei-control")
        run("systemctl", "reload", "caddy")
        for _ in range(30):
            try:
                with urllib.request.urlopen("https://zhishimanghe.com/healthz", timeout=3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("Public HTTPS readiness failed")
        # Negative schema probe must NOT produce a synthetic production usage event.
        request = urllib.request.Request("https://zhishimanghe.com/api/v1/telemetry/events", data=b'{}', headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=5)
            raise RuntimeError("Invalid telemetry unexpectedly accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 422
        with sqlite3.connect("file:/var/lib/shiwei-control/control.db?mode=ro", uri=True) as c:
            assert c.execute("SELECT count(*) FROM admins").fetchone()[0] == expected_admins
            assert c.execute("SELECT count(*) FROM releases").fetchone()[0] == expected_releases
    except Exception:
        restore = root / ("restore-" + stamp)
        restore.symlink_to(previous, target_is_directory=True)
        restore.replace(current)
        shutil.copyfile(backup / "control.env", env_file)
        shutil.copyfile(backup / "Caddyfile", caddy_file)
        run(str(root / "tools/uv"), "pip", "install", "--python", str(root / "venv/bin/python"), "--no-deps", "--offline", "-e", str(previous))
        run("systemctl", "restart", "shiwei-control")
        run("systemctl", "reload", "caddy")
        raise RuntimeError("Deployment failed; previous source/configuration restored") from None
    print(json.dumps({"status": "deployed", "source": str(source), "backup": str(backup), "admins_preserved": True,
                      "releases_preserved": True, "public_https": True, "invalid_telemetry_rejected": True, "synthetic_production_events": 0}))


if __name__ == "__main__":
    main()
