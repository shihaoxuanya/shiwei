from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def destination(self, content_hash: str, suffix: str) -> Path:
        normalized_suffix = suffix.lower() if suffix else ".bin"
        return self.root / content_hash[:2] / f"{content_hash}{normalized_suffix}"

    def copy(self, source: Path, content_hash: str) -> Path:
        destination = self.destination(content_hash, source.suffix)
        if destination.exists():
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
