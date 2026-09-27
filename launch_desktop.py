"""Desktop entry. On Windows: CursorTgBot.bat. Elsewhere: python launch_desktop.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _reexec_into_venv() -> None:
    """Use the project venv when it exists, so the window and the bot share dependencies."""
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parent
    if sys.platform == "win32":
        preferred = root / ".venv" / "Scripts" / "pythonw.exe"
        fallback = root / ".venv" / "Scripts" / "python.exe"
    else:
        preferred = root / ".venv" / "bin" / "python"
        fallback = preferred
    target = preferred if preferred.exists() else fallback
    if not target.exists():
        return
    if Path(sys.executable).resolve() == target.resolve():
        return
    os.execv(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> None:
    _reexec_into_venv()
    try:
        from desktop.__main__ import main as run

        run()
    except ModuleNotFoundError as exc:
        print(
            "Не хватает зависимостей. Установи их командой:\n"
            "  python -m pip install -r requirements-desktop.txt",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
