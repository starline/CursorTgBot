from __future__ import annotations

import os
import sys

from desktop.paths import ensure_runtime, env_path, runtime_dir


def main() -> None:
    ensure_runtime()
    os.environ.setdefault("CURSOR_TG_ENV", str(env_path()))
    os.environ.setdefault("BOT_DATA_DIR", str(runtime_dir()))
    if "--bot" in sys.argv[1:]:
        from bot.__main__ import main as bot_main

        bot_main()
        return
    from desktop.app import run_app

    run_app()


if __name__ == "__main__":
    main()
