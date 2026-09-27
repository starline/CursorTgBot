from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.agent_runner import AgentRunner, RunNotice
from bot.backlog_view import format_backlog_html, is_backlog_list_request, load_tasks
from bot.config import Settings, upsert_env_values
from bot.git_ops import commit_message, commit_worktree, worktree_patch
from bot.inbound import enrich_prompt, joined_caption, save_attachments
from bot.store import SessionKey

logger = logging.getLogger(__name__)
router = Router()

# Shown in the Telegram "/" menu (private chats and groups).
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="task", description="Новая задача"),
    BotCommand(command="ask", description="Уточнение в этом чате"),
    BotCommand(command="backlog", description="Активные задачи бэклога"),
    BotCommand(command="status", description="Очередь и текущий run"),
    BotCommand(command="cancel", description="Отменить текущий run"),
    BotCommand(command="diff", description="git status и diff"),
    BotCommand(command="new", description="Сбросить сессию агента"),
    BotCommand(command="model", description="Текущая модель или смена"),
    BotCommand(command="info", description="Id чата и настройки"),
    BotCommand(command="phpunit", description="Запустить PHPUnit"),
    BotCommand(command="phpstan", description="Запустить PHPStan"),
    BotCommand(command="help", description="Список команд"),
]

TG_LIMIT = 3900
TOPIC_NAME_LIMIT = 128
_FLOOD_UNTIL: dict[int, float] = {}
_ALBUMS: dict[str, list[Message]] = {}
_ALBUM_WAIT: dict[str, asyncio.Task[None]] = {}
_DONE_PHASES = {"completed", "complete", "done", "success"}


def _now() -> float:
    return asyncio.get_running_loop().time()


def _mark_flood(chat_id: int, seconds: int) -> None:
    wait = min(max(int(seconds), 1), 60)
    _FLOOD_UNTIL[chat_id] = max(_FLOOD_UNTIL.get(chat_id, 0), _now() + wait)


async def _wait_flood(chat_id: int) -> None:
    remaining = _FLOOD_UNTIL.get(chat_id, 0) - _now()
    if remaining > 0:
        logger.warning("Chat %s is flood-limited, waiting %.0fs", chat_id, remaining)
        await asyncio.sleep(remaining)


async def _retry_telegram(factory, *, chat_id: int, what: str):  # type: ignore[no-untyped-def]
    """Repeat a Telegram call when the server asks us to wait."""
    last_exc: TelegramRetryAfter | None = None
    for attempt in range(4):
        await _wait_flood(chat_id)
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            last_exc = exc
            _mark_flood(chat_id, int(exc.retry_after) + 1)
            logger.warning(
                "%s hit flood control, retry in %ss (attempt %s)",
                what,
                exc.retry_after,
                attempt + 1,
            )
    assert last_exc is not None
    raise last_exc


def _truncate(text: str, limit: int = TG_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n\n… (обрезано)"


def _chunk_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split long replies head-first so Telegram lists keep P0… at the start."""
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        piece = rest[:cut].rstrip()
        chunks.append(piece if piece else rest[:limit])
        rest = rest[cut:].lstrip("\n") if cut < len(rest) else ""
    return chunks


async def _deny(message: Message) -> None:
    user = message.from_user
    if user is not None:
        await message.answer(
            "Нет доступа.\n"
            f"Твой Telegram user id: `{user.id}`\n"
            "Добавь его в `ALLOWED_USER_IDS` в `.env` и перезапусти бота."
        )
        return
    await message.answer("Нет доступа.")


async def _auth(
    settings: Settings,
    message: Message,
    *,
    deny: bool = True,
    check_chat: bool = True,
) -> bool:
    """Authorize user/chat. Empty allowlist → first user is saved to .env automatically."""
    user = message.from_user
    if user is None:
        if deny:
            await _deny(message)
        return False
    if not settings.is_user_allowed(user.id):
        if settings.try_claim_first_user(user.id):
            await message.answer(
                f"Доступ выдан (первый пользователь).\n"
                f"id `{user.id}` записан в `ALLOWED_USER_IDS`."
            )
        else:
            if deny:
                await _deny(message)
            return False
    if check_chat and not settings.is_chat_allowed(message.chat.id):
        return False
    return True


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


def _forum_topic_link(chat_id: int, thread_id: int) -> str | None:
    """Private forum topic deep-link: https://t.me/c/<id_without_-100>/<thread_id>."""
    s = str(chat_id)
    if not s.startswith("-100"):
        return None
    return f"https://t.me/c/{s[4:]}/{thread_id}"


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
    if len(text) <= limit:
        return text
    return "…\n" + text[-(limit - 2) :]


async def _edit_progress(status_msg: Message, text: str) -> bool:
    chat_id = status_msg.chat.id
    if _FLOOD_UNTIL.get(chat_id, 0) > _now():
        return False
    try:
        await status_msg.edit_text(_fit_edit(text))
        return True
    except TelegramRetryAfter as exc:
        _mark_flood(chat_id, int(exc.retry_after) + 1)
        logger.warning("Progress edit flood-limited for %ss", exc.retry_after)
        return False
    except TelegramBadRequest as exc:
        if "not modified" in str(exc).lower():
            return True
        logger.debug("progress edit failed", exc_info=True)
        return False
    except Exception:  # noqa: BLE001
        logger.debug("progress edit failed", exc_info=True)
        return False


async def _send_html_chunks(message: Message, chunks: list[str]) -> None:
    chat_id = message.chat.id
    for index, chunk in enumerate(chunks):
        try:
            await _retry_telegram(
                lambda chunk=chunk: message.answer(chunk, parse_mode=ParseMode.HTML),
                chat_id=chat_id,
                what=f"HTML chunk {index + 1}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to send HTML chunk")
            try:
                plain = re.sub(r"<[^>]+>", "", chunk)
                await _retry_telegram(
                    lambda plain=plain: message.answer(plain),
                    chat_id=chat_id,
                    what=f"plain chunk {index + 1}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("plain fallback failed")
                break


async def _reply_backlog(message: Message, settings: Settings, mode: str) -> None:
    tasks = await asyncio.to_thread(load_tasks, settings.repo_cwd, mode=mode)
    chunks = format_backlog_html(tasks, mode=mode)
    await _send_html_chunks(message, chunks)


async def _maybe_reply_backlog(message: Message, settings: Settings, text: str) -> bool:
    mode = is_backlog_list_request(text)
    if mode is None:
        return False
    await _reply_backlog(message, settings, mode)
    return True


@router.message(Command("start", "help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    if not await _auth(settings, message):
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
        "/backlog — active tasks (proposed/ready/doing)\n"
        "/backlog deferred — deferred tasks\n"
        "/info — chat/group id and metadata\n"
        "/status — queue / run\n"
        "/cancel — cancel the current run\n"
        "/diff — git status + diff --stat\n"
        "/new — reset the agent for this session\n"
        "/model — текущая модель; /model <id> — сменить\n"
        "/phpunit [args]\n"
        "/phpstan [args]"
        + forum_hint
        + "\n\n"
        "Фото, файл или голосовое уходят агенту вместе с подписью.\n"
        "Ответ на сообщение добавляет его текст в запрос.\n"
        "После задачи кнопки: Патч, Коммит, Сброс.\n"
        "/status показывает, какой топик сейчас работает и что в очереди.",
    )


@router.message(Command("backlog"))
async def cmd_backlog(
    message: Message,
    settings: Settings,
    command: CommandObject,
) -> None:
    if not await _auth(settings, message):
        return
    arg = (command.args or "").strip().lower()
    mode = "deferred" if arg in {"deferred", "отложенные", "defer"} else "active"
    if arg and mode == "active" and arg not in {"active", "all", "задачи"}:
        await _reply(message, "Использование: /backlog  или  /backlog deferred")
        return
    await _reply_backlog(message, settings, mode)


@router.message(Command("info"))
async def cmd_info(message: Message, settings: Settings) -> None:
    # User allowlist only — so you can discover group id before ALLOWED_CHAT_IDS is set
    if not await _auth(settings, message, check_chat=False):
        return
    user = message.from_user
    assert user is not None

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
    if not await _auth(settings, message):
        return
    here = _session_key(message)
    st = runner.status
    lines = [f"Модель: {settings.model}"]
    if st.busy and st.current_session is not None:
        place = _place(st.current_session, here)
        label = f" — {st.current_label}" if st.current_label else ""
        lines.append(f"Сейчас: {place}{label}")
    else:
        lines.append("Свободен")
    if st.queued:
        lines.append(f"Очередь ({len(st.queued)}):")
        for index, (session, label) in enumerate(st.queued, start=1):
            lines.append(f"{index}. {_place(session, here)} — {label}")
    else:
        lines.append("Очередь пуста")
    await _reply(message, "\n".join(lines))


def _place(session: SessionKey, here: SessionKey) -> str:
    if session == here:
        return "здесь"
    _chat_id, thread_id = session
    if thread_id:
        return f"топик {thread_id}"
    return f"чат {session[0]}"


@router.message(Command("model"))
async def cmd_model(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    command: CommandObject,
) -> None:
    if not await _auth(settings, message):
        return
    arg = (command.args or "").strip()
    if not arg:
        await _reply(message, f"Модель: {settings.model}\nСмена: /model <id>")
        return
    if arg == settings.model:
        await _reply(message, f"Уже {arg}")
        return
    if any(char.isspace() for char in arg) or len(arg) > 80:
        await _reply(message, "Нужен id модели без пробелов, например /model auto")
        return
    upsert_env_values({"CURSOR_MODEL": arg})
    busy = await runner.apply_model(arg)
    if busy:
        await _reply(
            message,
            f"Модель: {arg}\n"
            "Записана в .env. Текущий run доработает на старой, следующий запрос — уже на новой.",
        )
        return
    await _reply(message, f"Модель: {arg}\nСледующий запрос пойдёт с ней.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, settings: Settings, runner: AgentRunner) -> None:
    if not await _auth(settings, message):
        return
    ok = await runner.cancel_current()
    await _reply(message, "Cancel запрошен." if ok else "Нет активного run.")


@router.message(Command("new"))
async def cmd_new(message: Message, settings: Settings, runner: AgentRunner) -> None:
    if not await _auth(settings, message):
        return
    await runner.drop_session(_session_key(message))
    await _reply(message, "Сессия агента сброшена для этого топика/чата.")


@router.message(Command("diff"))
async def cmd_diff(message: Message, settings: Settings) -> None:
    if not await _auth(settings, message):
        return
    cwd = settings.repo_cwd
    status = await asyncio.to_thread(_run_git, cwd, ["status", "-sb"])
    diff = await asyncio.to_thread(_run_git, cwd, ["diff", "--stat"])
    await _reply(message, _truncate(f"```\n{status}\n\n{diff}\n```"))


@router.message(Command("phpunit"))
async def cmd_phpunit(message: Message, settings: Settings, command: CommandObject) -> None:
    if not await _auth(settings, message):
        return
    args = (command.args or "").split()
    await _reply(message, "PHPUnit…")
    out = await asyncio.to_thread(_run_script, settings.repo_cwd, "phpunit.sh", args)
    await _reply(message, _truncate(out))


@router.message(Command("phpstan"))
async def cmd_phpstan(message: Message, settings: Settings, command: CommandObject) -> None:
    if not await _auth(settings, message):
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
    if not await _auth(settings, message):
        return
    raw = (command.args or "").strip()
    if not raw:
        await _reply(message, "Нужен текст: /task …")
        return

    if await _maybe_reply_backlog(message, settings, raw):
        return
    text = enrich_prompt(raw, message)

    if settings.forum_mode:
        if settings.forum_chat_id is None:
            await _reply(
                message,
                "FORUM_MODE=1, но FORUM_CHAT_ID ещё не задан.\n"
                "Напиши /info в группе и пропиши id в .env.",
            )
            return
        await _start_task_in_new_topic(bot, message, settings, runner, text, label=raw)
    else:
        await _run_agent_task(message, runner, _session_key(message), text, label=raw)


@router.message(Command("ask"))
async def cmd_ask(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    command: CommandObject,
) -> None:
    if not await _auth(settings, message):
        return
    raw = (command.args or "").strip()
    if not raw:
        await _reply(message, "Нужен текст: /ask …")
        return

    if await _maybe_reply_backlog(message, settings, raw):
        return

    if settings.forum_mode and not _is_in_topic(message):
        await _reply(message, "Follow-up только внутри топика задачи. Новый task: /task …")
        return

    await _run_agent_task(
        message, runner, _session_key(message), enrich_prompt(raw, message), label=raw
    )


@router.message(F.text & ~F.text.startswith("/"))
async def plain_text(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
) -> None:
    if not await _auth(settings, message, deny=False):
        return
    raw = (message.text or "").strip()
    if not raw:
        return

    if await _maybe_reply_backlog(message, settings, raw):
        return

    if settings.forum_mode and not _is_in_topic(message):
        return

    await _run_agent_task(
        message, runner, _session_key(message), enrich_prompt(raw, message), label=raw
    )


@router.message(F.photo | F.document | F.voice | F.audio | F.video)
async def inbound_media(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
) -> None:
    if not await _auth(settings, message, deny=False):
        return
    group_id = message.media_group_id
    if group_id:
        _ALBUMS.setdefault(group_id, []).append(message)
        if group_id not in _ALBUM_WAIT:
            _ALBUM_WAIT[group_id] = asyncio.create_task(
                _flush_album(group_id, settings, runner),
                name=f"album-{group_id}",
            )
        return
    await _handle_inbound(message, settings, runner, [message])


async def _flush_album(group_id: str, settings: Settings, runner: AgentRunner) -> None:
    await asyncio.sleep(0.9)
    messages = _ALBUMS.pop(group_id, [])
    _ALBUM_WAIT.pop(group_id, None)
    if messages:
        await _handle_inbound(messages[0], settings, runner, messages)


async def _handle_inbound(
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    messages: list[Message],
) -> None:
    if settings.forum_mode and not _is_in_topic(message):
        return
    raw = joined_caption(messages)
    if raw and await _maybe_reply_backlog(message, settings, raw):
        return
    saved = await save_attachments(messages, settings.data_dir / "inbox")
    if not raw and not saved:
        await message.answer("Не удалось сохранить вложение. Лимит — 20 МБ.")
        return
    prompt = enrich_prompt(raw, message, saved)
    label = raw or (saved[0].kind if saved else "вложение")
    await _run_agent_task(message, runner, _session_key(message), prompt, label=label)


async def _start_task_in_new_topic(
    bot: Bot,
    message: Message,
    settings: Settings,
    runner: AgentRunner,
    text: str,
    *,
    label: str,
) -> None:
    forum_chat_id = settings.forum_chat_id
    assert forum_chat_id is not None

    name = _topic_name(label or text)
    try:
        topic = await bot.create_forum_topic(chat_id=forum_chat_id, name=name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_forum_topic failed")
        await _reply(message, f"Не удалось создать топик: {exc}")
        return

    thread_id = int(topic.message_thread_id)
    session: SessionKey = (forum_chat_id, thread_id)
    await bot.send_message(
        chat_id=forum_chat_id,
        message_thread_id=thread_id,
        text=label or text,
    )
    status_msg = await bot.send_message(
        chat_id=forum_chat_id,
        message_thread_id=thread_id,
        text="Начинаю…",
    )
    try:
        link = _forum_topic_link(forum_chat_id, thread_id)
        ack = f"Топик создан: {name}"
        if link:
            ack = f"{ack}\n{link}"
        await message.answer(ack)
    except Exception:  # noqa: BLE001
        logger.debug("origin ack failed", exc_info=True)

    await _drive_run(status_msg, runner, session, text, label=label)


async def _run_agent_task(
    message: Message,
    runner: AgentRunner,
    session: SessionKey,
    text: str,
    *,
    label: str | None = None,
) -> None:
    status_msg = await message.answer("Начинаю…")
    await _drive_run(status_msg, runner, session, text, label=label or text)


async def _drive_run(
    status_msg: Message,
    runner: AgentRunner,
    session: SessionKey,
    text: str,
    *,
    label: str,
) -> None:
    bot = status_msg.bot
    chat_id = status_msg.chat.id
    thread_id = status_msg.message_thread_id
    last = {"line": "", "at": 0.0}

    async def on_progress(note: str) -> None:
        await _edit_progress(status_msg, note)

    async def on_notice(notice: RunNotice) -> None:
        line = _notice_status(notice)
        if not line or line == last["line"]:
            return
        now = time.monotonic()
        if now - last["at"] < 1.5:
            return
        last["line"] = line
        last["at"] = now
        await _edit_progress(status_msg, line)

    async with TypingKeepalive(bot, chat_id, thread_id):
        try:
            result = await runner.enqueue(
                session, text, on_progress, on_notice=on_notice, label=label
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("task failed")
            await _edit_progress(status_msg, "Ошибка")
            await _send_answer(status_msg, f"Ошибка: {exc}", buttons=False)
            return
    done = "Отменено" if result.strip() == "Отменено." else "Готово"
    await _edit_progress(status_msg, done)
    await _send_answer(status_msg, result, buttons=done == "Готово")


def _notice_status(notice: RunNotice) -> str:
    if notice.kind == "tool":
        if notice.phase.lower() in _DONE_PHASES:
            return ""
        line = notice.tool or "tool"
        if notice.text:
            line = f"{line} {notice.text}"
        return line[:180]
    if notice.kind == "thinking":
        return "думает…"
    if notice.kind == "status" and notice.text:
        return notice.text[:180]
    return ""


def _done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Патч", callback_data="run:diff"),
                InlineKeyboardButton(text="Коммит", callback_data="run:commit"),
                InlineKeyboardButton(text="Сброс", callback_data="run:new"),
            ]
        ]
    )


async def _send_answer(status_msg: Message, text: str, *, buttons: bool) -> None:
    parts = _chunk_text(text)
    chat_id = status_msg.chat.id
    last = len(parts) - 1
    for index, part in enumerate(parts):
        markup = _done_keyboard() if buttons and index == last else None

        async def _send(part: str = part, markup: InlineKeyboardMarkup | None = markup) -> Message:
            return await status_msg.answer(part, reply_markup=markup)

        try:
            await _retry_telegram(_send, chat_id=chat_id, what=f"answer {index + 1}")
        except Exception:  # noqa: BLE001
            logger.exception("failed to deliver answer chunk")
            break


@router.callback_query(F.data.startswith("run:"))
async def on_run_action(
    query: CallbackQuery,
    settings: Settings,
    runner: AgentRunner,
) -> None:
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        await query.answer()
        return
    if not settings.is_user_allowed(user.id) or not settings.is_chat_allowed(message.chat.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    action = (query.data or "").split(":", 1)[1]
    await query.answer()
    session = (message.chat.id, int(message.message_thread_id or 0))
    if action == "diff":
        patch = await asyncio.to_thread(worktree_patch, settings.repo_cwd)
        if not patch:
            await message.answer("Нет изменений.")
            return
        await message.answer_document(
            BufferedInputFile(patch.encode("utf-8"), filename="changes.diff"),
            caption="Патч относительно HEAD, включая новые файлы.",
        )
        return
    if action == "commit":
        note = commit_message(runner.last_label(session))
        result = await asyncio.to_thread(commit_worktree, settings.repo_cwd, note)
        await message.answer(result)
        return
    if action == "new":
        await runner.drop_session(session)
        await message.answer("Сессия агента сброшена для этого топика/чата.")
        return
    await message.answer("Неизвестная кнопка.")


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
        "Polling Telegram… repo=%s forum_mode=%s forum_chat_id=%s allowed_users=%s",
        settings.repo_cwd,
        settings.forum_mode,
        settings.forum_chat_id,
        len(settings.allowed_user_ids),
    )
    if not settings.allowed_user_ids:
        logger.warning(
            "ALLOWED_USER_IDS is empty — the first user who messages the bot "
            "will be allowlisted automatically"
        )
    await bot.set_my_commands(BOT_COMMANDS)
    logger.info("Telegram command menu set (%s commands)", len(BOT_COMMANDS))
    await dp.start_polling(bot)
