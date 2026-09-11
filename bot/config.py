from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BOT_ROOT = Path(__file__).resolve().parent.parent


def _parse_id_set(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        values.add(int(part))
    return frozenset(values)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    cursor_api_key: str
    repo_cwd: Path
    model: str
    data_dir: Path
    # Forum (topics): /task creates a new topic; follow-ups stay in that topic
    forum_mode: bool
    forum_chat_id: int | None

    def is_user_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if not self.allowed_user_ids:
            return False
        return user_id in self.allowed_user_ids

    def is_chat_allowed(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids


def load_settings() -> Settings:
    load_dotenv(_BOT_ROOT / ".env")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    api_key = (os.getenv("CURSOR_API_KEY") or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required (.env)")
    if not api_key:
        raise SystemExit("CURSOR_API_KEY is required (.env)")

    allowed_users = _parse_id_set(os.getenv("ALLOWED_USER_IDS") or "")
    # Empty allowlist is allowed so you can DM the bot once, read your user id from
    # the denial message, then set ALLOWED_USER_IDS and restart.

    repo_raw = (os.getenv("REPO_CWD") or "").strip()
    if not repo_raw:
        raise SystemExit("REPO_CWD is required (.env) — absolute path to the target git repo")
    repo_cwd = Path(repo_raw).expanduser().resolve()
    if not repo_cwd.is_dir():
        raise SystemExit(f"REPO_CWD is not a directory: {repo_cwd}")
    if not (repo_cwd / ".git").exists() and not (repo_cwd / "AGENTS.md").exists():
        raise SystemExit(
            f"REPO_CWD does not look like a project root (no .git or AGENTS.md): {repo_cwd}"
        )

    data_dir = Path(os.getenv("BOT_DATA_DIR") or (_BOT_ROOT / "data")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    forum_mode = _env_bool("FORUM_MODE", default=False)
    forum_chat_raw = (os.getenv("FORUM_CHAT_ID") or "").strip()
    forum_chat_id = int(forum_chat_raw) if forum_chat_raw else None
    if forum_mode and forum_chat_id is None:
        allowed = _parse_id_set(os.getenv("ALLOWED_CHAT_IDS") or "")
        if len(allowed) == 1:
            forum_chat_id = next(iter(allowed))
        # else: allow boot without FORUM_CHAT_ID so /info can discover it

    return Settings(
        telegram_token=token,
        allowed_user_ids=allowed_users,
        allowed_chat_ids=_parse_id_set(os.getenv("ALLOWED_CHAT_IDS") or ""),
        cursor_api_key=api_key,
        repo_cwd=repo_cwd,
        model=(os.getenv("CURSOR_MODEL") or "composer-2.5").strip(),
        data_dir=data_dir,
        forum_mode=forum_mode,
        forum_chat_id=forum_chat_id,
    )
