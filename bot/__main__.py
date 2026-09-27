from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from bot.agent_runner import AgentRunner
from bot.config import load_settings
from bot.handlers import run_bot
from bot.store import SessionStore

logger = logging.getLogger(__name__)


def _arm_stop(run_task: asyncio.Task[None], data_dir: Path) -> asyncio.Task[None]:
    """SIGTERM (Linux/macOS), Ctrl+Break (Windows), or data_dir/stop.request."""
    loop = asyncio.get_running_loop()
    flag = data_dir / "stop.request"

    def _request_stop() -> None:
        if not run_task.done():
            run_task.cancel()

    def _from_signal(*_args: object) -> None:
        loop.call_soon_threadsafe(_request_stop)

    signals = [signal.SIGTERM]
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError, ValueError):
            signal.signal(sig, _from_signal)

    async def _watch_stop_file() -> None:
        # Drop a stale flag left by a previous stop, then honor a new one.
        if flag.exists():
            flag.unlink(missing_ok=True)
        while not run_task.done():
            if flag.exists():
                flag.unlink(missing_ok=True)
                _request_stop()
                return
            await asyncio.sleep(0.4)

    return asyncio.create_task(_watch_stop_file(), name="stop-request")


def main() -> None:
    log_stream = sys.stderr if sys.stderr is not None else None
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=log_stream,
    )
    settings = load_settings()
    store = SessionStore(settings.data_dir / "sessions.sqlite3")
    runner = AgentRunner(settings, store)

    async def _amain() -> None:
        await runner.start()
        run_task = asyncio.create_task(run_bot(settings, runner), name="telegram-poll")
        watch_stop = _arm_stop(run_task, settings.data_dir)
        try:
            await run_task
        except asyncio.CancelledError:
            logger.info("Stop requested")
        finally:
            watch_stop.cancel()
            try:
                await watch_stop
            except asyncio.CancelledError:
                pass
            await runner.stop()
            store.close()

    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
