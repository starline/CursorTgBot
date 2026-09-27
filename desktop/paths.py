from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "CursorTgBot"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Writable settings directory. Source runs keep `.env` in the repo."""
    if not is_frozen():
        return project_root()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "cursortgbot"


def env_path() -> Path:
    if is_frozen():
        return config_dir() / ".env"
    return project_root() / ".env"


def example_env_path() -> Path:
    if is_frozen():
        bundled = Path(getattr(sys, "_MEIPASS", project_root()))
        return bundled / ".env.example"
    return project_root() / ".env.example"


def runtime_dir() -> Path:
    """Logs, pid, and UI prefs. Honors BOT_DATA_DIR from the env file."""
    path = env_path()
    custom = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("BOT_DATA_DIR="):
                custom = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if custom:
        return Path(custom).expanduser().resolve()
    if is_frozen():
        return config_dir() / "data"
    return project_root() / "data"


def ensure_runtime() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    runtime_dir().mkdir(parents=True, exist_ok=True)
    target = env_path()
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    example = example_env_path()
    if example.exists():
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target.write_text(
            "TELEGRAM_BOT_TOKEN=\nCURSOR_API_KEY=\n# REPO_CWD=\n",
            encoding="utf-8",
        )
