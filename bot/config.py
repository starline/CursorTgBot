from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BOT_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_PATH = _BOT_ROOT / ".env.example"


def env_file_path() -> Path:
    """`.env` path. CURSOR_TG_ENV overrides it when set."""
    raw = (os.getenv("CURSOR_TG_ENV") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _BOT_ROOT / ".env"


def format_env_value(value: str) -> str:
    if any(char in value for char in "\n\r"):
        raise ValueError("env value contains a newline")
    if value == "" or any(char in value for char in " #'\""):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_env_values(path: Path | None = None) -> dict[str, str]:
    from dotenv import dotenv_values

    target = path or env_file_path()
    if not target.exists():
        return {}
    raw = dotenv_values(target)
    return {key: val for key, val in raw.items() if key and val is not None}


def upsert_env_values(values: dict[str, str], *, path: Path | None = None) -> None:
    """Create or update KEY=value lines. Other lines and comments stay."""
    target = path or env_file_path()
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    for key, value in values.items():
        line = f"{key}={format_env_value(value)}"
        pattern = re.compile(rf"^(?:export\s+)?{re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

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
    upsert_env_values({key: value})
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
    target = env_file_path()
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if _EXAMPLE_PATH.exists():
        target.write_text(_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target.write_text(
            "TELEGRAM_BOT_TOKEN=\nCURSOR_API_KEY=\n# REPO_CWD=\n",
            encoding="utf-8",
        )
    print(f"Created {target}", file=sys.stderr)


def looks_like_project(path: Path) -> bool:
    return (path / ".git").exists() or (path / "AGENTS.md").exists()


def interactive_setup(*, start_cwd: Path | None = None, require_telegram: bool = True) -> None:
    """
    Fill missing required settings interactively when stdin is a TTY.
    Optional vars keep defaults (repo = start cwd, model = auto, …).
    CLI mode only asks for the Cursor API key.
    """
    ensure_env_file()
    load_dotenv(env_file_path(), override=False)

    required = _REQUIRED if require_telegram else ("CURSOR_API_KEY",)
    missing = [k for k in required if not (os.getenv(k) or "").strip()]
    if not missing:
        return

    if not sys.stdin.isatty():
        raise SystemExit(
            "Missing config: "
            + ", ".join(missing)
            + f". Edit {env_file_path()} or run ./run.sh in a terminal."
        )

    title = "Cursor Telegram Bot — quick setup" if require_telegram else "Cursor CLI — quick setup"
    print(title, file=sys.stderr)
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

    if require_telegram and not (os.getenv("REPO_CWD") or "").strip():
        default_repo = str((start_cwd or Path.cwd()).resolve())
        chosen = _prompt("Path to target git repo", default=default_repo)
        if chosen:
            _upsert_env("REPO_CWD", chosen)

    print(f"\nSaved {env_file_path()}", file=sys.stderr)
    if require_telegram and not (os.getenv("ALLOWED_USER_IDS") or "").strip():
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


def load_settings(
    *,
    start_cwd: Path | None = None,
    require_telegram: bool = True,
    repo_override: Path | None = None,
    prefer_start_cwd: bool = False,
) -> Settings:
    start = start_cwd
    if start is None:
        raw_start = (os.getenv("CURSOR_TG_START_CWD") or "").strip()
        start = Path(raw_start).expanduser().resolve() if raw_start else Path.cwd()

    interactive_setup(start_cwd=start, require_telegram=require_telegram)
    load_dotenv(env_file_path(), override=True)

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    api_key = (os.getenv("CURSOR_API_KEY") or "").strip()
    if require_telegram and not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required (.env)")
    if not api_key:
        raise SystemExit("CURSOR_API_KEY is required (.env)")

    allowed_users = _parse_id_set(os.getenv("ALLOWED_USER_IDS") or "")
    # Empty allowlist: first user who messages the bot is auto-added (see try_claim_first_user).

    repo_raw = (os.getenv("REPO_CWD") or "").strip()
    if repo_override is not None:
        repo_cwd = repo_override.expanduser().resolve()
    elif prefer_start_cwd and looks_like_project(start):
        repo_cwd = start.resolve()
    elif not repo_raw:
        repo_cwd = start.resolve()
        logger.info("REPO_CWD not set — using start directory: %s", repo_cwd)
    else:
        repo_cwd = Path(repo_raw).expanduser().resolve()
        if prefer_start_cwd and repo_cwd != start.resolve():
            logger.info("Start directory is not a project — using REPO_CWD: %s", repo_cwd)
    if not repo_cwd.is_dir():
        raise SystemExit(f"REPO_CWD is not a directory: {repo_cwd}")
    if not looks_like_project(repo_cwd):
        raise SystemExit(
            f"Not a project root (no .git or AGENTS.md): {repo_cwd}\n"
            "Run from the repo, set REPO_CWD, or pass --cwd PATH."
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
