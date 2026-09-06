"""Fail closed on incomplete, wrong-architecture, or non-portable Mac bundles."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys

MACHO_MAGICS = {bytes.fromhex(value) for value in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def validate_load_commands(text: str) -> None:
    for block in re.split(r"Load command \d+", text):
        if "LC_BUILD_VERSION" in block:
            version = re.search(r"\bminos (\d+)(?:\.(\d+))?", block)
        elif "LC_VERSION_MIN_MACOSX" in block:
            version = re.search(r"\bversion (\d+)(?:\.(\d+))?", block)
        else:
            continue
        if not version:
            raise ValueError("Unrecognized macOS deployment target")
        if (int(version[1]), int(version[2] or 0)) > (14, 0):
            raise ValueError("Native dependency requires a system newer than macOS 14.0")


def validate_dependencies(text: str) -> None:
    for line in text.splitlines()[1:]:
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if dependency and not dependency.startswith(("@rpath/", "@loader_path/", "@executable_path/", "/System/Library/", "/usr/lib/")):
            raise ValueError("Native dependency references a build-machine or unbundled absolute path")


def verify(root: Path) -> dict:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("This verification must run on a native Apple Silicon Mac")
    root = root.resolve(strict=True)
    is_app = root.suffix == ".app"
    worker_dir = root / "Contents/MacOS" if is_app else root
    worker = worker_dir / "shiwei-ai-worker"
    if not worker.is_file() or not (worker_dir / "_internal").is_dir():
        raise ValueError("Worker must be accompanied by its adjacent _internal directory")
    if is_app:
        with (root / "Contents/Info.plist").open("rb") as file:
            info = plistlib.load(file)
        if info.get("LSMinimumSystemVersion") != "14.0":
            raise ValueError("App must declare macOS 14.0 as its minimum system")
        if not (worker_dir / info["CFBundleExecutable"]).is_file():
            raise ValueError("Missing native desktop executable")
        run("codesign", "--verify", "--deep", "--strict", str(root))
    count = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError("Bundle symlink escapes its directory")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as file:
            magic = file.read(4)
        if magic not in MACHO_MAGICS:
            continue
        if "arm64" not in run("lipo", "-archs", str(path)).split():
            raise ValueError("Bundle contains a native binary without ARM64 support")
        validate_load_commands(run("otool", "-arch", "arm64", "-l", str(path)))
        validate_dependencies(run("otool", "-arch", "arm64", "-L", str(path)))
        run("codesign", "--verify", "--strict", str(path))
        count += 1
    if count < 2:
        raise ValueError("Incomplete native runtime")
    return {"status": "passed", "nativeBinaries": count, "architecture": "arm64", "minimumOS": "14.0", "appleNotarized": False}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]))))
