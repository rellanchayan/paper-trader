"""
autopilot.py — entry point for the trading strategy (75% hand-picked stocks
+ 25% ETF ballast), built to fit a Robinhood-style agentic investment account.

Wires Alpaca's price/quote data into the pure planner in engine.py, enforces the
daily invest cap and the no-day-trade rule (with help from constitution.py at
submit time), and submits sell-before-buy.

Usage:
    python3 code/autopilot.py --dry-run     # plan only, no orders
    python3 code/autopilot.py --execute     # submit paper orders
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import engine

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PENDING = STATE / "pending_trades"
COMPLETED = STATE / "completed_trades"
RUNS = STATE / "runs"
STATE_FILE = STATE / "strategy_state.json"
CONFIG_FILE = STATE / "config.json"
SECTORS_FILE = STATE / "sectors.json"
WATCHLIST_FILE = STATE / "watchlist.txt"
HALT_FILE = ROOT / ".HALT_TRADING"

_BARS: dict[str, list[float]] = {}


def run_json(args):
    p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return json.loads(p.stdout)


def run_text(args):
    p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()


def get_closes(ticker, days=260):
    if ticker not in _BARS:
        try:
            d = run_json([sys.executable, "code/alpaca_client.py", "--bars", ticker, "--days", str(days)])
            _BARS[ticker] = [float(b["c"]) for b in d.get("bars", [])]
        except Exception:
            _BARS[ticker] = []
    return _BARS[ticker]


def get_price(ticker, side, fallback):
    try:
        q = run_json([sys.executable, "code/alpaca_client.py", "--quote", ticker])
        if side == "BUY" and float(q.get("ask") or 0) > 0:
            return float(q["ask"])
        if side == "SELL" and float(q.get("bid") or 0) > 0:
            return float(q["bid"])
    except Exception:
        pass
    return float(fallback) if fallback else 0.0


def market_is_open():
    try:
        return bool(run_json([sys.executable, "code/alpaca_client.py", "--is-open"]).get("is_open"))
    except Exception:
        return False


def load_watchlist():
    if not WATCHLIST_FILE.exists():
        return []
    out = []
    for line in WATCHLIST_FILE.read_text().splitlines():
        s = line.strip().upper()
        if s and not s.startswith("#"):
            out.append(s)
    return sorted(set(out))


def todays_activity():
    """(invested_today $, tickers sold today, tickers bought today)."""
    today = datetime.now(timezone.utc).date().isoformat()
    invested, sold, bought = 0.0, set(), set()
    if COMPLETED.exists():
        for f in COMPLETED.glob("*.json"):
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            if str(d.get("submitted_at_utc", ""))[:10] != today:
                continue
            side = str(d.get("side", "")).upper()
            tk = str(d.get("ticker", "")).upper()
            if side == "BUY":
                invested += float(d.get("limit_price", 0)) * float(d.get("qty", 0))
                bought.add(tk)
            elif side == "SELL":
                sold.add(tk)
    return invested, sold, bought


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(gates):
    STATE_FILE.write_text(json.dumps(
        {"gates": gates, "updated_utc": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")


def next_trade_id():
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"T-{today}-"
    nums = []
    for folder in (PENDING, COMPLETED):
        if folder.exists():
            for p in folder.glob(f"{prefix}*.json"):
                try:
                    nums.append(int(p.stem.split("-")[-1]))
                except ValueError:
                    pass
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def submit_one(trade):
    PENDING.mkdir(parents=True, exist_ok=True)
    record = {"trade_id": next_trade_id(), "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "strategy": "blend", "status": "ready", **trade}
    path = PENDING / f"{record['trade_id']}.json"
    path.write_text(json.dumps(record, indent=2))
    run_text([sys.executable, "code/constitution.py", "--check", str(path)])  # raises if it fails
    return run_json([sys.executable, "code/alpaca_client.py", "--submit", str(path)])


def run(execute):
    if HALT_FILE.exists():
        return {"status": "halted", "reason": ".HALT_TRADING exists — no orders placed"}

    config = json.loads(CONFIG_FILE.read_text())
    sectors = json.loads(SECTORS_FILE.read_text())
    watchlist = load_watchlist()

    run_json([sys.executable, "code/alpaca_client.py", "--reconcile"])
    portfolio = run_json([sys.executable, "code/alpaca_client.py", "--positions"])
    equity = float(portfolio.get("equity") or 0)
    cash = float(portfolio.get("cash") or 0)
    drawdown = float(portfolio.get("drawdown_from_hwm") or 0)
    positions = {
        str(p["ticker"]).upper(): {
            "qty": float(p["qty"]), "market_value": float(p["market_value"]),
            "price": float(p["market_value"]) / float(p["qty"]) if float(p["qty"]) else 0.0,
        } for p in portfolio.get("positions", [])
    }

    invested_today, sold_today, bought_today = todays_activity()
    prior_gates = load_state().get("gates", {})

    plan = engine.plan_trades(
        equity=equity, cash=cash, positions=positions, drawdown_from_hwm=drawdown,
        prior_gates=prior_gates, invested_today=invested_today,
        sold_today=sold_today, bought_today=bought_today,
        config=config, sectors=sectors, watchlist=watchlist,
        bars_provider=get_closes, price_provider=get_price)

    market_open = market_is_open()
    blocked = execute and not market_open
    submitted = []
    if execute and not blocked:
        for trade in plan["trades"]:
            try:
                submitted.append(submit_one(trade))
            except Exception as exc:
                submitted.append({"ticker": trade.get("ticker"), "side": trade.get("side"), "error": str(exc)})
        save_state(plan["gates"])

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "strategy": "blend",
        "mode": "execute" if execute else "dry_run", "market_open": market_open,
        "blocked": "market_closed" if blocked else None,
        "equity": equity, "cash": cash, "drawdown_from_hwm": round(drawdown, 4),
        "invested_today_before_run": round(invested_today, 2),
        "regime": plan["regime"], "regime_detail": plan["regime_detail"],
        "gates": plan["gates"], "targets": plan["targets"],
        "compliance": plan["compliance"], "planned_trades": plan["trades"],
        "submitted": submitted, "diagnostics": plan["diagnostics"],
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=2))
    summary["saved_to"] = str(out.relative_to(ROOT))
    return summary


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(execute=args.execute), indent=2))
        return 0
    except Exception as exc:
        print(f"FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
