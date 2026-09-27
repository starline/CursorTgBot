from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import time

from desktop.paths import env_path, is_frozen, project_root, runtime_dir
from desktop.platform import popen_kwargs

logger = logging.getLogger(__name__)

_BOT_MODULE = re.compile(r"(?:^|\s)-m\s+bot(?:\s|$)")
_FROZEN_BOT = re.compile(r"(?:^|\s)--bot(?:\s|$)")


def _is_bot_command(args: str) -> bool:
    if _BOT_MODULE.search(args):
        return True
    return "CursorTgBot" in args and _FROZEN_BOT.search(args) is not None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def find_bot_pids() -> list[int]:
    """Other bot processes (source `python -m bot` or frozen `--bot`)."""
    me = os.getpid()
    found: list[int] = []
    if sys.platform == "win32":
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and "
            "($_.CommandLine -match '(^|\\s)-m bot(\\s|$)' -or "
            "($_.CommandLine -match 'CursorTgBot' -and $_.CommandLine -match '(^|\\s)--bot(\\s|$)')) } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                errors="replace",
                timeout=20,
                **{k: v for k, v in popen_kwargs().items() if k == "creationflags"},
            )
        except (OSError, subprocess.SubprocessError):
            return []
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != me:
                    found.append(pid)
        return found

    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        args = parts[1]
        if pid == me:
            continue
        if _is_bot_command(args):
            found.append(pid)
    return found


class BotSupervisor:
    """Start and stop one local bot process. The child is `python -m bot`."""

    def __init__(self) -> None:
        self.log_path = runtime_dir() / "bot.log"
        self._pid_path = runtime_dir() / "bot.pid"
        self._stop_path = runtime_dir() / "stop.request"
        self._proc: subprocess.Popen[str] | None = None
        self._log_handle = None
        self.exit_code: int | None = None
        self._adopt_existing()

    @property
    def pid(self) -> int | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid
        stored = self._read_pid()
        if stored is not None and pid_alive(stored):
            return stored
        return None

    @property
    def running(self) -> bool:
        return self.pid is not None

    def start(self) -> None:
        if self.running:
            return
        self._stop_path.unlink(missing_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._note("Запускаю бота…")
        child_env = os.environ.copy()
        child_env["CURSOR_TG_ENV"] = str(env_path())
        child_env["BOT_DATA_DIR"] = str(runtime_dir())
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        command = self._bot_command()
        self._log_handle = open(self.log_path, "a", encoding="utf-8", buffering=1)
        try:
            self._proc = subprocess.Popen(
                command,
                cwd=str(project_root()) if not is_frozen() else None,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                **popen_kwargs(),
            )
        except OSError:
            self._log_handle.close()
            self._log_handle = None
            raise
        self._write_pid(self._proc.pid)
        self.exit_code = None

    def stop(self) -> None:
        pid = self.pid
        if pid is None:
            return
        self._note("Останавливаю бота…")
        try:
            self._stop_path.write_text("stop\n", encoding="utf-8")
        except OSError:
            logger.exception("Could not write stop request")
        self._signal(pid, graceful=True)
        if not self._wait_until_dead(pid, timeout=8):
            self._signal(pid, graceful=False)
            self._wait_until_dead(pid, timeout=3)
        self._proc = None
        self._clear_pid()
        self._stop_path.unlink(missing_ok=True)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._note("Бот остановлен.")

    def poll(self) -> None:
        if self._proc is None:
            stored = self._read_pid()
            if stored is not None and not pid_alive(stored):
                self._clear_pid()
            return
        code = self._proc.poll()
        if code is None:
            return
        self.exit_code = code
        self._proc = None
        self._clear_pid()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._note(f"Бот завершился (код {code}).")

    def _bot_command(self) -> list[str]:
        if is_frozen():
            return [sys.executable, "--bot"]
        return [self._source_python(), "-m", "bot"]

    def _source_python(self) -> str:
        root = project_root()
        if sys.platform == "win32":
            candidate = root / ".venv" / "Scripts" / "python.exe"
        else:
            candidate = root / ".venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
        return sys.executable

    def _adopt_existing(self) -> None:
        stored = self._read_pid()
        if stored is not None and pid_alive(stored):
            return
        self._clear_pid()
        foreign = find_bot_pids()
        if foreign:
            self._write_pid(foreign[0])
            self._note(f"Подключён уже запущенный бот (pid {foreign[0]}).")

    def _signal(self, pid: int, *, graceful: bool) -> None:
        if sys.platform == "win32":
            if graceful and self._proc is not None and self._proc.pid == pid:
                try:
                    self._proc.send_signal(signal.CTRL_BREAK_EVENT)
                    return
                except (OSError, ValueError, AttributeError):
                    pass
            flag = "/T" if graceful else "/F"
            subprocess.run(
                ["taskkill", flag, "/PID", str(pid)],
                check=False,
                capture_output=True,
                **popen_kwargs(),
            )
            return
        try:
            os.kill(pid, signal.SIGTERM if graceful else signal.SIGKILL)
        except OSError:
            logger.info("Process %s is already gone", pid)

    def _wait_until_dead(self, pid: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.pid == pid:
                if self._proc.poll() is not None:
                    return True
            elif not pid_alive(pid):
                return True
            time.sleep(0.2)
        return not pid_alive(pid)

    def _note(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} desktop: {text}\n"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _read_pid(self) -> int | None:
        try:
            raw = self._pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw.isdigit():
            return None
        return int(raw)

    def _write_pid(self, pid: int) -> None:
        self._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._pid_path.write_text(str(pid), encoding="utf-8")

    def _clear_pid(self) -> None:
        self._pid_path.unlink(missing_ok=True)
