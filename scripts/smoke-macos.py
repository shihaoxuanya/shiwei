"""Mount the actual DMG, copy its app and validate native startup in isolation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, text=True, capture_output=True, **kwargs)


def worker_roundtrip(executable: Path, library: Path, requests: list[tuple[str, dict]]):
    wire = "".join(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": str(i), "method": method, "params": params}, ensure_ascii=False) + "\n" for i, (method, params) in enumerate(requests + [("shutdown", {})]))
    result = run(executable, input=wire, timeout=180, env={**os.environ, "SHIWEI_DATA_DIR": str(library), "HF_HUB_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"})
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    responses = {item["id"]: item for item in messages if item.get("id") is not None}
    assert not any("error" in response for response in responses.values()), "Packaged Worker RPC failed"
    return [responses[str(i)]["result"] for i in range(len(requests))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("A real macOS desktop is required; browser preview is not a substitute")
    args.evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shiwei-mac-qa-") as temporary:
        temp = Path(temporary)
        mount = temp / "volume"
        mount.mkdir()
        process = None
        worker_pid = None
        mounted = False
        report = {"status": "failed", "testerGatekeeperCheck": "pending", "sourceDrawerFinderInteraction": "pending"}
        try:
            run("hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, args.dmg.resolve(), timeout=120)
            mounted = True
            app = temp / "Applications/拾微.app"
            app.parent.mkdir()
            run("ditto", mount / "拾微.app", app, timeout=180)
            run("codesign", "--verify", "--deep", "--strict", app)
            worker = app / "Contents/MacOS/shiwei-ai-worker"
            run(sys.executable, ROOT / "scripts/smoke-pdf-worker.py", "--worker", worker, timeout=300)
            run(sys.executable, ROOT / "scripts/smoke-answer-fidelity.py", "--worker", worker, timeout=300)
            library = temp / "persistent-test-library"
            note = worker_roundtrip(worker, library, [("create_note", {})])[0]["note"]
            worker_roundtrip(worker, library, [("update_note", {"noteId": note["id"], "title": "2025年9月3日会议", "content": "Oracle 至 TiDB 迁移会议。单次38~50天，增量7~14天。"})])
            restored = worker_roundtrip(worker, library, [("get_note", {"noteId": note["id"]})])[0]["note"]
            assert "38~50" in restored["content"] and restored["title"] == "2025年9月3日会议"
            with (app / "Contents/Info.plist").open("rb") as file:
                info = plistlib.load(file)
            main_exe = app / "Contents/MacOS" / info["CFBundleExecutable"]
            # Compile the small window probe once. It inspects no other PID and
            # needs no accessibility scripting or screen capture permission.
            probe = temp / "window-probe"
            run("swiftc", ROOT / "scripts/mac-window-probe.swift", "-o", probe, timeout=120)
            with (temp / "native.log").open("w") as log:
                process = subprocess.Popen([str(main_exe)], stdout=log, stderr=log, env={**os.environ, "SHIWEI_DATA_DIR": str(temp / "ui-library"), "SHIWEI_TELEMETRY_DISABLED": "1", "HF_HUB_OFFLINE": "1"})
                deadline = time.monotonic() + 45
                window_count = 0
                while time.monotonic() < deadline:
                    assert process.poll() is None, "Native application exited during startup"
                    children = subprocess.run(["pgrep", "-P", str(process.pid)], capture_output=True, text=True)
                    for value in children.stdout.split():
                        command = run("ps", "-p", value, "-o", "comm=").stdout.strip()
                        if command == str(worker):
                            worker_pid = int(value)
                    window_count = int(run(probe, process.pid).stdout.strip())
                    if worker_pid and window_count:
                        break
                    time.sleep(1)
                assert worker_pid and window_count, "Actual native window and bundled Worker must both start"
                # Quit only our copied app, using the ordinary macOS application event.
                app_literal = str(app).replace('\\', '\\\\').replace('"', '\\"')
                run("osascript", "-e", f'tell application "{app_literal}" to quit', timeout=30)
                process.wait(timeout=30)
                for _ in range(20):
                    result = subprocess.run(["kill", "-0", str(worker_pid)], capture_output=True)
                    if result.returncode:
                        break
                    time.sleep(0.25)
                assert result.returncode, "Worker leaked after normal application quit"
            report.update(status="passed", dmgMounted=True, copiedApplication=True, nativeWindowCount=window_count, packagedWorker=True, pdfAndOfflineOCR=True, answerFidelity=True, noteRestartPersistence=True, workerExitedOnQuit=True)
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            if worker_pid:
                existing = subprocess.run(["ps", "-p", str(worker_pid), "-o", "comm="], capture_output=True, text=True)
                if existing.stdout.strip() == str(worker):
                    subprocess.run(["kill", "-TERM", str(worker_pid)], capture_output=True)
            if mounted:
                run("hdiutil", "detach", mount, timeout=60)
            (args.evidence / "native-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
