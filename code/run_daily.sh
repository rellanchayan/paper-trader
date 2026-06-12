#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

if ! "$PY" -c "import alpaca, dotenv" >/dev/null 2>&1; then
  if ! "$PY" -m pip install -q -r requirements.txt >/dev/null 2>&1; then
    # System Python refused the install (common on newer macOS).
    # Build a private virtual environment instead.
    python3 -m venv .venv
    PY=".venv/bin/python"
    "$PY" -m pip install -q -r requirements.txt
  fi
fi

exec "$PY" code/autopilot.py "$@"
