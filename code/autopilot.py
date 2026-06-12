"""
Daily paper-trading autopilot.

Strategy:
- Buy strong liquid names from state/watchlist.txt.
- Sell held names when trend weakens.
- Paper only. LIMIT + DAY only.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PENDING = STATE / "pending_trades"
COMPLETED = STATE / "completed_trades"
RUNS = STATE / "autopilot_runs"
WATCHLIST = STATE / "watchlist.txt"
HALT_FILE = ROOT / ".HALT_TRADING"

DEFAULT_POSITION_PCT = 0.10
DEFAULT_MAX_HOLDINGS = 8


@dataclass
class Signal:
    ticker: str
    action: str
    score: float
    reason: str
    risk: str
    limit_price: float
    qty: int = 0


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


def load_watchlist() -> list[str]:
    if not WATCHLIST.exists():
        WATCHLIST.write_text("SPY\nQQQ\nAAPL\nMSFT\nNVDA\nAMZN\nMETA\nGOOGL\n")
    tickers: list[str] = []
    for line in WATCHLIST.read_text().splitlines():
        line = line.strip().upper()
        if line and not line.startswith("#"):
            tickers.append(line)
    return sorted(set(tickers))


def sma(values: list[float], n: int) -> float:
    return sum(values[-n:]) / n


def get_bars(ticker: str, days: int = 240) -> list[dict]:
    data = run_json([sys.executable, "code/alpaca_client.py", "--bars", ticker, "--days", str(days)])
    return data.get("bars", [])


def get_quote_price(ticker: str, side: str, fallback: float) -> float:
    try:
        q = run_json([sys.executable, "code/alpaca_client.py", "--quote", ticker])
        if side == "BUY" and float(q.get("ask") or 0) > 0:
            return round(float(q["ask"]), 2)
        if side == "SELL" and float(q.get("bid") or 0) > 0:
            return round(float(q["bid"]), 2)
    except Exception:
        pass
    return round(fallback, 2)


def market_is_open() -> bool:
    try:
        data = run_json([sys.executable, "code/alpaca_client.py", "--is-open"])
        return bool(data.get("is_open"))
    except Exception:
        return False


def next_trade_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"T-{today}-"
    nums: list[int] = []
    for folder in (PENDING, COMPLETED):
        if not folder.exists():
            continue
        for path in folder.glob(f"{prefix}*.json"):
            try:
                nums.append(int(path.stem.split("-")[-1]))
            except ValueError:
                continue
    return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"


def build_trade(signal: Signal) -> Path:
    PENDING.mkdir(parents=True, exist_ok=True)
    trade = {
        "trade_id": next_trade_id(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ticker": signal.ticker,
        "side": signal.action,
        "qty": signal.qty,
        "limit_price": signal.limit_price,
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "reason": signal.reason,
        "risk": signal.risk,
        "strategy": "daily_momentum",
        "score": round(signal.score, 4),
        "status": "ready",
    }
    path = PENDING / f"{trade['trade_id']}.json"
    path.write_text(json.dumps(trade, indent=2))
    return path


def submit_trade(path: Path) -> dict:
    run_text([sys.executable, "code/constitution.py", "--check", str(path)])
    return run_json([sys.executable, "code/alpaca_client.py", "--submit", str(path)])


def analyze_ticker(ticker: str, spy_ret20: float) -> tuple[Signal | None, dict]:
    bars = get_bars(ticker)
    if len(bars) < 210:
        return None, {"ticker": ticker, "skip": "not enough bars"}

    closes = [float(b["c"]) for b in bars]
    close = closes[-1]
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    ret20 = close / closes[-21] - 1
    ret5 = close / closes[-6] - 1

    data = {
        "ticker": ticker,
        "close": round(close, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "ret20": round(ret20, 4),
        "ret5": round(ret5, 4),
    }

    if close > sma50 > sma200 and ret20 > max(0.02, spy_ret20):
        score = (ret20 - spy_ret20) + (close / sma50 - 1) + max(ret5, 0)
        reason = (
            f"{ticker} is in an uptrend: close above 50d and 200d averages; "
            f"20d return {ret20:.1%} vs SPY {spy_ret20:.1%}."
        )
        risk = "Momentum can reverse; limit order may fill before a pullback."
        return Signal(ticker, "BUY", score, reason, risk, close), data

    return None, data


def sell_signal_for_position(pos: dict) -> Signal | None:
    ticker = str(pos["ticker"]).upper()
    bars = get_bars(ticker)
    if len(bars) < 60:
        return None
    closes = [float(b["c"]) for b in bars]
    close = closes[-1]
    sma50 = sma(closes, 50)
    plpc = float(pos.get("unrealized_plpc") or 0)
    if close < sma50 or plpc <= -0.08:
        qty = math.floor(float(pos["qty"]))
        if qty < 1:
            return None
        limit_price = get_quote_price(ticker, "SELL", close)
        reason = f"{ticker} sell: price below 50d average or paper loss exceeded 8%."
        risk = "May sell before a rebound."
        return Signal(ticker, "SELL", 1.0, reason, risk, limit_price, qty)
    return None


def recently_sold(days: int = 5) -> set[str]:
    """Tickers we sold in the last `days` days — skip re-buying them (avoids whipsaw)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tickers: set[str] = set()
    if not COMPLETED.exists():
        return tickers
    for path in COMPLETED.glob("*.json"):
        try:
            trade = json.loads(path.read_text())
            submitted = trade.get("submitted_at_utc")
            if (
                str(trade.get("side", "")).upper() == "SELL"
                and submitted
                and datetime.fromisoformat(submitted.replace("Z", "+00:00")) >= cutoff
            ):
                tickers.add(str(trade.get("ticker", "")).upper())
        except Exception:
            continue
    return tickers


def reconcile_orders() -> dict:
    """Best-effort: update yesterday's trades with their real fill status."""
    try:
        return run_json([sys.executable, "code/alpaca_client.py", "--reconcile"])
    except Exception as exc:
        return {"error": str(exc)}


def run_autopilot(execute: bool, max_buys: int, position_pct: float, max_holdings: int) -> dict:
    if HALT_FILE.exists():
        return {"status": "halted", "reason": ".HALT_TRADING exists"}

    reconciled = reconcile_orders()
    portfolio = run_json([sys.executable, "code/alpaca_client.py", "--positions"])
    equity = float(portfolio.get("equity") or 0)
    cash = float(portfolio.get("cash") or 0)
    positions = portfolio.get("positions", [])
    held = {str(p["ticker"]).upper(): p for p in positions}

    spy_bars = get_bars("SPY")
    if len(spy_bars) < 25:
        raise RuntimeError("Not enough SPY bars for benchmark signal")
    spy_closes = [float(b["c"]) for b in spy_bars]
    spy_ret20 = spy_closes[-1] / spy_closes[-21] - 1

    diagnostics: list[dict] = []
    sells = [s for p in positions if (s := sell_signal_for_position(p))]

    cooldown = recently_sold()
    buy_candidates: list[Signal] = []
    if len(held) < max_holdings:
        for ticker in load_watchlist():
            if ticker in held:
                continue
            if ticker in cooldown:
                diagnostics.append({"ticker": ticker, "skip": "sold within last 5 days (cooldown)"})
                continue
            signal, data = analyze_ticker(ticker, spy_ret20)
            diagnostics.append(data)
            if signal:
                buy_candidates.append(signal)

    buy_candidates.sort(key=lambda s: s.score, reverse=True)
    buys: list[Signal] = []
    remaining_slots = max(0, max_holdings - len(held))
    budget_per_buy = equity * position_pct
    available_cash = cash
    for signal in buy_candidates[: min(max_buys, remaining_slots)]:
        price = get_quote_price(signal.ticker, "BUY", signal.limit_price)
        qty = math.floor(min(budget_per_buy, available_cash) / price)
        if qty < 1:
            continue
        signal.qty = qty
        signal.limit_price = price
        buys.append(signal)
        available_cash -= qty * price

    planned = sells + buys
    submitted: list[dict] = []
    market_open = market_is_open()
    blocked = execute and not market_open

    if execute and not blocked:
        for signal in planned:
            path = build_trade(signal)
            try:
                submitted.append(submit_trade(path))
            except Exception as exc:
                submitted.append({"trade": str(path), "error": str(exc)})

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if execute else "dry_run",
        "market_open": market_open,
        "blocked": "market_closed" if blocked else None,
        "reconciled_orders": reconciled,
        "equity": equity,
        "cash": cash,
        "held": sorted(held),
        "spy_ret20": round(spy_ret20, 4),
        "planned": [s.__dict__ for s in planned],
        "submitted": submitted,
        "diagnostics": diagnostics,
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
    mode.add_argument("--dry-run", action="store_true", help="plan only")
    parser.add_argument("--max-buys", type=int, default=2)
    parser.add_argument("--position-pct", type=float, default=DEFAULT_POSITION_PCT)
    parser.add_argument("--max-holdings", type=int, default=DEFAULT_MAX_HOLDINGS)
    args = parser.parse_args()

    try:
        summary = run_autopilot(
            execute=args.execute,
            max_buys=args.max_buys,
            position_pct=args.position_pct,
            max_holdings=args.max_holdings,
        )
        print(json.dumps(summary, indent=2))
        return 0
    except Exception as exc:
        print(f"FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
