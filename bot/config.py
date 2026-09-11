from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BOT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _BOT_ROOT / ".env"
_EXAMPLE_PATH = _BOT_ROOT / ".env.example"

logger = logging.getLogger(__name__)

# Keys the user must provide; everything else has a default.
_REQUIRED = ("TELEGRAM_BOT_TOKEN", "CURSOR_API_KEY")


def _parse_id_set(raw: str) -> set[int]:
    values: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        values.add(int(part))
    return values


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _upsert_env(key: str, value: str) -> None:
    """Create or update KEY=value in .env (preserves other lines/comments)."""
    text = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^(?:export\s+)?{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    _ENV_PATH.write_text(text, encoding="utf-8")
    os.environ[key] = value


def _prompt(label: str, *, default: str = "", secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{hint}: ").strip()
    except EOFError:
        raw = ""
    if not raw:
        return default
    if secret and raw:
        print("(ok)")
    return raw


def ensure_env_file() -> None:
    """Create .env from the example template if missing."""
    if _ENV_PATH.exists():
        return
    if _EXAMPLE_PATH.exists():
        _ENV_PATH.write_text(_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        _ENV_PATH.write_text(
            "TELEGRAM_BOT_TOKEN=\nCURSOR_API_KEY=\n# REPO_CWD=\n",
            encoding="utf-8",
        )
    print(f"Created {_ENV_PATH}", file=sys.stderr)


def interactive_setup(*, start_cwd: Path | None = None) -> None:
    """
    Fill missing required settings interactively when stdin is a TTY.
    Optional vars keep defaults (repo = start cwd, model = auto, …).
    """
    ensure_env_file()
    load_dotenv(_ENV_PATH, override=False)

    missing = [k for k in _REQUIRED if not (os.getenv(k) or "").strip()]
    if not missing:
        return

    if not sys.stdin.isatty():
        raise SystemExit(
            "Missing config: "
            + ", ".join(missing)
            + f". Edit {_ENV_PATH} or run ./run.sh in a terminal."
        )

    print("Cursor Telegram Bot — quick setup", file=sys.stderr)
    print("Only required values are asked; the rest use defaults.\n", file=sys.stderr)

    if "TELEGRAM_BOT_TOKEN" in missing:
        token = _prompt("Telegram bot token (from @BotFather)")
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required")
        _upsert_env("TELEGRAM_BOT_TOKEN", token)

    if "CURSOR_API_KEY" in missing:
        key = _prompt("Cursor API key (Dashboard → API Keys)", secret=True)
        if not key:
            raise SystemExit("CURSOR_API_KEY is required")
        _upsert_env("CURSOR_API_KEY", key)

    if not (os.getenv("REPO_CWD") or "").strip():
        default_repo = str((start_cwd or Path.cwd()).resolve())
        chosen = _prompt("Path to target git repo", default=default_repo)
        if chosen:
            _upsert_env("REPO_CWD", chosen)

    print(f"\nSaved {_ENV_PATH}", file=sys.stderr)
    if not (os.getenv("ALLOWED_USER_IDS") or "").strip():
        print(
            "ALLOWED_USER_IDS пуст — первый, кто напишет боту, получит доступ автоматически.\n",
            file=sys.stderr,
        )
    else:
        print(file=sys.stderr)


@dataclass
class Settings:
    telegram_token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    cursor_api_key: str = ""
    repo_cwd: Path = field(default_factory=Path.cwd)
    model: str = "auto"
    data_dir: Path = field(default_factory=lambda: _BOT_ROOT / "data")
    # Forum (topics): /task creates a new topic; follow-ups stay in that topic
    forum_mode: bool = False
    forum_chat_id: int | None = None

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

    def try_claim_first_user(self, user_id: int) -> bool:
        """
        If the allowlist is empty, grant access to this user and persist to .env.
        Returns True if the user is (now) allowed.
        """
        if user_id in self.allowed_user_ids:
            return True
        if self.allowed_user_ids:
            return False
        self.allowed_user_ids.add(user_id)
        _upsert_env("ALLOWED_USER_IDS", str(user_id))
        logger.info("First user claimed allowlist: %s (written to .env)", user_id)
        return True


def load_settings(*, start_cwd: Path | None = None) -> Settings:
    start = start_cwd
    if start is None:
        raw_start = (os.getenv("CURSOR_TG_START_CWD") or "").strip()
        start = Path(raw_start).expanduser().resolve() if raw_start else Path.cwd()

    interactive_setup(start_cwd=start)
    load_dotenv(_ENV_PATH, override=True)

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    api_key = (os.getenv("CURSOR_API_KEY") or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required (.env)")
    if not api_key:
        raise SystemExit("CURSOR_API_KEY is required (.env)")

    allowed_users = _parse_id_set(os.getenv("ALLOWED_USER_IDS") or "")
    # Empty allowlist: first user who messages the bot is auto-added (see try_claim_first_user).

    repo_raw = (os.getenv("REPO_CWD") or "").strip()
    if not repo_raw:
        repo_cwd = start.resolve()
        logger.info("REPO_CWD not set — using start directory: %s", repo_cwd)
    else:
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
        allowed_chat_ids=frozenset(_parse_id_set(os.getenv("ALLOWED_CHAT_IDS") or "")),
        cursor_api_key=api_key,
        repo_cwd=repo_cwd,
        model=(os.getenv("CURSOR_MODEL") or "auto").strip() or "auto",
        data_dir=data_dir,
        forum_mode=forum_mode,
        forum_chat_id=forum_chat_id,
    )
