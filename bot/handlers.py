from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.agent_runner import AgentRunner
from bot.config import Settings
from bot.store import SessionKey

logger = logging.getLogger(__name__)
router = Router()

TG_LIMIT = 3900
TOPIC_NAME_LIMIT = 128


def _truncate(text: str, limit: int = TG_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n\n… (обрезано)"


def _auth(settings: Settings, message: Message) -> bool:
    user = message.from_user
    if user is None or not settings.is_user_allowed(user.id):
        return False
    if not settings.is_chat_allowed(message.chat.id):
        return False
    return True


async def _deny(message: Message) -> None:
    await message.answer("Нет доступа.")


def _thread_id(message: Message) -> int:
    return int(message.message_thread_id or 0)


def _session_key(message: Message) -> SessionKey:
    return (message.chat.id, _thread_id(message))


def _is_in_topic(message: Message) -> bool:
    """True when message is inside a forum topic (not General / not DM)."""
    tid = message.message_thread_id
    if tid is None:
        return False
    # General topic uses is_topic_message=False in some clients; trust is_topic_message when set
    if message.is_topic_message is False:
        return False
    return bool(message.is_topic_message) or tid > 1


def _topic_name(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.replace("\n", " ")
    if not cleaned:
        cleaned = "task"
    if len(cleaned) > TOPIC_NAME_LIMIT:
        cleaned = cleaned[: TOPIC_NAME_LIMIT - 1].rstrip() + "…"
    return cleaned


async def _reply(
    message: Message,
    text: str,
    *,
    thread_id: int | None = None,
) -> Message:
    """Reply in-place. message.answer already keeps the source thread — do not pass it again."""
    if thread_id is not None:
        return await message.bot.send_message(
            chat_id=message.chat.id,
            text=text,
            message_thread_id=thread_id or None,
        )
    return await message.answer(text)


class TypingKeepalive:
    """Keep Telegram 'typing…' visible until stop (action expires ~5s)."""

    def __init__(self, bot: Bot, chat_id: int, thread_id: int | None = None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id if thread_id else None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> TypingKeepalive:
        self._task = asyncio.create_task(self._loop(), name="tg-typing")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                logger.debug("typing task failed", exc_info=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._bot.send_chat_action(
                    chat_id=self._chat_id,
                    action="typing",
                    message_thread_id=self._thread_id,
                )
            except Exception:  # noqa: BLE001
                logger.debug("send_chat_action failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=4.0)
            except TimeoutError:
                continue


def _fit_edit(text: str, limit: int = TG_LIMIT) -> str:
    """Fit growing transcript into one Telegram message (keep the tail)."""
    if len(text) <= limit:
        return text
    return "…\n" + text[-(limit - 2) :]


async def _edit_progress(status_msg: Message, text: str) -> bool:
    try:
        await status_msg.edit_text(_fit_edit(text))
        return True
    except Exception:  # noqa: BLE001
        logger.debug("progress edit failed", exc_info=True)
        return False


async def _finish_status(status_msg: Message, text: str) -> None:
    """Prefer editing the status message; fall back to a new reply if edit fails."""
    if await _edit_progress(status_msg, text):
        return
    try:
        await status_msg.answer(_fit_edit(text))
    except Exception:  # noqa: BLE001
        logger.exception("failed to deliver final status")


@router.message(Command("start", "help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    forum_hint = (
        "\n\nForum mode: /task → new topic; text and /ask inside a topic = follow-up."
        if settings.forum_mode
        else ""
    )
    await _reply(
        message,
        "Cursor local agent bot.\n\n"
        "/task <text> — start a task"
        + (" (new topic)" if settings.forum_mode else "")
        + "\n"
        "/ask <text> — follow-up in the current topic/chat\n"
        "/info — chat/group id and metadata\n"
        "/status — queue / run\n"
        "/cancel — cancel the current run\n"
        "/diff — git status + diff --stat\n"
        "/new — reset the agent for this session\n"
        "/phpunit [args]\n"
        "/phpstan [args]"
        + forum_hint,
    )


@router.message(Command("info"))
async def cmd_info(message: Message, settings: Settings) -> None:
    # User allowlist only — so you can discover group id before ALLOWED_CHAT_IDS is set
    user = message.from_user
    if user is None or not settings.is_user_allowed(user.id):
        await _deny(message)
        return

    chat = message.chat
    lines = [
        f"chat.id = `{chat.id}`",
        f"chat.type = `{chat.type}`",
        f"chat.title = `{chat.title or '—'}`",
        f"chat.username = `{chat.username or '—'}`",
        f"is_forum = `{bool(getattr(chat, 'is_forum', False))}`",
        f"message_thread_id = `{message.message_thread_id or '—'}`",
        f"is_topic_message = `{message.is_topic_message}`",
        f"user.id = `{user.id}`",
        f"user.username = `@{user.username}`" if user.username else "user.username = —",
        f"FORUM_MODE = `{settings.forum_mode}`",
        f"FORUM_CHAT_ID = `{settings.forum_chat_id or '—'}`",
        f"ALLOWED_CHAT_IDS = `{', '.join(str(x) for x in sorted(settings.allowed_chat_ids)) or '(any)'}`",
    ]
    if chat.type in {"group", "supergroup"}:
        lines.append("")
        lines.append("Для .env:")
        lines.append(f"`FORUM_CHAT_ID={chat.id}`")
        lines.append(f"`ALLOWED_CHAT_IDS={chat.id}`")
    await _reply(message, "\n".join(lines))


@router.message(Command("status"))
async def cmd_status(message: Message, settings: Settings, runner: AgentRunner) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    st = runner.status
    if st.busy and st.current_session:
        chat_id, thread_id = st.current_session
        text = f"Busy: chat={chat_id} thread={thread_id}, queue={st.queue_len}"
    else:
        text = f"Idle, queue={st.queue_len}"
    await _reply(message, text)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, settings: Settings, runner: AgentRunner) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    ok = await runner.cancel_current()
    await _reply(message, "Cancel запрошен." if ok else "Нет активного run.")


@router.message(Command("new"))
async def cmd_new(message: Message, settings: Settings, runner: AgentRunner) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    await runner.drop_session(_session_key(message))
    await _reply(message, "Сессия агента сброшена для этого топика/чата.")


@router.message(Command("diff"))
async def cmd_diff(message: Message, settings: Settings) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    cwd = settings.repo_cwd
    status = await asyncio.to_thread(_run_git, cwd, ["status", "-sb"])
    diff = await asyncio.to_thread(_run_git, cwd, ["diff", "--stat"])
    await _reply(message, _truncate(f"```\n{status}\n\n{diff}\n```"))


@router.message(Command("phpunit"))
async def cmd_phpunit(message: Message, settings: Settings, command: CommandObject) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    args = (command.args or "").split()
    await _reply(message, "PHPUnit…")
    out = await asyncio.to_thread(_run_script, settings.repo_cwd, "phpunit.sh", args)
    await _reply(message, _truncate(out))


@router.message(Command("phpstan"))
async def cmd_phpstan(message: Message, settings: Settings, command: CommandObject) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    args = (command.args or "").split()
    await _reply(message, "PHPStan…")
    out = await asyncio.to_thread(_run_script, settings.repo_cwd, "phpstan.sh", args)
    await _reply(message, _truncate(out))


@router.message(Command("task"))
async def cmd_task(
    message: Message,
    bot: Bot,
    settings: Settings,
    runner: AgentRunner,
    command: CommandObject,
) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    text = (command.args or "").strip()
    if not text:
        await _reply(message, "Нужен текст: /task …")
        return

    if settings.forum_mode:
        if settings.forum_chat_id is None:
            await _reply(
                message,
                "FORUM_MODE=1, но FORUM_CHAT_ID ещё не задан.\n"
                "Напиши /info в группе и пропиши id в .env.",
            )
            return
        await _start_task_in_new_topic(bot, message, settings, runner, text)
    else:
        await _run_agent_task(message, runner, _session_key(message), text)


@router.message(Command("ask"))
async def cmd_ask(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    command: CommandObject,
) -> None:
    if not _auth(settings, message):
        await _deny(message)
        return
    text = (command.args or "").strip()
    if not text:
        await _reply(message, "Нужен текст: /ask …")
        return

    if settings.forum_mode and not _is_in_topic(message):
        await _reply(message, "Follow-up только внутри топика задачи. Новый task: /task …")
        return

    await _run_agent_task(message, runner, _session_key(message), text)


@router.message(F.text & ~F.text.startswith("/"))
async def plain_text(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
) -> None:
    if not _auth(settings, message):
        return
    text = (message.text or "").strip()
    if not text:
        return

    if settings.forum_mode:
        # In forum: plain text only continues an existing task topic
        if not _is_in_topic(message):
            return
        await _run_agent_task(message, runner, _session_key(message), text)
        return

    await _run_agent_task(message, runner, _session_key(message), text)


async def _start_task_in_new_topic(
    bot: Bot,
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    text: str,
) -> None:
    forum_chat_id = settings.forum_chat_id
    assert forum_chat_id is not None

    name = _topic_name(text)
    try:
        topic = await bot.create_forum_topic(chat_id=forum_chat_id, name=name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_forum_topic failed")
        await _reply(message, f"Не удалось создать топик: {exc}")
        return

    thread_id = int(topic.message_thread_id)
    session: SessionKey = (forum_chat_id, thread_id)

    header = f"Задача:\n{text}\n\n"
    status_msg = await bot.send_message(
        chat_id=forum_chat_id,
        message_thread_id=thread_id,
        text=header + "Принято…",
    )
    try:
        await message.answer(f"Топик создан: {name}")
    except Exception:  # noqa: BLE001
        logger.debug("origin ack failed", exc_info=True)

    async def on_progress(note: str) -> None:
        await _edit_progress(status_msg, header + note)

    async with TypingKeepalive(bot, forum_chat_id, thread_id):
        try:
            result = await runner.enqueue(session, text, on_progress)
            await _finish_status(status_msg, header + result + "\n\n— готово")
        except Exception as exc:  # noqa: BLE001
            logger.exception("task failed")
            await _finish_status(status_msg, header + f"Ошибка: {exc}")


async def _run_agent_task(
    message: Message,
    runner: AgentRunner,
    session: SessionKey,
    text: str,
) -> None:
    bot = message.bot
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    status_msg = await message.answer("Принято…")

    async def on_progress(note: str) -> None:
        await _edit_progress(status_msg, note)

    async with TypingKeepalive(bot, chat_id, thread_id):
        try:
            result = await runner.enqueue(session, text, on_progress)
            await _finish_status(status_msg, f"{result}\n\n— готово")
        except Exception as exc:  # noqa: BLE001
            logger.exception("task failed")
            await _finish_status(status_msg, f"Ошибка: {exc}")


def _run_git(cwd: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or "(empty)"


def _run_script(cwd: Path, name: str, args: list[str]) -> str:
    script = cwd / "scripts" / name
    proc = subprocess.run(
        ["bash", str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    header = f"exit={proc.returncode}\n"
    return header + (out.strip() or "(no output)")


def build_dispatcher(settings: Settings, runner: AgentRunner) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings
    dp["runner"] = runner
    dp.include_router(router)

    @dp.update.outer_middleware()
    async def inject_deps(handler, event, data):  # type: ignore[no-untyped-def]
        data["settings"] = settings
        data["runner"] = runner
        return await handler(event, data)

    return dp


async def run_bot(settings: Settings, runner: AgentRunner) -> None:
    bot = Bot(token=settings.telegram_token)
    dp = build_dispatcher(settings, runner)
    logger.info(
        "Polling Telegram… repo=%s forum_mode=%s forum_chat_id=%s",
        settings.repo_cwd,
        settings.forum_mode,
        settings.forum_chat_id,
    )
    await dp.start_polling(bot)
