"""Turn a Telegram message into an agent prompt: reply quote and saved files."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
QUOTE_LIMIT = 1500


@dataclass(frozen=True)
class SavedFile:
    kind: str
    path: Path


def reply_excerpt(message: Message) -> str:
    replied = message.reply_to_message
    if replied is None:
        return ""
    bits: list[str] = []
    raw = (replied.text or replied.caption or "").strip()
    if raw:
        bits.append(raw)
    if replied.photo:
        bits.append("[фото]")
    if replied.document is not None:
        name = replied.document.file_name or "файл"
        bits.append(f"[файл {name}]")
    if replied.voice is not None:
        bits.append("[голосовое]")
    if replied.audio is not None:
        bits.append("[аудио]")
    if replied.video is not None:
        bits.append("[видео]")
    text = "\n".join(bits).strip()
    if len(text) > QUOTE_LIMIT:
        text = text[: QUOTE_LIMIT - 1].rstrip() + "…"
    return text


def enrich_prompt(text: str, message: Message, files: list[SavedFile] | None = None) -> str:
    """User text plus the message they replied to and any files saved on disk."""
    blocks: list[str] = []
    excerpt = reply_excerpt(message)
    if excerpt:
        blocks.append("Пользователь отвечает на это сообщение:\n" + excerpt)
    saved = files or []
    if saved:
        lines = ["Вложения (абсолютные пути, прочитай с диска):"]
        for item in saved:
            lines.append(f"- {item.kind}: {item.path}")
        blocks.append("\n".join(lines))
    body = text.strip()
    if not body and saved:
        kinds = {item.kind for item in saved}
        if kinds <= {"voice", "audio"}:
            body = "Прослушай голосовое и выполни просьбу из него."
        else:
            body = "Посмотри вложения и сделай то, что из них следует."
    if not blocks:
        return body
    return "\n\n".join([*blocks, body])


def joined_caption(messages: list[Message]) -> str:
    parts = [(message.caption or message.text or "").strip() for message in messages]
    return "\n".join(part for part in parts if part)


async def save_attachments(messages: list[Message], inbox: Path) -> list[SavedFile]:
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[SavedFile] = []
    for message in messages:
        bot = message.bot
        if message.photo:
            photo = message.photo[-1]
            item = await _download(
                bot, photo.file_id, inbox, message.message_id, "photo", "photo.jpg", photo.file_size
            )
            if item:
                saved.append(item)
        if message.document is not None:
            doc = message.document
            item = await _download(
                bot,
                doc.file_id,
                inbox,
                message.message_id,
                "file",
                doc.file_name or "file.bin",
                doc.file_size,
            )
            if item:
                saved.append(item)
        if message.voice is not None:
            item = await _download(
                bot,
                message.voice.file_id,
                inbox,
                message.message_id,
                "voice",
                "voice.ogg",
                message.voice.file_size,
            )
            if item:
                saved.append(item)
        if message.audio is not None:
            item = await _download(
                bot,
                message.audio.file_id,
                inbox,
                message.message_id,
                "audio",
                message.audio.file_name or "audio.mp3",
                message.audio.file_size,
            )
            if item:
                saved.append(item)
        if message.video is not None:
            item = await _download(
                bot,
                message.video.file_id,
                inbox,
                message.message_id,
                "video",
                message.video.file_name or "video.mp4",
                message.video.file_size,
            )
            if item:
                saved.append(item)
    return saved


async def _download(
    bot: Bot,
    file_id: str,
    inbox: Path,
    message_id: int,
    kind: str,
    filename: str,
    file_size: int | None,
) -> SavedFile | None:
    if file_size is not None and file_size > MAX_FILE_BYTES:
        logger.info("Skip %s over size limit (%s bytes)", kind, file_size)
        return None
    try:
        tg_file = await bot.get_file(file_id)
    except Exception:  # noqa: BLE001
        logger.exception("get_file failed for %s", kind)
        return None
    if tg_file.file_size and tg_file.file_size > MAX_FILE_BYTES:
        return None
    if not tg_file.file_path:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = inbox / f"{stamp}-{message_id}-{_safe_name(filename)}"
    try:
        await bot.download_file(tg_file.file_path, destination=dest)
    except Exception:  # noqa: BLE001
        logger.exception("download failed for %s", kind)
        return None
    return SavedFile(kind=kind, path=dest)


def _safe_name(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (cleaned or "file")[:80]
