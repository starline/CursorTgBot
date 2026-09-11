from __future__ import annotations

import asyncio
import logging
import sys

from bot.agent_runner import AgentRunner
from bot.config import load_settings
from bot.handlers import run_bot
from bot.store import SessionStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    settings = load_settings()
    store = SessionStore(settings.data_dir / "sessions.sqlite3")
    runner = AgentRunner(settings, store)

    async def _amain() -> None:
        await runner.start()
        try:
            await run_bot(settings, runner)
        finally:
            await runner.stop()
            store.close()

    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
