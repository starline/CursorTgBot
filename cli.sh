#!/usr/bin/env bash
# Same launcher as `tgBot`. Kept so older docs and scripts still work.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
exec "$ROOT/tgBot" "$@"
