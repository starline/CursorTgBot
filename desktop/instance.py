from __future__ import annotations

import sys
from pathlib import Path


class AlreadyRunning(Exception):
    pass


class SingleInstance:
    """Keep a single shell window. The lock file stays open for the process lifetime."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a+b")
        if path.stat().st_size == 0:
            self._fh.write(b"\0")
            self._fh.flush()
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            raise AlreadyRunning() from exc

    def release(self) -> None:
        if self._fh.closed:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
