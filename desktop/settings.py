from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bot.config import read_env_values, upsert_env_values

from desktop.paths import env_path


@dataclass
class BotForm:
    telegram_token: str = ""
    cursor_api_key: str = ""
    repo_cwd: str = ""
    model: str = "auto"


def load_form() -> BotForm:
    values = read_env_values(env_path())
    return BotForm(
        telegram_token=(values.get("TELEGRAM_BOT_TOKEN") or "").strip(),
        cursor_api_key=(values.get("CURSOR_API_KEY") or "").strip(),
        repo_cwd=(values.get("REPO_CWD") or "").strip(),
        model=(values.get("CURSOR_MODEL") or "auto").strip() or "auto",
    )


def save_form(form: BotForm) -> None:
    upsert_env_values(
        {
            "TELEGRAM_BOT_TOKEN": form.telegram_token.strip(),
            "CURSOR_API_KEY": form.cursor_api_key.strip(),
            "REPO_CWD": form.repo_cwd.strip(),
            "CURSOR_MODEL": form.model.strip() or "auto",
        },
        path=env_path(),
    )


def validate_form(form: BotForm) -> str | None:
    if not form.telegram_token.strip():
        return "A Telegram token is required. Get one from @BotFather."
    if not form.cursor_api_key.strip():
        return "A Cursor API key is required (Dashboard → API Keys)."
    repo = Path(form.repo_cwd.strip()).expanduser()
    if not form.repo_cwd.strip():
        return "Choose the git repository folder."
    if not repo.is_dir():
        return f"Folder not found:\n{repo}"
    if not (repo / ".git").exists() and not (repo / "AGENTS.md").exists():
        return f"This folder has no .git or AGENTS.md:\n{repo}"
    return None
