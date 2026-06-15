#!/usr/bin/env bash
# Daily driver for the trading strategy (75% hand-picked stocks + 25% ETF
# ballast), built to fit a Robinhood-style agentic investment account.
#
#   bash code/run_daily.sh --dry-run        # plan only, no orders
#   bash code/run_daily.sh --execute        # submit paper orders + report
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

if ! "$PY" -c "import alpaca, dotenv" >/dev/null 2>&1; then
  if ! "$PY" -m pip install -q -r requirements.txt >/dev/null 2>&1; then
    # System Python refused the install (common on newer macOS); use a venv.
    python3 -m venv .venv
    PY=".venv/bin/python"
    "$PY" -m pip install -q -r requirements.txt
  fi
fi

# Run the strategy (frozen params — no learning loop by design).
"$PY" code/autopilot.py "$@"

# Always print a risk-adjusted performance report (never blocks the run).
"$PY" code/metrics.py || true
