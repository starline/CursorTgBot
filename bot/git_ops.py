"""Worktree diff and an explicit commit for the Telegram action buttons."""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_DIFF_BYTES = 1_000_000
_FILE_DIFF_CAP = 200_000


def commit_message(label: str) -> str:
    line = " ".join(label.split())
    if not line:
        line = "Update from Telegram"
    if len(line) > 72:
        line = line[:71].rstrip() + "…"
    return line


def worktree_patch(cwd: Path) -> str:
    """Unstaged, staged, and untracked changes as one unified diff."""
    parts: list[str] = []
    tracked = _git(cwd, ["diff", "HEAD"])
    if tracked:
        parts.append(tracked)
    others = _git(cwd, ["ls-files", "--others", "--exclude-standard"])
    for name in others.splitlines():
        name = name.strip()
        if not name:
            continue
        chunk = _git(cwd, ["diff", "--no-index", "--", "/dev/null", name], ok_codes=(0, 1))
        if not chunk or len(chunk.encode("utf-8", errors="replace")) > _FILE_DIFF_CAP:
            parts.append(f"\n# new file omitted or too large: {name}\n")
            continue
        parts.append(chunk)
    text = "\n".join(part for part in parts if part).strip()
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= MAX_DIFF_BYTES:
        return text
    cut = raw[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n\n# … обрезано, патч больше 1 МБ\n"


def commit_worktree(cwd: Path, message: str) -> str:
    status = _git(cwd, ["status", "--porcelain"])
    if not status.strip():
        return "Нечего коммитить."
    add = _run(cwd, ["git", "add", "-A"])
    if add.returncode != 0:
        return _clip(add.stderr or add.stdout or "git add не прошёл")
    commit = _run(cwd, ["git", "commit", "-m", message])
    out = ((commit.stdout or "") + (commit.stderr or "")).strip()
    if commit.returncode != 0:
        return _clip(out or "Коммит не создан.")
    return _clip(out or "Коммит создан.")


def _git(cwd: Path, args: list[str], *, ok_codes: tuple[int, ...] = (0,)) -> str:
    proc = _run(cwd, ["git", *args])
    if proc.returncode not in ok_codes:
        return ""
    return (proc.stdout or "").strip()


def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _clip(text: str, limit: int = 3500) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n\n… (обрезано)"
