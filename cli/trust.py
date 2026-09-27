from __future__ import annotations

import json
import os
import select
import sys
import termios
import tty
from pathlib import Path


def trust_store_path() -> Path:
    root = os.getenv("XDG_DATA_HOME", "").strip()
    base = Path(root).expanduser() if root else Path.home() / ".local" / "share"
    return base / "tgbot" / "trusted-workspaces.json"


def _load(store: Path) -> set[str]:
    if not store.exists():
        return set()
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    paths = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(paths, list):
        return set()
    return {str(item) for item in paths}


def _save(store: Path, paths: set[str]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": sorted(paths)}
    store.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_trusted(workspace: Path, *, store: Path | None = None) -> bool:
    return str(workspace.resolve()) in _load(store or trust_store_path())


def remember(workspace: Path, *, store: Path | None = None) -> None:
    path = store or trust_store_path()
    paths = _load(path)
    paths.add(str(workspace.resolve()))
    _save(path, paths)


def _width() -> int:
    columns = shutil_columns()
    return max(40, min(columns, 160) - 1)


def shutil_columns() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def trust_lines(workspace: Path, selected: int, width: int) -> list[str]:
    inner = width - 2
    path = str(workspace)
    if len(path) > inner - 4:
        path = "…" + path[-(inner - 5) :]

    def row(text: str) -> str:
        if len(text) > inner:
            text = text[: inner - 1] + "…"
        return "│" + text.ljust(inner) + "│"

    trust_mark = "▶" if selected == 0 else " "
    quit_mark = "▶" if selected == 1 else " "
    return [
        "╭" + "─" * inner + "╮",
        row(""),
        row("  ⚠ Workspace Trust Required"),
        row(""),
        row("  tgBot can execute code and access files in this directory."),
        row(""),
        row("  Do you trust the contents of this directory?"),
        row(""),
        row(f"    {path}"),
        row(""),
        row(""),
        row(f"  {trust_mark} [a] Trust this workspace"),
        row(f"  {quit_mark} [q] Quit"),
        row(""),
        row("  Use arrow keys to navigate, Enter to select, or press the key shown"),
        row(""),
        "╰" + "─" * inner + "╯",
    ]


def _paint(line: str) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return line
    line = line.replace("⚠", "\033[33m⚠\033[0m")
    return line.replace("Workspace Trust Required", "\033[1mWorkspace Trust Required\033[0m")


def _draw(workspace: Path, selected: int, *, redraw: bool) -> int:
    lines = trust_lines(workspace, selected, _width())
    if redraw:
        sys.stdout.write(f"\033[{len(lines)}A")
    sys.stdout.write("\n".join(_paint(line) for line in lines) + "\n")
    sys.stdout.flush()
    return len(lines)


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char != "\x1b":
            return char
        if select.select([sys.stdin], [], [], 0.05)[0]:
            return "\x1b" + sys.stdin.read(2)
        return char
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _prompt(workspace: Path) -> str:
    selected = 0
    sys.stdout.write("\033[?25l")
    try:
        _draw(workspace, selected, redraw=False)
        while True:
            key = _read_key()
            if key in {"q", "Q", "\x03"}:
                return "quit"
            if key in {"a", "A"}:
                return "trust"
            if key in {"\r", "\n"}:
                return "trust" if selected == 0 else "quit"
            nxt = selected
            if key in {"\x1b[A", "\x1b[D"}:
                nxt = 0
            elif key in {"\x1b[B", "\x1b[C"}:
                nxt = 1
            else:
                continue
            if nxt != selected:
                selected = nxt
                _draw(workspace, selected, redraw=True)
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def ensure_trusted(workspace: Path, *, force: bool = False, store: Path | None = None) -> None:
    """Ask once per directory, the way `agent` asks before it runs."""
    if force or is_trusted(workspace, store=store):
        if force:
            remember(workspace, store=store)
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit(
            f"Workspace is not trusted: {workspace}\n"
            "Run tgBot in a terminal and choose Trust, or pass --trust."
        )
    if _prompt(workspace) != "trust":
        print()
        raise SystemExit(0)
    remember(workspace, store=store)
    print()
