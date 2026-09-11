from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

ACTIVE_STATUSES = frozenset({"proposed", "ready", "doing"})
DEFERRED_STATUSES = frozenset({"deferred"})
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
EFFORT_ORDER = {"S": 0, "M": 1, "L": 2, "XL": 3}

_CARD_HEAD = re.compile(r"^###\s+([A-Z0-9-]+)\s*[·•\-–—]\s*(.+)$", re.M)
_FIELD = re.compile(r"^- \*\*(status|priority|effort|area|when):\*\*\s*(.+)$", re.M)

_LIST_ACTIVE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:покажи|дай|выведи|покажи\s+мне|дай\s+мне)\s+(?:список\s+)?(?:задач\w*|backlog|бэклог\w*)"
    r"|список\s+задач\w*"
    r"|что\s+в\s+(?:backlog|бэклог\w*)"
    r"|active\s+backlog"
    r"|покажи\s+задачи"
    r")\s*[.!?…]*\s*$"
)
_LIST_DEFERRED = re.compile(
    r"(?is)^\s*(?:"
    r".*\b(?:отложенн\w*|deferred)\b.*"
    r")\s*$"
)


@dataclass(frozen=True)
class BacklogTask:
    id: str
    title: str
    priority: str
    effort: str
    status: str
    zone: str


def is_backlog_list_request(text: str) -> str | None:
    """Return 'active' | 'deferred' | None for short list-only prompts."""
    raw = (text or "").strip()
    if not raw or len(raw) > 120:
        return None
    if _LIST_DEFERRED.search(raw) and (
        "список" in raw.lower()
        or "покажи" in raw.lower()
        or "дай" in raw.lower()
        or "что" in raw.lower()
        or "deferred" in raw.lower()
    ):
        return "deferred"
    if _LIST_ACTIVE.match(raw):
        return "active"
    return None


def load_tasks(repo_cwd: Path, *, mode: str = "active") -> list[BacklogTask]:
    backlog = repo_cwd / ".cursor" / "backlog"
    if not backlog.is_dir():
        return []

    wanted = DEFERRED_STATUSES if mode == "deferred" else ACTIVE_STATUSES
    tasks: list[BacklogTask] = []

    for path in sorted(backlog.glob("*.md")):
        name = path.name
        if name == "README.md" or name.endswith(".archive.md"):
            continue
        text = path.read_text(encoding="utf-8")
        for part in re.split(r"(?=^###\s+)", text, flags=re.M):
            hm = _CARD_HEAD.match(part)
            if not hm:
                continue
            fields = dict(_FIELD.findall(part))
            status = fields.get("status", "").strip().lower()
            if status not in wanted:
                continue
            title = hm.group(2).strip()
            title = title.replace("`", "")
            tasks.append(
                BacklogTask(
                    id=hm.group(1).strip(),
                    title=title,
                    priority=fields.get("priority", "P?").strip(),
                    effort=fields.get("effort", "?").strip(),
                    status=status,
                    zone=path.stem,
                )
            )

    tasks.sort(
        key=lambda t: (
            PRIORITY_ORDER.get(t.priority, 99),
            EFFORT_ORDER.get(t.effort, 99),
            t.id,
        )
    )
    return tasks


def format_backlog_html(tasks: list[BacklogTask], *, mode: str = "active") -> list[str]:
    """Build Telegram HTML chunks (UI-friendly, one task block each)."""
    if not tasks:
        label = "отложенных" if mode == "deferred" else "активных"
        return [f"<b>Backlog</b>\nНет {label} задач."]

    if mode == "deferred":
        header = (
            f"<b>Отложенные</b> · {len(tasks)}\n"
            f"<code>deferred</code> · P0→P3 · S→L"
        )
    else:
        header = (
            f"<b>Backlog</b> · {len(tasks)}\n"
            f"<code>proposed</code> / <code>ready</code> / <code>doing</code>\n"
            f"P0→P3 · effort S→L"
        )

    blocks: list[str] = [header]
    current_p: str | None = None
    for task in tasks:
        if task.priority != current_p:
            current_p = task.priority
            count = sum(1 for t in tasks if t.priority == current_p)
            blocks.append(
                f"────────────\n"
                f"<b>{html.escape(current_p)}</b> · {count}"
            )
        blocks.append(
            f"<code>{html.escape(task.id)}</code>  "
            f"<b>{html.escape(task.effort)}</b> · {html.escape(task.status)}\n"
            f"{html.escape(task.title)}"
        )

    return _pack_blocks(blocks, limit=3500)


def _pack_blocks(blocks: list[str], *, limit: int) -> list[str]:
    chunks: list[str] = []
    buf = ""
    for block in blocks:
        candidate = block if not buf else f"{buf}\n\n{block}"
        if buf and len(candidate) > limit:
            chunks.append(buf)
            buf = block
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks
