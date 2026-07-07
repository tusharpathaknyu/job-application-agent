#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUNDLED_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

cd "$ROOT"
if [ -x "$BUNDLED_PYTHON" ]; then
  exec "$BUNDLED_PYTHON" -m job_agent serve
fi
exec python3 -m job_agent serve
