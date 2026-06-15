#!/usr/bin/env bash
# Daily driver. Default strategy is the DTA (Diversified Trend Allocator) — the
# path to real money. The legacy momentum bot is opt-in via --momentum.
#
#   bash code/run_daily.sh --dry-run        # DTA, plan only
#   bash code/run_daily.sh --execute        # DTA, submit paper orders + report
#   bash code/run_daily.sh --momentum --execute   # legacy momentum bot + learn loop
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

# Split a --momentum/--dta flag out of the args; pass the rest through.
STRATEGY="dta"
ARGS=()
for a in "$@"; do
  case "$a" in
    --momentum) STRATEGY="momentum" ;;
    --dta)      STRATEGY="dta" ;;
    *)          ARGS+=("$a") ;;
  esac
done
# Safe expansion of a possibly-empty array under `set -u`.
PASS=("${ARGS[@]+"${ARGS[@]}"}")

if [ "$STRATEGY" = "momentum" ]; then
  "$PY" code/autopilot.py "${PASS[@]}"
  # Legacy bot learns from closed trades (never blocks the run).
  "$PY" code/learn.py || echo "learning step failed — continuing"
else
  "$PY" code/dta_autopilot.py "${PASS[@]}"
  # DTA has NO learning loop (frozen params by design).
fi

# Always print a risk-adjusted performance report (never blocks the run).
"$PY" code/dta_metrics.py || true
