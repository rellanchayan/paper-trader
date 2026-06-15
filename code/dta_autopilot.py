"""
dta_autopilot.py — entry point for the Diversified Trend Allocator (DTA).

This is the "path to real money" engine. It wires Alpaca's price/quote data into
the pure planner in dta_engine.py, persists trend-gate state for hysteresis, and
submits orders sell-before-buy. The legacy momentum bot (autopilot.py) is left
untouched for paper comparison.

Usage:
    python3 code/dta_autopilot.py --dry-run     # plan only, no orders, no state writes
    python3 code/dta_autopilot.py --execute     # submit paper orders, persist state
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import dta_engine

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PENDING = STATE / "pending_trades"
COMPLETED = STATE / "completed_trades"
RUNS = STATE / "dta_runs"
STATE_FILE = STATE / "dta_state.json"
UNIVERSE_FILE = STATE / "dta_universe.json"
CONFIG_FILE = STATE / "dta_config.json"
SECTORS_FILE = STATE / "sectors.json"
HALT_FILE = ROOT / ".HALT_TRADING"

_BARS_CACHE: dict[str, list[float]] = {}


def run_json(args: list[str]) -> dict:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)


def run_text(args: list[str]) -> str:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def get_closes(ticker: str, days: int = 260) -> list[float]:
    if ticker not in _BARS_CACHE:
        try:
            data = run_json([sys.executable, "code/alpaca_client.py", "--bars", ticker, "--days", str(days)])
            _BARS_CACHE[ticker] = [float(b["c"]) for b in data.get("bars", [])]
        except Exception:
            _BARS_CACHE[ticker] = []
    return _BARS_CACHE[ticker]


def get_price(ticker: str, side: str, fallback: float) -> float:
    try:
        q = run_json([sys.executable, "code/alpaca_client.py", "--quote", ticker])
        if side == "BUY" and float(q.get("ask") or 0) > 0:
            return float(q["ask"])
        if side == "SELL" and float(q.get("bid") or 0) > 0:
            return float(q["bid"])
    except Exception:
        pass
    return float(fallback) if fallback else 0.0


def market_is_open() -> bool:
    try:
        return bool(run_json([sys.executable, "code/alpaca_client.py", "--is-open"]).get("is_open"))
    except Exception:
        return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(gates: dict, last_buy_month: str) -> None:
    STATE_FILE.write_text(json.dumps({
        "gates": gates,
        "last_buy_month": last_buy_month,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


def next_trade_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"T-{today}-"
    nums: list[int] = []
    for folder in (PENDING, COMPLETED):
        if folder.exists():
            for path in folder.glob(f"{prefix}*.json"):
                try:
                    nums.append(int(path.stem.split("-")[-1]))
                except ValueError:
                    continue
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def build_trade_file(trade: dict) -> Path:
    PENDING.mkdir(parents=True, exist_ok=True)
    record = {
        "trade_id": next_trade_id(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "dta",
        "status": "ready",
        **trade,
    }
    path = PENDING / f"{record['trade_id']}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def submit_trade(path: Path) -> dict:
    run_text([sys.executable, "code/constitution.py", "--check", str(path)])
    return run_json([sys.executable, "code/alpaca_client.py", "--submit", str(path)])


def run(execute: bool) -> dict:
    if HALT_FILE.exists():
        return {"status": "halted", "reason": ".HALT_TRADING exists — no orders placed"}

    universe = json.loads(UNIVERSE_FILE.read_text())
    config = json.loads(CONFIG_FILE.read_text())
    sectors = {k: v for k, v in json.loads(SECTORS_FILE.read_text()).items() if not k.startswith("_")}

    run_json([sys.executable, "code/alpaca_client.py", "--reconcile"])  # best-effort, refreshes fills
    portfolio = run_json([sys.executable, "code/alpaca_client.py", "--positions"])
    equity = float(portfolio.get("equity") or 0)
    cash = float(portfolio.get("cash") or 0)
    drawdown = float(portfolio.get("drawdown_from_hwm") or 0)
    positions = {
        str(p["ticker"]).upper(): {
            "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "price": float(p["market_value"]) / float(p["qty"]) if float(p["qty"]) else 0.0,
        }
        for p in portfolio.get("positions", [])
    }

    state = load_state()
    prior_gates = state.get("gates", {})
    last_buy_month = state.get("last_buy_month")
    this_month = datetime.now().strftime("%Y-%m")
    is_new_buy_month = last_buy_month != this_month

    plan = dta_engine.plan_trades(
        equity=equity, cash=cash, positions=positions, drawdown_from_hwm=drawdown,
        prior_gates=prior_gates, is_new_buy_month=is_new_buy_month,
        universe=universe, config=config, sectors=sectors,
        bars_provider=get_closes, price_provider=get_price,
    )

    market_open = market_is_open()
    blocked = execute and not market_open
    submitted: list[dict] = []
    if execute and not blocked:
        for trade in plan["trades"]:
            path = build_trade_file(trade)
            try:
                submitted.append(submit_trade(path))
            except Exception as exc:
                submitted.append({"trade": str(path), "error": str(exc)})
        # Persist state only on a real (executed) run so dry-runs have no side effects.
        new_last_buy = this_month if plan["regime_detail"]["full_rebalance"] else last_buy_month
        save_state(plan["gates"], new_last_buy or last_buy_month)

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "dta",
        "mode": "execute" if execute else "dry_run",
        "market_open": market_open,
        "blocked": "market_closed" if blocked else None,
        "equity": equity,
        "cash": cash,
        "drawdown_from_hwm": round(drawdown, 4),
        "regime": plan["regime"],
        "regime_detail": plan["regime_detail"],
        "gates": plan["gates"],
        "gate_reasons": plan["gate_reasons"],
        "targets": plan["targets"],
        "planned_trades": plan["trades"],
        "submitted": submitted,
        "diagnostics": plan["diagnostics"],
        "is_new_buy_month": is_new_buy_month,
    }

    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=2))
    summary["saved_to"] = str(out.relative_to(ROOT))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="submit paper trades")
    mode.add_argument("--dry-run", action="store_true", help="plan only (default)")
    args = parser.parse_args()
    try:
        summary = run(execute=args.execute)
        print(json.dumps(summary, indent=2))
        return 0
    except Exception as exc:
        print(f"FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
