"""Read-only verifier used with ephemeral official Tauri signing fixtures."""
import hashlib
import json
import sys
from pathlib import Path
from .artifacts import verify_minisign


def main():
    try:
        artifact, signature, config = map(Path, sys.argv[1:])
        key = json.loads(config.read_text(encoding="utf-8"))["plugins"]["updater"]["pubkey"]
        verify_minisign(key, signature.read_text(encoding="utf-8").strip(), hashlib.blake2b(artifact.read_bytes(), digest_size=64).digest())
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
