"""OS hooks. Windows is the packaged app; Linux and macOS use the same functions."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from desktop.paths import is_frozen, project_root

_APP_ID = "CursorTgBot"
_LINUX_DESKTOP_NAME = "cursortgbot.desktop"
_MAC_LABEL = "com.cursortgbot.app"


def launch_argv(*, background: bool) -> list[str]:
    """Command that starts this shell, independent of the current working directory."""
    extra = ["--background"] if background else []
    if is_frozen():
        return [sys.executable, *extra]
    root = project_root()
    if sys.platform == "win32":
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        python = root / ".venv" / "Scripts" / "python.exe"
        interpreter = pythonw if pythonw.exists() else python
        if not interpreter.exists():
            interpreter = Path(sys.executable)
    else:
        interpreter = root / ".venv" / "bin" / "python"
        if not interpreter.exists():
            interpreter = Path(sys.executable)
    script = root / "launch_desktop.py"
    return [str(interpreter), str(script), *extra]


def supports_login_autostart() -> bool:
    return sys.platform in {"win32", "linux", "darwin"}


def login_autostart_enabled() -> bool:
    if sys.platform == "win32":
        return _win_autostart_command() is not None
    if sys.platform == "darwin":
        return _mac_plist_path().exists()
    if sys.platform == "linux":
        return _linux_desktop_path().exists()
    return False


def set_login_autostart(enabled: bool) -> None:
    if sys.platform == "win32":
        _win_set_autostart(enabled)
        return
    if sys.platform == "darwin":
        _mac_set_autostart(enabled)
        return
    if sys.platform == "linux":
        _linux_set_autostart(enabled)
        return
    raise OSError("Login startup is not available on this system")


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


def popen_kwargs() -> dict:
    if sys.platform == "win32":
        create_no_window = 0x08000000
        new_group = 0x00000200
        return {"creationflags": create_no_window | new_group}
    return {"start_new_session": True}


def _win_run_key(write: bool):
    import winreg

    access = winreg.KEY_SET_VALUE if write else winreg.KEY_READ
    if write:
        access |= winreg.KEY_READ
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        access,
    )


def _win_autostart_command() -> str | None:
    import winreg

    try:
        key = _win_run_key(write=False)
    except OSError:
        return None
    try:
        value, _ = winreg.QueryValueEx(key, _APP_ID)
    except FileNotFoundError:
        return None
    finally:
        winreg.CloseKey(key)
    return str(value)


def _win_set_autostart(enabled: bool) -> None:
    import winreg

    key = _win_run_key(write=True)
    try:
        if enabled:
            command = subprocess.list2cmdline(launch_argv(background=True))
            winreg.SetValueEx(key, _APP_ID, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, _APP_ID)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def _linux_desktop_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "autostart" / _LINUX_DESKTOP_NAME


def _linux_set_autostart(enabled: bool) -> None:
    path = _linux_desktop_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = launch_argv(background=True)
    exec_line = " ".join(_desktop_quote(part) for part in argv)
    workdir = str(project_root())
    path.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Cursor Telegram Bot",
                f"Exec={exec_line}",
                f"Path={workdir}",
                "Terminal=false",
                "X-GNOME-Autostart-enabled=true",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _desktop_quote(part: str) -> str:
    if part == "":
        return '""'
    if any(char in part for char in " \t"):
        return '"' + part.replace('"', '\\"') + '"'
    return part


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"


def _mac_set_autostart(enabled: bool) -> None:
    path = _mac_plist_path()
    if not enabled:
        if path.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False)
            path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": _MAC_LABEL,
        "ProgramArguments": launch_argv(background=True),
        "WorkingDirectory": str(project_root()),
        "RunAtLoad": True,
    }
    path.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=False)
