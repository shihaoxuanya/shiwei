from __future__ import annotations

import hashlib
import errno
import os
import shutil
from pathlib import Path
from uuid import uuid4

COPY_DISK_RESERVE_BYTES = 64 * 1024 * 1024


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

        # Reserve room for the database and derived metadata as well as the
        # immutable original. Checking the destination volume also covers a
        # relocated library; an existing managed copy needs no extra space.
        if shutil.disk_usage(self.root).free < source.stat().st_size + COPY_DISK_RESERVE_BYTES:
            raise OSError(errno.ENOSPC, "资料库所在磁盘空间不足，请释放空间或更改资料库位置后重试（需容纳文件并预留 64MB）")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        except OSError as error:
            if error.errno == errno.ENOSPC:
                raise OSError(errno.ENOSPC, "保存资料时磁盘空间不足，请释放空间或更改资料库位置后重试") from error
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return destination
