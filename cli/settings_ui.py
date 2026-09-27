"""Settings that used to live in the desktop window, edited from the terminal."""

from __future__ import annotations

import getpass
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import Settings, env_file_path, looks_like_project, read_env_values, upsert_env_values

_ROOT = Path(__file__).resolve().parents[1]
_APP_ID = "CursorTgBot"
_LINUX_DESKTOP_NAME = "cursortgbot.desktop"
_MAC_LABEL = "com.cursortgbot.app"


@dataclass
class BotForm:
    telegram_token: str = ""
    cursor_api_key: str = ""
    repo_cwd: str = ""
    model: str = "auto"


@dataclass
class UiPrefs:
    start_bot_on_launch: bool = False


def load_form() -> BotForm:
    values = read_env_values()
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
        }
    )


def validate_form(form: BotForm) -> str | None:
    if not form.telegram_token.strip():
        return "Нужен Telegram token. Его даёт @BotFather."
    if not form.cursor_api_key.strip():
        return "Нужен Cursor API key (Dashboard → API Keys)."
    raw = form.repo_cwd.strip()
    if not raw:
        return "Укажи папку репозитория."
    repo = Path(raw).expanduser()
    if not repo.is_dir():
        return f"Папка не найдена:\n{repo}"
    if not looks_like_project(repo):
        return f"В папке нет .git или AGENTS.md:\n{repo}"
    return None


def mask_secret(value: str) -> str:
    value = value.strip()
    if not value:
        return "—"
    if len(value) <= 4:
        return "•" * len(value)
    return "••••••••" + value[-4:]


def prefs_path(data_dir: Path) -> Path:
    return data_dir / "ui.json"


def load_prefs(data_dir: Path) -> UiPrefs:
    path = prefs_path(data_dir)
    if not path.exists():
        return UiPrefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UiPrefs()
    if not isinstance(raw, dict):
        return UiPrefs()
    return UiPrefs(start_bot_on_launch=bool(raw.get("start_bot_on_launch", False)))


def save_prefs(data_dir: Path, prefs: UiPrefs) -> None:
    path = prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"start_bot_on_launch": prefs.start_bot_on_launch}, indent=2) + "\n",
        encoding="utf-8",
    )


def _python() -> str:
    if sys.platform == "win32":
        candidate = _ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = _ROOT / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _bot_argv() -> list[str]:
    return [_python(), "-m", "bot"]


_BOT_CMD = re.compile(r"(?:^|\s)-m\s+bot(?:\s|$)")


def find_bot_pids() -> list[int]:
    me = os.getpid()
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[int] = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid == me:
            continue
        if _BOT_CMD.search(parts[1]):
            found.append(pid)
    return found


def bot_status(data_dir: Path) -> str:
    del data_dir
    pids = find_bot_pids()
    if not pids:
        return "остановлен"
    return "запущен · pid " + ", ".join(str(pid) for pid in pids)


def start_bot(data_dir: Path) -> str:
    pids = find_bot_pids()
    if pids:
        return "Уже запущен · pid " + ", ".join(str(pid) for pid in pids)
    error = validate_form(load_form())
    if error:
        return error
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "stop.request").unlink(missing_ok=True)
    log_path = data_dir / "bot.log"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["BOT_DATA_DIR"] = str(data_dir)
    log = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            _bot_argv(),
            cwd=str(_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return f"Не удалось запустить бота: {exc}"
    finally:
        log.close()
    (data_dir / "bot.pid").write_text(str(proc.pid), encoding="utf-8")
    return f"Бот запущен · pid {proc.pid}"


def stop_bot(data_dir: Path) -> str:
    pids = find_bot_pids()
    if not pids:
        return "Бот не запущен."
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        (data_dir / "stop.request").write_text("stop\n", encoding="utf-8")
    except OSError as exc:
        return f"Не удалось остановить бота: {exc}"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and find_bot_pids():
        time.sleep(0.3)
    for pid in find_bot_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and find_bot_pids():
        time.sleep(0.2)
    left = find_bot_pids()
    (data_dir / "bot.pid").unlink(missing_ok=True)
    if left:
        return "Бот ещё работает · pid " + ", ".join(str(pid) for pid in left)
    return "Бот остановлен."


def log_tail(data_dir: Path, *, lines: int = 40) -> str:
    path = data_dir / "bot.log"
    if not path.is_file():
        return "Лога ещё нет."
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = text[-lines:]
    return "\n".join(chunk) if chunk else "Лог пуст."


def start_bot_if_pref(settings: Settings) -> str | None:
    """Desktop checkbox: start the Telegram bot when the app opens."""
    if not load_prefs(settings.data_dir).start_bot_on_launch:
        return None
    if validate_form(load_form()) is not None:
        return None
    if find_bot_pids():
        return None
    return start_bot(settings.data_dir)


def supports_login_autostart() -> bool:
    return sys.platform in {"win32", "linux", "darwin"}


def login_autostart_enabled() -> bool:
    if sys.platform == "linux":
        return _linux_desktop_path().exists()
    if sys.platform == "darwin":
        return _mac_plist_path().exists()
    if sys.platform == "win32":
        return _win_autostart_command() is not None
    return False


def set_login_autostart(enabled: bool) -> None:
    if sys.platform == "linux":
        _linux_set_autostart(enabled)
        return
    if sys.platform == "darwin":
        _mac_set_autostart(enabled)
        return
    if sys.platform == "win32":
        _win_set_autostart(enabled)
        return
    raise OSError("Автозапуск при входе на этой системе недоступен")


def _linux_desktop_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "autostart" / _LINUX_DESKTOP_NAME


def _quote(part: str) -> str:
    if part == "" or any(char in part for char in " \t"):
        return '"' + part.replace('"', '\\"') + '"'
    return part


def _linux_set_autostart(enabled: bool) -> None:
    path = _linux_desktop_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(_quote(part) for part in _bot_argv())
    path.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=Cursor Telegram Bot",
                f"Exec={exec_line}",
                f"Path={_ROOT}",
                "Terminal=false",
                "X-GNOME-Autostart-enabled=true",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"


def _mac_set_autostart(enabled: bool) -> None:
    import plistlib

    path = _mac_plist_path()
    if not enabled:
        if path.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False)
            path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": _MAC_LABEL,
        "ProgramArguments": _bot_argv(),
        "WorkingDirectory": str(_ROOT),
        "RunAtLoad": True,
    }
    path.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=False)


def _win_autostart_command() -> str | None:
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
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

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        if enabled:
            command = subprocess.list2cmdline(_bot_argv())
            winreg.SetValueEx(key, _APP_ID, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, _APP_ID)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def _on_off(enabled: bool) -> str:
    return "вкл" if enabled else "выкл"


def _print_screen(form: BotForm, prefs: UiPrefs, data_dir: Path) -> None:
    try:
        autostart = _on_off(login_autostart_enabled()) if supports_login_autostart() else "недоступен"
    except OSError:
        autostart = "не удалось прочитать"
    print()
    print("Настройки")
    print(f"Файл: {env_file_path()}")
    print()
    print(f"  1  Telegram token          {mask_secret(form.telegram_token)}")
    print(f"  2  Cursor API key          {mask_secret(form.cursor_api_key)}")
    print(f"  3  Папка репозитория       {form.repo_cwd or '—'}")
    print(f"  4  Модель                  {form.model or 'auto'}")
    print(f"  5  Старт при входе         {autostart}")
    print(f"  6  Запускать бота с tgBot  {_on_off(prefs.start_bot_on_launch)}")
    print()
    print(f"  Бот: {bot_status(data_dir)}")
    print()
    print("  s запустить    x остановить    l лог    q назад")
    print("Первый, кто напишет боту в личке, получает доступ.")
    print()


def _ask(label: str, current: str, *, secret: bool) -> str | None:
    shown = mask_secret(current) if secret else (current or "—")
    print(f"{label} сейчас: {shown}")
    try:
        if secret:
            raw = getpass.getpass("Новое значение (пусто = не менять): ")
        else:
            raw = input("Новое значение (пусто = не менять): ")
    except EOFError:
        print()
        return None
    if not raw.strip():
        return current
    return raw.strip()


def _apply_live(settings: Settings, form: BotForm) -> None:
    settings.telegram_token = form.telegram_token.strip()
    settings.cursor_api_key = form.cursor_api_key.strip()
    settings.model = form.model.strip() or "auto"


def _commit(settings: Settings, form: BotForm) -> bool:
    error = validate_form(form)
    if error:
        print(error)
        return False
    try:
        save_form(form)
    except OSError as exc:
        print(f"Не удалось сохранить: {exc}")
        return False
    _apply_live(settings, form)
    saved_repo = str(Path(form.repo_cwd).expanduser().resolve())
    print(f"Сохранено в {env_file_path()}")
    if saved_repo != str(settings.repo_cwd.resolve()):
        print(f"Папка в .env: {saved_repo}")
        print("Эта сессия tgBot остаётся в текущей папке до перезапуска.")
    if find_bot_pids():
        print("Перезапусти бота, чтобы он увидел изменения.")
    return True


def run_settings(settings: Settings) -> None:
    form = load_form()
    prefs = load_prefs(settings.data_dir)
    while True:
        _print_screen(form, prefs, settings.data_dir)
        try:
            choice = input("› ").strip().lower()
        except EOFError:
            print()
            return
        if choice in {"q", "quit", "exit", ""}:
            if choice == "":
                continue
            return
        if choice == "1":
            nxt = _ask("Telegram token", form.telegram_token, secret=True)
            if nxt is None:
                return
            previous = form.telegram_token
            form.telegram_token = nxt
            if not _commit(settings, form):
                form.telegram_token = previous
        elif choice == "2":
            nxt = _ask("Cursor API key", form.cursor_api_key, secret=True)
            if nxt is None:
                return
            previous = form.cursor_api_key
            form.cursor_api_key = nxt
            if not _commit(settings, form):
                form.cursor_api_key = previous
        elif choice == "3":
            nxt = _ask("Папка репозитория", form.repo_cwd, secret=False)
            if nxt is None:
                return
            previous = form.repo_cwd
            form.repo_cwd = nxt
            if not _commit(settings, form):
                form.repo_cwd = previous
        elif choice == "4":
            nxt = _ask("Модель", form.model, secret=False)
            if nxt is None:
                return
            previous = form.model
            form.model = nxt or "auto"
            if not _commit(settings, form):
                form.model = previous
        elif choice == "5":
            if not supports_login_autostart():
                print("Автозапуск при входе на этой системе недоступен.")
                continue
            enabled = not login_autostart_enabled()
            try:
                set_login_autostart(enabled)
            except OSError as exc:
                print(f"Не удалось изменить автозапуск: {exc}")
            else:
                print("Старт при входе: " + _on_off(enabled))
        elif choice == "6":
            prefs.start_bot_on_launch = not prefs.start_bot_on_launch
            try:
                save_prefs(settings.data_dir, prefs)
            except OSError as exc:
                prefs.start_bot_on_launch = not prefs.start_bot_on_launch
                print(f"Не удалось сохранить: {exc}")
            else:
                print("Запускать бота с tgBot: " + _on_off(prefs.start_bot_on_launch))
        elif choice == "s":
            print(start_bot(settings.data_dir))
        elif choice == "x":
            print(stop_bot(settings.data_dir))
        elif choice == "l":
            print(log_tail(settings.data_dir))
        else:
            print("Нет такого пункта. Цифра 1–6, s, x, l или q.")
