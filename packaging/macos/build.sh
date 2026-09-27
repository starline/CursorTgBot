#!/usr/bin/env bash
# Foundation build for macOS. Same spec as Windows; run on a Mac.
# Result: dist/CursorTgBot.app
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -r requirements-desktop.txt pyinstaller
python -m PyInstaller packaging/CursorTgBot.spec --noconfirm --clean
echo "Built $ROOT/dist/CursorTgBot.app"
