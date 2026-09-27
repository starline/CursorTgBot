from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, TextIO

from bot.agent_runner import AgentRunner, RunNotice
from bot.backlog_view import is_backlog_list_request, load_tasks
from bot.config import Settings
from bot.store import SessionKey
from cli.settings_ui import find_bot_pids, run_settings, start_bot, stop_bot


def workspace_chat_id(workspace: Path) -> int:
    """Stable negative id so each directory has its own agent, apart from Telegram chats."""
    digest = hashlib.sha256(str(workspace.resolve()).encode()).digest()
    number = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
    return -(number + 2)

HELP = """\
/help                 команды
/new                  новая сессия, как в боте
/cancel               отменить текущий run
/status               занят или свободен
/diff                 git status и diff --stat
/backlog [deferred]   задачи бэклога
/model [id]           показать или сменить модель
/settings             токен, ключ, репозиторий, модель, автозапуск, бот
/start                запустить Telegram-бота
/stop                 остановить Telegram-бота
/cwd                  репозиторий
/phpunit [args]       scripts/phpunit.sh
/phpstan [args]       scripts/phpstan.sh
/exit                 выход

Ctrl+C во время run — отмена.
Ctrl+C на пустой строке ещё раз — выход.
Ctrl+D — выход.
"""

_QUIET_STATUS = {"Запускаю local agent…"}


def use_color(stream: IO[str] | None) -> bool:
    if os.getenv("NO_COLOR"):
        return False
    return bool(stream is not None and stream.isatty())


class TurnPrinter:
    """Stream assistant text and one-line tool calls."""

    def __init__(
        self,
        *,
        color: bool,
        out: TextIO,
        status: TextIO | None,
        stream_text: bool,
    ) -> None:
        self.color = color
        self.out = out
        self.status = status
        self.stream_text = stream_text
        self.chunks: list[str] = []
        self.seen_tools: set[str] = set()
        self.thinking_shown = False
        self.last_status = ""
        self._at_nl = True

    def _paint(self, code: str, text: str) -> str:
        if not self.color or not text:
            return text
        return f"\033[{code}m{text}\033[0m"

    def _write(self, text: str, *, stream: TextIO | None = None) -> None:
        target = self.out if stream is None else stream
        if not text or target is None:
            return
        target.write(text)
        target.flush()
        if target is self.out:
            self._at_nl = text.endswith("\n")

    def _ensure_nl(self) -> None:
        if not self._at_nl:
            self._write("\n")

    def _status_line(self, text: str, *, code: str = "2") -> None:
        if self.status is None:
            return
        if self.status is self.out:
            self._ensure_nl()
        self.status.write(self._paint(code, text) + "\n")
        self.status.flush()
        if self.status is self.out:
            self._at_nl = True

    async def __call__(self, notice: RunNotice) -> None:
        if notice.kind == "status":
            text = notice.text.strip()
            if not text or text in _QUIET_STATUS or text == self.last_status:
                return
            self.last_status = text
            self._status_line(text)
            return
        if notice.kind == "thinking":
            if self.thinking_shown:
                return
            self.thinking_shown = True
            self._status_line("thinking…")
            return
        if notice.kind == "tool":
            self.thinking_shown = False
            key = notice.call_id or f"{notice.tool}:{notice.text}"
            if key in self.seen_tools:
                return
            if notice.phase == "running" and not notice.text:
                return
            self.seen_tools.add(key)
            label = (notice.tool or "tool").replace("_", " ")
            line = f"  ⏺ {label}"
            if notice.text:
                line += f"  {notice.text}"
            if notice.phase == "error":
                line += "  failed"
            self._status_line(line, code="36")
            return
        if notice.kind == "text" and notice.text:
            self.thinking_shown = False
            self.chunks.append(notice.text)
            if self.stream_text:
                self._write(notice.text)

    def finish(self, final: str) -> None:
        raw = "".join(self.chunks)
        streamed = raw.strip()
        final = (final or "").strip()
        if final == "Отменено.":
            self._ensure_nl()
            self._status_line("Отменено.")
            return
        if not self.stream_text or not streamed:
            if final:
                self._write(final)
            self._ensure_nl()
            return
        if final in {streamed, raw}:
            self._ensure_nl()
            return
        if raw and final.startswith(raw):
            self._write(final[len(raw) :])
            self._ensure_nl()
            return
        self._ensure_nl()
        if final.startswith("Статус:") and "Статус:" not in streamed:
            head, _, tail = final.partition("\n")
            self._write(self._paint("31", head) + "\n")
            rest = tail.strip()
            if rest and rest != streamed:
                self._write(rest + "\n")

    def fail(self, text: str) -> None:
        self._ensure_nl()
        target = self.status or self.out
        target.write(self._paint("31", text) + "\n")
        target.flush()

    def meta(self, text: str) -> None:
        self._status_line(text)


def load_slot(data_dir: Path, workspace: Path, *, continue_session: bool) -> tuple[int, bool]:
    """Return (slot, resumed) for this workspace. A restart continues that directory's session."""
    path = data_dir / "cli_session.json"
    key = str(workspace.resolve())
    workspaces: dict[str, int] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw.get("workspaces") if isinstance(raw, dict) else None
            if isinstance(stored, dict):
                workspaces = {str(name): int(slot) for name, slot in stored.items()}
        except (OSError, ValueError, TypeError):
            workspaces = {}
    slot = workspaces.get(key, 0)
    resumed = continue_session and slot >= 1
    if not resumed:
        slot += 1
    if slot < 1:
        slot = 1
    workspaces[key] = slot
    path.write_text(json.dumps({"workspaces": workspaces}, indent=2) + "\n", encoding="utf-8")
    return slot, resumed


def save_slot(data_dir: Path, workspace: Path, slot: int) -> None:
    path = data_dir / "cli_session.json"
    workspaces: dict[str, int] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw.get("workspaces") if isinstance(raw, dict) else None
            if isinstance(stored, dict):
                workspaces = {str(name): int(value) for name, value in stored.items()}
        except (OSError, ValueError, TypeError):
            workspaces = {}
    workspaces[str(workspace.resolve())] = slot
    path.write_text(json.dumps({"workspaces": workspaces}, indent=2) + "\n", encoding="utf-8")


def session_key(workspace: Path, slot: int) -> SessionKey:
    return (workspace_chat_id(workspace), slot)


def format_backlog(repo: Path, mode: str) -> str:
    tasks = load_tasks(repo, mode=mode)
    if not tasks:
        label = "отложенных" if mode == "deferred" else "активных"
        return f"Backlog\nНет {label} задач."
    lines = [f"Backlog · {len(tasks)}", "P0→P3 · effort S→L"]
    current: str | None = None
    for task in tasks:
        if task.priority != current:
            current = task.priority
            count = sum(1 for item in tasks if item.priority == current)
            lines.append("")
            lines.append(f"—— {current} ({count}) ——")
        lines.append(f"{task.id}  {task.effort} · {task.status}")
        lines.append(task.title)
        lines.append("")
    return "\n".join(lines).rstrip()


def _run_git(cwd: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return ((proc.stdout or "") + (proc.stderr or "")).strip() or "(empty)"


def _run_script(cwd: Path, name: str, args: list[str]) -> str:
    script = cwd / "scripts" / name
    if not script.is_file():
        return f"Нет скрипта: {script}"
    proc = subprocess.run(
        ["bash", str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    body = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"
    return f"exit={proc.returncode}\n{body}"


class _Reader:
    def __init__(self, settings: Settings, history_path: Path) -> None:
        self._settings = settings
        self._history_path = history_path
        self._session = None
        self._fallback = False
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            self._fallback = True
            return
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.key_binding import KeyBindings
        except ImportError:
            self._fallback = True
            return

        bindings = KeyBindings()

        @bindings.add("escape", "enter")
        def _newline(event) -> None:  # type: ignore[no-untyped-def]
            event.current_buffer.insert_text("\n")

        @bindings.add("f2")
        def _start_key(_event) -> None:  # type: ignore[no-untyped-def]
            self._run_bot_action(start_bot)

        @bindings.add("f3")
        def _stop_key(_event) -> None:  # type: ignore[no-untyped-def]
            self._run_bot_action(stop_bot)

        completer = WordCompleter(
            [
                "/help",
                "/new",
                "/clear",
                "/cancel",
                "/status",
                "/diff",
                "/backlog",
                "/backlog deferred",
                "/model",
                "/settings",
                "/start",
                "/stop",
                "/cwd",
                "/phpunit",
                "/phpstan",
                "/exit",
                "/quit",
            ],
            sentence=True,
        )
        from prompt_toolkit.styles import Style

        self._html = HTML
        self._status_at = 0.0
        self._running = False
        self._session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=completer,
            complete_while_typing=True,
            key_bindings=bindings,
            mouse_support=True,
            bottom_toolbar=self._toolbar,
            style=Style.from_dict(
                {
                    "bottom-toolbar": "bg:#2b2b2b #e8e8e8",
                    "start-btn": "bg:#2e7d32 #ffffff bold",
                    "stop-btn": "bg:#b71c1c #ffffff bold",
                }
            ),
        )

    def _bot_running(self) -> bool:
        now = time.monotonic()
        if now - self._status_at > 0.8:
            self._running = bool(find_bot_pids())
            self._status_at = now
        return self._running

    def _run_bot_action(self, action) -> None:  # type: ignore[no-untyped-def]
        from prompt_toolkit.application import get_app, run_in_terminal

        message = action(self._settings.data_dir)
        self._status_at = 0.0

        def _show() -> None:
            print(message)

        run_in_terminal(_show)
        get_app().invalidate()

    def _click(self, action):  # type: ignore[no-untyped-def]
        from prompt_toolkit.mouse_events import MouseEventType

        def handle(mouse_event) -> None:  # type: ignore[no-untyped-def]
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self._run_bot_action(action)

        return handle

    def _toolbar(self) -> list:
        running = self._bot_running()
        state = "запущен" if running else "остановлен"
        return [
            ("class:bottom-toolbar", " "),
            ("class:start-btn", " Старт ", self._click(start_bot)),
            ("class:bottom-toolbar", " "),
            ("class:stop-btn", " Стоп ", self._click(stop_bot)),
            (
                "class:bottom-toolbar",
                f"  бот {state}   {self._settings.model}   {self._settings.repo_cwd} ",
            ),
        ]

    async def read(self) -> str:
        if self._session is None:
            return await asyncio.to_thread(input, "❯ ")
        return await self._session.prompt_async(self._html("<ansigreen><b>❯</b></ansigreen> "))


def _print_banner(settings: Settings, *, continued: bool) -> None:
    color = use_color(sys.stdout)
    title = "Cursor CLI"
    if color:
        title = f"\033[1m{title}\033[0m"
    mode = "продолжение сессии" if continued else "новая сессия"
    print(f"\n{title}  ·  {settings.model}  ·  {mode}")
    print(settings.repo_cwd)
    print("\nСообщение — задача агенту. Кнопка Старт внизу (или F2) запускает Telegram-бота.")
    print("Ctrl+C — отмена run. Ctrl+D — выход. /new — сброс сессии.\n")


async def _run_turn(
    runner: AgentRunner,
    session: SessionKey,
    text: str,
    printer: TurnPrinter,
) -> str:
    async def _ignore(_note: str) -> None:
        return

    result = await runner.enqueue(session, text, _ignore, on_notice=printer)
    printer.finish(result)
    return result


def _arm_sigint(runner: AgentRunner) -> None:
    loop = asyncio.get_running_loop()

    def _on_sigint() -> None:
        sys.stderr.write("\nОтмена…\n")
        sys.stderr.flush()
        asyncio.create_task(runner.cancel_current())

    try:
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
    except (NotImplementedError, RuntimeError, ValueError):
        return


def _disarm_sigint() -> None:
    loop = asyncio.get_running_loop()
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, RuntimeError, ValueError):
        pass


async def execute_turn(
    runner: AgentRunner,
    session: SessionKey,
    text: str,
    *,
    print_mode: bool,
    show_timing: bool,
) -> tuple[str, int]:
    pipe = print_mode and not sys.stdout.isatty()
    status: TextIO | None
    if pipe:
        status = sys.stderr if sys.stderr.isatty() else None
        color = use_color(sys.stderr)
    else:
        status = sys.stdout
        color = use_color(sys.stdout)
    printer = TurnPrinter(
        color=color,
        out=sys.stdout,
        status=status,
        stream_text=not pipe,
    )
    _arm_sigint(runner)
    started = time.monotonic()
    try:
        try:
            result = await _run_turn(runner, session, text, printer)
        except asyncio.CancelledError:
            printer.finish("Отменено.")
            raise
        except KeyboardInterrupt:
            await runner.cancel_current()
            printer.finish("Отменено.")
            return "Отменено.", 130
        except Exception as exc:  # noqa: BLE001
            printer.fail(f"Ошибка: {exc}")
            return str(exc), 1
    finally:
        _disarm_sigint()

    if show_timing and result != "Отменено.":
        elapsed = time.monotonic() - started
        shown = f"{elapsed:.1f}s" if elapsed < 10 else f"{elapsed:.0f}s"
        printer.meta(f"· {shown}")
    if result == "Отменено.":
        return result, 130
    if result.startswith("Статус:"):
        return result, 2
    return result, 0


async def handle_command(
    line: str,
    *,
    settings: Settings,
    runner: AgentRunner,
    slot: int,
) -> tuple[str, int]:
    """Return (action, slot). action is 'exit' | 'continue'."""
    head, _, arg = line.strip().partition(" ")
    cmd = head.lower()
    arg = arg.strip()
    session = session_key(settings.repo_cwd, slot)

    if cmd in {"/exit", "/quit"}:
        return "exit", slot
    if cmd == "/help":
        print(HELP.rstrip())
        print("Alt+Enter — новая строка в сообщении.")
        return "continue", slot
    if cmd == "/cwd":
        print(settings.repo_cwd)
        return "continue", slot
    if cmd == "/model":
        if not arg:
            print(settings.model)
            return "continue", slot
        settings.model = arg
        if runner.has_live_agent(session):
            print(f"Модель: {arg}")
            print("Текущая сессия уже открыта — /clear, чтобы агент точно взял новую модель.")
        else:
            print(f"Модель: {arg}")
        return "continue", slot
    if cmd == "/settings":
        run_settings(settings)
        return "continue", slot
    if cmd == "/start":
        print(start_bot(settings.data_dir))
        return "continue", slot
    if cmd == "/stop":
        print(stop_bot(settings.data_dir))
        return "continue", slot
    if cmd == "/status":
        st = runner.status
        if st.busy:
            print(f"Занят, очередь {st.queue_len}")
        else:
            print(f"Свободен, очередь {st.queue_len}")
        return "continue", slot
    if cmd == "/cancel":
        ok = await runner.cancel_current()
        print("Отмена запрошена." if ok else "Нет активного run.")
        return "continue", slot
    if cmd in {"/clear", "/new"}:
        await runner.drop_session(session)
        slot += 1
        save_slot(settings.data_dir, settings.repo_cwd, slot)
        print("Сессия сброшена.")
        return "continue", slot
    if cmd == "/diff":
        status = await asyncio.to_thread(_run_git, settings.repo_cwd, ["status", "-sb"])
        diff = await asyncio.to_thread(_run_git, settings.repo_cwd, ["diff", "--stat"])
        print(status)
        if diff and diff != "(empty)":
            print()
            print(diff)
        return "continue", slot
    if cmd == "/backlog":
        mode = "deferred" if arg.lower() in {"deferred", "отложенные", "defer"} else "active"
        if arg and mode == "active" and arg.lower() not in {"active", "all", "задачи"}:
            print("Использование: /backlog  или  /backlog deferred")
            return "continue", slot
        print(format_backlog(settings.repo_cwd, mode))
        return "continue", slot
    if cmd == "/phpunit":
        print(await asyncio.to_thread(_run_script, settings.repo_cwd, "phpunit.sh", arg.split()))
        return "continue", slot
    if cmd == "/phpstan":
        print(await asyncio.to_thread(_run_script, settings.repo_cwd, "phpstan.sh", arg.split()))
        return "continue", slot
    print(f"Неизвестная команда: {cmd}. /help")
    return "continue", slot


async def interactive(
    settings: Settings,
    runner: AgentRunner,
    *,
    slot: int,
    continued: bool,
    initial: str,
) -> int:
    _print_banner(settings, continued=continued)
    reader = _Reader(settings, settings.data_dir / "cli_history")
    if initial:
        await execute_turn(
            runner,
            session_key(settings.repo_cwd, slot),
            initial,
            print_mode=False,
            show_timing=True,
        )
        print()

    last_sigint = 0.0
    while True:
        try:
            text = await reader.read()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_sigint < 1.2:
                print()
                return 0
            last_sigint = now
            print("\nЕщё раз Ctrl+C или /exit — выход.")
            continue
        except asyncio.CancelledError:
            print()
            return 130

        text = text.strip()
        if not text:
            continue
        last_sigint = 0.0
        if text.startswith("/"):
            action, slot = await handle_command(
                text,
                settings=settings,
                runner=runner,
                slot=slot,
            )
            if action == "exit":
                return 0
            continue

        mode = is_backlog_list_request(text)
        if mode is not None:
            print(format_backlog(settings.repo_cwd, mode))
            print()
            continue

        try:
            _result, code = await execute_turn(
                runner,
                session_key(settings.repo_cwd, slot),
                text,
                print_mode=False,
                show_timing=True,
            )
        except asyncio.CancelledError:
            print()
            return 130
        if code == 130:
            print()
            continue
        print()
