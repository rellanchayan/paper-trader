#!/usr/bin/env bash
# Daily driver. Default strategy is the BLEND (75% hand-picked stocks + 25% ETF
# ballast), built to fit a Robinhood-style agentic investment account.
# Alternatives are opt-in: --dta (diversified ETF core) or --momentum (legacy).
#
#   bash code/run_daily.sh --dry-run        # BLEND, plan only
#   bash code/run_daily.sh --execute        # BLEND, submit paper orders + report
#   bash code/run_daily.sh --dta --execute       # DTA ETF core
#   bash code/run_daily.sh --momentum --execute  # legacy momentum bot + learn loop
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

# Split the strategy flag out of the args; pass the rest through.
STRATEGY="blend"
ARGS=()
for a in "$@"; do
  case "$a" in
    --blend)    STRATEGY="blend" ;;
    --dta)      STRATEGY="dta" ;;
    --momentum) STRATEGY="momentum" ;;
    *)          ARGS+=("$a") ;;
  esac
done
# Safe expansion of a possibly-empty array under `set -u`.
PASS=("${ARGS[@]+"${ARGS[@]}"}")

case "$STRATEGY" in
  momentum)
    "$PY" code/autopilot.py "${PASS[@]}"
    # Legacy bot learns from closed trades (never blocks the run).
    "$PY" code/learn.py || echo "learning step failed — continuing"
    ;;
  dta)
    "$PY" code/dta_autopilot.py "${PASS[@]}"
    # DTA has NO learning loop (frozen params by design).
    ;;
  *)
    "$PY" code/blend_autopilot.py "${PASS[@]}"
    # BLEND has NO learning loop (frozen params by design).
    ;;
esac

# Always print a risk-adjusted performance report (never blocks the run).
"$PY" code/dta_metrics.py || true
