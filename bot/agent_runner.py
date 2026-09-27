from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from cursor_sdk import AgentOptions, AsyncClient, LocalAgentOptions

from bot.config import Settings
from bot.prompt import wrap_user_task
from bot.store import SessionKey, SessionStore

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], Awaitable[None]]
NoticeCb = Callable[["RunNotice"], Awaitable[None]]

_ARG_KEYS = (
    "command",
    "cmd",
    "path",
    "file_path",
    "target_file",
    "file",
    "pattern",
    "query",
    "url",
    "glob",
)


@dataclass(frozen=True)
class RunNotice:
    """Live event. Terminal prints it; Telegram turns tool/status into one short line."""

    kind: str  # status | text | tool | thinking
    text: str = ""
    tool: str = ""
    phase: str = ""
    call_id: str = ""


@dataclass
class RunnerStatus:
    busy: bool = False
    current_session: SessionKey | None = None
    queue_len: int = 0
    current_label: str = ""
    queued: tuple[tuple[SessionKey, str], ...] = ()


@dataclass
class _Job:
    session: SessionKey
    prompt: str
    on_progress: ProgressCb
    on_notice: NoticeCb | None = None
    label: str = ""
    queued: bool = False
    done: asyncio.Future[str] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class AgentRunner:
    """Single-flight local agent runs against one repo cwd."""

    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        *,
        reply_channel: str = "telegram",
    ) -> None:
        self._settings = settings
        self._store = store
        self._reply_channel = reply_channel
        self._client: AsyncClient | None = None
        self._agents: dict[SessionKey, Any] = {}
        self._handles: dict[SessionKey, Any] = {}
        self._current_run: Any | None = None
        self._current_session: SessionKey | None = None
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._pending: list[tuple[SessionKey, str]] = []
        self._labels: dict[SessionKey, str] = {}
        self._current_label = ""
        self._worker_task: asyncio.Task[None] | None = None
        self._cancel_requested = False
        self._close_agents_after_current = False

    @property
    def status(self) -> RunnerStatus:
        return RunnerStatus(
            busy=self._current_session is not None,
            current_session=self._current_session,
            queue_len=len(self._pending),
            current_label=self._current_label,
            queued=tuple(self._pending),
        )

    def last_label(self, session: SessionKey) -> str:
        return self._labels.get(session, "")

    async def apply_model(self, model: str) -> bool:
        """Switch the model for the next run. Returns True when a run is still in progress."""
        self._settings.model = model
        busy = self._current_session is not None
        if busy:
            self._close_agents_after_current = True
            for key in list(self._agents):
                if key != self._current_session:
                    await self._close_agent(key)
            return True
        for key in list(self._agents):
            await self._close_agent(key)
        return False

    async def start(self) -> None:
        self._client = await AsyncClient.launch_bridge(workspace=str(self._settings.repo_cwd))
        await self._client.__aenter__()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="agent-worker")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        for key in list(self._agents):
            await self._close_agent(key)

        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to close AsyncClient")
            self._client = None

    def has_live_agent(self, session: SessionKey) -> bool:
        return session in self._handles

    async def enqueue(
        self,
        session: SessionKey,
        text: str,
        on_progress: ProgressCb,
        *,
        on_notice: NoticeCb | None = None,
        label: str | None = None,
    ) -> str:
        shown = _task_label(label or text)
        self._labels[session] = shown
        queued = self._current_session is not None or bool(self._pending)
        job = _Job(
            session=session,
            prompt=text,
            on_progress=on_progress,
            on_notice=on_notice,
            label=shown,
            queued=queued,
        )
        self._pending.append((session, shown))
        self._queue.put_nowait(job)
        if queued:
            note = f"В очереди ({len(self._pending)}). Один run на репозиторий."
            await on_progress(note)
            await _emit(job, RunNotice(kind="status", text=note))
        return await job.done

    async def cancel_current(self) -> bool:
        run = self._current_run
        if run is None:
            return False
        self._cancel_requested = True
        try:
            if run.supports("cancel"):
                await run.cancel()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Cancel failed")
            return False

    async def drop_session(self, session: SessionKey) -> None:
        await self._close_agent(session)
        self._store.clear(session)

    async def _close_agent(self, session: SessionKey) -> None:
        self._handles.pop(session, None)
        agent = self._agents.pop(session, None)
        if agent is None:
            return
        try:
            await agent.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to close agent for session %s", session)

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            if self._pending:
                self._pending.pop(0)
            self._current_session = job.session
            self._current_label = job.label
            self._cancel_requested = False
            try:
                result = await self._run_job(job)
                if not job.done.done():
                    job.done.set_result(result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Job failed for session %s", job.session)
                if not job.done.done():
                    job.done.set_exception(exc)
            finally:
                finished = job.session
                self._current_run = None
                self._current_session = None
                self._current_label = ""
                if self._close_agents_after_current:
                    self._close_agents_after_current = False
                    await self._close_agent(finished)
                self._queue.task_done()

    async def _run_job(self, job: _Job) -> str:
        if job.queued:
            await _say(job, "Очередь дошла. Начинаю.")
        else:
            await _say(job, "Начинаю…")
        agent = await self._get_or_create_agent(job.session)
        prompt = wrap_user_task(job.prompt, channel=self._reply_channel)
        run = await agent.send(prompt)
        self._current_run = run

        chunks: list[str] = []

        async for message in run.messages():
            if self._cancel_requested:
                break
            msg_type = str(_field(message, "type", "") or "").lower()
            if msg_type == "tool_call":
                await _emit(job, _tool_notice(message))
                continue
            if msg_type == "thinking":
                await _emit(job, RunNotice(kind="thinking"))
                continue
            if msg_type in {"status", "system", "usage", "task", "user", "request"}:
                if msg_type == "status":
                    note = str(_field(message, "message", "") or "").strip()
                    if note:
                        await _emit(job, RunNotice(kind="status", text=note))
                continue
            for block in _content_blocks(message):
                if str(_field(block, "type", "") or "") == "tool_use":
                    await _emit(job, _tool_notice(block))
            text = _assistant_text(message)
            if not text:
                continue
            chunks.append(text)
            await _emit(job, RunNotice(kind="text", text=text))

        live = "".join(chunks)

        result = await run.wait()

        if self._cancel_requested:
            return "Отменено."

        status = getattr(result, "status", None)
        status_s = str(status).lower() if status is not None else ""
        raw = getattr(result, "result", None)
        final = live.strip()
        if isinstance(raw, str) and raw.strip():
            # Prefer SDK terminal text when present (more reliable than stream concat).
            final = raw.strip()
        if not final:
            final = "(нет текстового ответа)"

        if status_s and status_s not in {"finished", "success"}:
            return f"Статус: {status}\n\n{final}"
        return final

    def _local_options(self) -> LocalAgentOptions:
        return LocalAgentOptions(
            cwd=str(self._settings.repo_cwd),
            setting_sources=["project"],
        )

    def _agent_options(self) -> AgentOptions:
        return AgentOptions(
            model=self._settings.model,
            api_key=self._settings.cursor_api_key,
            local=self._local_options(),
        )

    async def _get_or_create_agent(self, session: SessionKey) -> Any:
        if session in self._handles:
            return self._handles[session]

        assert self._client is not None
        stored_id = self._store.get_agent_id(session)
        agent: Any

        if stored_id:
            try:
                agent = await self._client.resume_agent(stored_id, self._agent_options())
                logger.info("Resumed agent %s for session %s", stored_id, session)
            except Exception:  # noqa: BLE001
                logger.exception("Resume failed for %s, creating new agent", stored_id)
                self._store.clear(session)
                agent = await self._client.create_agent(self._agent_options())
        else:
            agent = await self._client.create_agent(self._agent_options())

        handle = await agent.__aenter__()
        self._agents[session] = agent
        self._handles[session] = handle
        agent_id = getattr(handle, "agent_id", None) or stored_id
        if agent_id:
            self._store.set_agent_id(session, str(agent_id))
        return handle


async def _say(job: _Job, text: str) -> None:
    await job.on_progress(text)
    await _emit(job, RunNotice(kind="status", text=text))


async def _emit(job: _Job, notice: RunNotice) -> None:
    if job.on_notice is None:
        return
    try:
        await job.on_notice(notice)
    except Exception:  # noqa: BLE001
        logger.debug("on_notice failed", exc_info=True)


def _task_label(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > 80:
        return line[:79].rstrip() + "…"
    return line


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _content_blocks(message: Any) -> list[Any]:
    content = _field(message, "content", None)
    if content is None:
        inner = _field(message, "message", None)
        content = _field(inner, "content", None) if inner is not None else None
    if not content:
        return []
    return list(content)


def _tool_notice(message: Any) -> RunNotice:
    name = str(_field(message, "name", "") or "tool")
    args = _field(message, "args", None)
    if args is None:
        args = _field(message, "input", None)
    phase = str(_field(message, "status", "") or "")
    call_id = str(_field(message, "call_id", "") or _field(message, "id", "") or "")
    return RunNotice(
        kind="tool",
        tool=name,
        text=_summarize_args(args),
        phase=phase,
        call_id=call_id,
    )


def _summarize_args(args: Any, depth: int = 0) -> str:
    found = _find_arg_string(args, depth)
    if not found:
        return ""
    text = " ".join(found.split())
    if len(text) > 160:
        return text[:159] + "…"
    return text


def _find_arg_string(value: Any, depth: int = 0) -> str:
    if depth > 4 or value is None:
        return ""
    if isinstance(value, dict):
        for key in _ARG_KEYS:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _find_arg_string(item, depth + 1)
            if found:
                return found
    return ""


def _assistant_text(message: Any) -> str:
    msg_type = getattr(message, "type", None)
    type_s = str(msg_type).lower() if msg_type is not None else ""

    if "assistant" not in type_s:
        inner = getattr(message, "message", None)
        if inner is not None and inner is not message:
            return _assistant_text(inner)
        return ""

    content = getattr(message, "content", None)
    if content is None:
        inner = getattr(message, "message", None)
        content = getattr(inner, "content", None) if inner is not None else None
    if not content:
        return ""

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)
