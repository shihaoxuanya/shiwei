"""One OS-level writer lease for SQLite and its adjacent managed files."""
from __future__ import annotations

import os
from pathlib import Path
import stat

LOCK_FILENAME = ".shiwei-library.lock"


class LibraryLockError(RuntimeError):
    code = "LIBRARY_IN_USE"


class LibraryLock:
    def __init__(self, root: Path, *, filename: str = LOCK_FILENAME) -> None:
        path = root / filename
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0)
                                 & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise LibraryLockError("资料库锁文件不是普通文件，请先检查资料库目录。")
        self._file = path.open("a+b")
        try:
            if not path.stat().st_size:
                self._file.write(b"0")
                self._file.flush()
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._file.close()
            raise LibraryLockError("资料库正在被另一个拾微实例使用，请关闭使用同一资料库的其他实例后重试。") from error

    def close(self) -> None:
        if self._file.closed:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Closing the OS handle also releases its lease. An unlock error
            # must not turn a successfully committed relocation into failure.
            pass
        finally:
            self._file.close()

    def __enter__(self) -> LibraryLock:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
