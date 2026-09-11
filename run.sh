#!/usr/bin/env bash
# Install deps if needed, then start the bot.
# Run from your target repo (REPO_CWD defaults to the directory you launch from):
#   /path/to/CursorTgBot/run.sh
# Or set REPO_CWD in .env.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Directory the user launched from — used as default REPO_CWD
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
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — will ask for token and API key on first start."
fi

exec python -m bot "$@"
