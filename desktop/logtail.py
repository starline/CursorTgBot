from __future__ import annotations

from pathlib import Path


class LogTail:
    """Read appended bytes from a log file. Starts near the end."""

    def __init__(self, path: Path, *, history_bytes: int = 32_000) -> None:
        self.path = path
        self._pos = 0
        self._ready = False
        self._history_bytes = history_bytes

    def read_new(self) -> str:
        if not self.path.exists():
            return ""
        size = self.path.stat().st_size
        drop_partial = False
        if not self._ready:
            self._pos = max(0, size - self._history_bytes)
            drop_partial = self._pos > 0
            self._ready = True
        elif self._pos > size:
            self._pos = 0
        with self.path.open("rb") as handle:
            handle.seek(self._pos)
            raw = handle.read()
            self._pos = handle.tell()
        if drop_partial:
            cut = raw.find(b"\n")
            if cut != -1:
                raw = raw[cut + 1 :]
        return raw.decode("utf-8", errors="replace")
