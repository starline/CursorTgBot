#!/bin/bash
# Runs inside batonogov/pyinstaller-windows (Wine + Windows Python).
set -euo pipefail

echo "Python: $(python -c 'import sys; print(sys.version)')"
echo "Platform: $(python -c 'import sys; print(sys.platform)')"

pip install pystray Pillow

python -c '
import cursor_sdk
from pathlib import Path
root = Path(cursor_sdk.__file__).resolve().parent / "_vendor" / "bridge" / "bin"
files = sorted(root.iterdir()) if root.is_dir() else []
print("bridge bin:", [p.name for p in files])
magic = b""
for path in files:
    if path.name.lower().startswith("node"):
        magic = path.read_bytes()[:2]
        print(path.name, magic)
        break
if magic != b"MZ":
    raise SystemExit("cursor-sdk bridge node is not a Windows binary")
'

pyinstaller --clean -y --dist ./dist --workpath ./build/pyi-win packaging/CursorTgBot.spec

if [[ ! -f dist/CursorTgBot/CursorTgBot.exe ]]; then
  echo "CursorTgBot.exe was not produced" >&2
  find dist -maxdepth 3 -type f -name '*.exe' -print >&2 || true
  exit 1
fi

chown -R --reference=. ./dist ./build
echo "Built dist/CursorTgBot/CursorTgBot.exe"
