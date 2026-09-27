from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from bot.agent_runner import AgentRunner
from bot.config import load_settings
from bot.store import SessionStore
from cli.repl import execute_turn, interactive, load_slot, session_key


def _start_cwd() -> Path:
    raw = (os.getenv("CURSOR_TG_START_CWD") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tgBot",
        description="Локальный агент в терминале. Рабочая папка — каталог, из которого запущен.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  tgBot\n"
            "  tgBot -p \"fix the failing test\"\n"
            "  tgBot --new\n"
        ),
    )
    parser.add_argument("prompt", nargs="*", help="Первое сообщение. Без -p остаёшься в сессии.")
    parser.add_argument(
        "-p",
        "--print",
        action="store_true",
        dest="print_mode",
        help="Один запрос, ответ в stdout, затем выход",
    )
    parser.add_argument(
        "-c",
        "--continue",
        action="store_true",
        dest="continue_session",
        help="Продолжить сессию (так и есть по умолчанию)",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Начать новую сессию, как /new",
    )
    parser.add_argument("--model", help="Модель (по умолчанию CURSOR_MODEL или auto)")
    parser.add_argument(
        "--workspace",
        help="Каталог вместо текущей папки (как --workspace у agent)",
    )
    parser.add_argument("--cwd", help="То же, что --workspace")
    return parser.parse_args(argv)


def _repo_override(cwd_arg: str | None, start: Path) -> Path | None:
    if not cwd_arg:
        return None
    raw = Path(cwd_arg).expanduser()
    if not raw.is_absolute():
        raw = start / raw
    return raw.resolve()


async def _amain(args: argparse.Namespace, workspace: Path) -> int:
    settings = load_settings(
        start_cwd=workspace,
        require_telegram=False,
        repo_override=workspace,
    )
    if args.model:
        settings.model = args.model.strip() or settings.model

    prompt = " ".join(args.prompt).strip()
    if args.print_mode and not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if args.print_mode and not prompt:
        print("Нужен промпт: tgBot -p \"…\"  или  текст в stdin.", file=sys.stderr)
        return 2

    store = SessionStore(settings.data_dir / "sessions.sqlite3")
    runner = AgentRunner(settings, store, reply_channel="cli")
    if sys.stderr.isatty():
        print("Подключаюсь к Cursor…", file=sys.stderr)
    try:
        await runner.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось запустить агента: {exc}", file=sys.stderr)
        store.close()
        return 1

    slot, resumed = load_slot(settings.data_dir, workspace, continue_session=not args.new)
    try:
        if args.print_mode:
            _result, code = await execute_turn(
                runner,
                session_key(workspace, slot),
                prompt,
                print_mode=True,
                show_timing=False,
            )
            return code
        return await interactive(
            settings,
            runner,
            slot=slot,
            continued=resumed,
            initial=prompt,
        )
    finally:
        await runner.stop()
        store.close()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    start = _start_cwd()
    workspace = _repo_override(args.workspace or args.cwd, start) or start
    if not workspace.is_dir():
        print(f"Нет такого каталога: {workspace}", file=sys.stderr)
        raise SystemExit(1)
    try:
        code = asyncio.run(_amain(args, workspace))
    except (KeyboardInterrupt, asyncio.CancelledError):
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
