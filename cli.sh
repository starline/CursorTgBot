#!/usr/bin/env bash
# Terminal agent. Same launch and repo rules as the Telegram bot (./run.sh):
#   cd your-project && /path/to/CursorTgBot/cli.sh
# REPO_CWD in .env wins; otherwise the directory you launched from is used.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CURSOR_TG_START_CWD="${CURSOR_TG_START_CWD:-$(pwd)}"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! python -c "import prompt_toolkit" >/dev/null 2>&1; then
    pip install -r requirements.txt
  fi
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — will ask for the Cursor API key on first start."
fi

exec python -m cli "$@"
