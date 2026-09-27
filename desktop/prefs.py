from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UiPrefs:
    start_bot_on_launch: bool = False


def load_prefs(path: Path) -> UiPrefs:
    if not path.exists():
        return UiPrefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UiPrefs()
    if not isinstance(raw, dict):
        return UiPrefs()
    return UiPrefs(start_bot_on_launch=bool(raw.get("start_bot_on_launch", False)))


def save_prefs(path: Path, prefs: UiPrefs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"start_bot_on_launch": prefs.start_bot_on_launch}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
