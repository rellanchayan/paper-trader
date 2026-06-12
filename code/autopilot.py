"""
Daily paper-trading autopilot.

Strategy v2 — runs a momentum book the way a careful wealth manager would:
- Trend + relative-strength entries from state/watchlist.txt.
- Diversification gates: sector caps and a similarity (correlation) check.
- Position sizing by risk (ATR-based), not equal dollars.
- Exits: hard stop, trailing stop, trend break, plus overweight trims.
- Regime awareness: market trend, market stress, and a drawdown brake.
- Paper only. LIMIT + DAY only. Tunables live in state/strategy_params.json.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PENDING = STATE / "pending_trades"
COMPLETED = STATE / "completed_trades"
RUNS = STATE / "autopilot_runs"
WATCHLIST = STATE / "watchlist.txt"
SECTORS_FILE = STATE / "sectors.json"
HALT_FILE = ROOT / ".HALT_TRADING"

DEFAULT_POSITION_PCT = 0.10
DEFAULT_MAX_HOLDINGS = 8

# Rebalancing: trim any position that grows past TRIM_ABOVE back to TRIM_TARGET.
TRIM_ABOVE = 0.20
TRIM_TARGET = 0.15
# Don't chase: skip a buy when the live ask gapped this far above the signal close.
MAX_CHASE = 1.02
# Market stress: SPY 20d realized volatility (annualized) above this -> half-size buys.
SPY_VOL_CAUTION = 0.30
# Drawdown brake: no new buys while the account is this far below its high-water mark.
DRAWDOWN_BRAKE = 0.10

PARAMS_FILE = STATE / "strategy_params.json"
PARAM_DEFAULTS = {
    "min_outperformance": 0.02,  # how much a stock must beat SPY by (20d) to qualify
    "stop_loss_pct": -0.08,      # hard stop: sell when paper loss is worse than this
    "cooldown_days": 5,          # days to wait before re-buying something we sold
    "atr_stop_mult": 3.0,        # trailing stop distance, in ATRs below the recent high
    "risk_per_trade": 0.005,     # fraction of equity one trade is allowed to lose
    "max_corr": 0.85,            # skip buys this correlated with an existing holding
    "max_per_sector": 3,         # max holdings per sector (index ETFs exempt)
}
# learn.py tunes the params above, but can NEVER push them past these bounds.
PARAM_BOUNDS = {
    "min_outperformance": (0.01, 0.06),
    "stop_loss_pct": (-0.12, -0.05),
    "cooldown_days": (3, 10),
    "atr_stop_mult": (2.0, 4.0),
    "risk_per_trade": (0.003, 0.01),
    "max_corr": (0.70, 0.95),
    "max_per_sector": (1, 4),
}


def load_params() -> dict:
    data: dict = {}
    if PARAMS_FILE.exists():
        try:
            data = json.loads(PARAMS_FILE.read_text())
        except Exception:
            data = {}
    params = {}
    for key, default in PARAM_DEFAULTS.items():
        try:
            value = float(data.get(key, default))
        except (TypeError, ValueError):
            value = default
        lo, hi = PARAM_BOUNDS[key]
        params[key] = min(max(value, lo), hi)
    params["cooldown_days"] = int(params["cooldown_days"])
    params["max_per_sector"] = int(params["max_per_sector"])
    return params


@dataclass
class Signal:
    ticker: str
    action: str
    score: float
    reason: str
    risk: str
    limit_price: float
    qty: int = 0
    exit_rule: str = ""
    initial_stop: float = 0.0


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


def load_sectors() -> dict[str, str]:
    if not SECTORS_FILE.exists():
        return {}
    try:
        data = json.loads(SECTORS_FILE.read_text())
        return {k.upper(): str(v) for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def sma(values: list[float], n: int) -> float:
    return sum(values[-n:]) / n


def atr(bars: list[dict], n: int = 14) -> float:
    """Average True Range — the stock's typical daily price swing, in dollars."""
    if len(bars) < 2:
        return 0.0
    window = bars[-(n + 1):]
    trs = []
    for prev, cur in zip(window[:-1], window[1:]):
        high, low, prev_close = float(cur["h"]), float(cur["l"]), float(prev["c"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs) if trs else 0.0


def daily_returns(closes: list[float], n: int = 60) -> list[float]:
    closes = closes[-(n + 1):]
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]


def correlation(a: list[float], b: list[float]) -> float:
    """How similarly two return series move. 1 = identical, 0 = unrelated."""
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def annualized_vol(closes: list[float], n: int = 20) -> float:
    """Realized volatility of daily returns, annualized (0.30 = 30%/yr swings)."""
    rets = daily_returns(closes, n)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


_BARS_CACHE: dict[str, list[dict]] = {}


def get_bars(ticker: str, days: int = 240) -> list[dict]:
    key = f"{ticker}:{days}"
    if key not in _BARS_CACHE:
        data = run_json([sys.executable, "code/alpaca_client.py", "--bars", ticker, "--days", str(days)])
        _BARS_CACHE[key] = data.get("bars", [])
    return _BARS_CACHE[key]


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
    if signal.exit_rule:
        trade["exit_rule"] = signal.exit_rule
    if signal.initial_stop > 0:
        trade["initial_stop"] = signal.initial_stop
    path = PENDING / f"{trade['trade_id']}.json"
    path.write_text(json.dumps(trade, indent=2))
    return path


def submit_trade(path: Path) -> dict:
    run_text([sys.executable, "code/constitution.py", "--check", str(path)])
    return run_json([sys.executable, "code/alpaca_client.py", "--submit", str(path)])


def recently_sold(days: int = 5) -> set[str]:
    """Tickers we exited in the last `days` days — skip re-buying them (avoids whipsaw).

    Rebalancing trims don't count: a trim means the position grew too big, not
    that we lost faith in it.
    """
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
                and trade.get("exit_rule") != "rebalance_trim"
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


def analyze_ticker(ticker: str, spy_ret20: float, params: dict) -> tuple[Signal | None, dict]:
    bars = get_bars(ticker)
    if len(bars) < 210:
        return None, {"ticker": ticker, "skip": "not enough bars"}

    closes = [float(b["c"]) for b in bars]
    close = closes[-1]
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    ret20 = close / closes[-21] - 1
    ret63 = close / closes[-64] - 1
    ret126 = close / closes[-127] - 1
    vol = annualized_vol(closes, 60)

    data = {
        "ticker": ticker,
        "close": round(close, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "ret20": round(ret20, 4),
        "ret63": round(ret63, 4),
        "ret126": round(ret126, 4),
        "vol": round(vol, 3),
        "atr": round(atr(bars), 2),
    }

    min_outperformance = params["min_outperformance"]
    uptrend = close > sma50 > sma200
    strong = ret20 >= min_outperformance and (ret20 - spy_ret20) >= min_outperformance
    if uptrend and strong:
        # Blended momentum (1m/3m/6m), divided by volatility: rewards steady
        # climbers over wild swingers — risk-adjusted relative strength.
        momentum = 0.2 * ret20 + 0.5 * ret63 + 0.3 * ret126
        score = momentum / max(vol, 0.10)
        reason = (
            f"{ticker} uptrend, stronger than the market: 1m {ret20:+.1%} vs SPY {spy_ret20:+.1%}, "
            f"3m {ret63:+.1%}, 6m {ret126:+.1%}, volatility {vol:.0%}."
        )
        risk = "Momentum can reverse; protected by a hard stop and a trailing stop."
        return Signal(ticker, "BUY", score, reason, risk, close), data

    return None, data


def sell_signal_for_position(pos: dict, params: dict) -> Signal | None:
    ticker = str(pos["ticker"]).upper()
    bars = get_bars(ticker)
    if len(bars) < 60:
        return None
    closes = [float(b["c"]) for b in bars]
    close = closes[-1]
    sma50 = sma(closes, 50)
    plpc = float(pos.get("unrealized_plpc") or 0)

    stopped = plpc <= params["stop_loss_pct"]
    a = atr(bars)
    high22 = max(closes[-22:])
    trail = high22 - params["atr_stop_mult"] * a
    trail_hit = a > 0 and close < trail
    trend_break = close < sma50

    if not (stopped or trail_hit or trend_break):
        return None
    qty = math.floor(float(pos["qty"]))
    if qty < 1:
        return None
    limit_price = get_quote_price(ticker, "SELL", close)
    if stopped:
        exit_rule = "stop_loss"
        reason = f"{ticker} sell: paper loss {plpc:.1%} breached the {params['stop_loss_pct']:.0%} stop."
    elif trail_hit:
        exit_rule = "trail_stop"
        reason = (f"{ticker} sell: ${close:.2f} dropped more than {params['atr_stop_mult']:g} ATRs "
                  f"below its 22d high ${high22:.2f} (trail ${trail:.2f}) — protecting gains.")
    else:
        exit_rule = "trend_break"
        reason = f"{ticker} sell: price ${close:.2f} fell below its 50d average ${sma50:.2f}."
    risk = "May sell before a rebound."
    return Signal(ticker, "SELL", 1.0, reason, risk, limit_price, qty, exit_rule=exit_rule)


def trim_signals(positions: list[dict], equity: float, already_selling: set[str]) -> list[Signal]:
    """Rebalancing: cut any position that has grown past TRIM_ABOVE back to TRIM_TARGET."""
    out: list[Signal] = []
    if equity <= 0:
        return out
    for pos in positions:
        ticker = str(pos["ticker"]).upper()
        if ticker in already_selling:
            continue
        qty_held = float(pos["qty"])
        market_value = float(pos["market_value"])
        if qty_held <= 0 or market_value <= 0:
            continue
        weight = market_value / equity
        if weight <= TRIM_ABOVE:
            continue
        price_now = market_value / qty_held
        excess = market_value - TRIM_TARGET * equity
        qty = min(math.floor(excess / price_now), math.floor(qty_held))
        if qty < 1:
            continue
        limit_price = get_quote_price(ticker, "SELL", price_now)
        reason = (f"{ticker} has grown to {weight:.0%} of the account — "
                  f"trimming back toward {TRIM_TARGET:.0%} (rebalancing).")
        out.append(Signal(ticker, "SELL", 1.0, reason, "Trimmed shares may keep rising.",
                          limit_price, qty, exit_rule="rebalance_trim"))
    return out


def run_autopilot(execute: bool, max_buys: int, position_pct: float, max_holdings: int) -> dict:
    if HALT_FILE.exists():
        return {"status": "halted", "reason": ".HALT_TRADING exists"}

    params = load_params()
    reconciled = reconcile_orders()
    portfolio = run_json([sys.executable, "code/alpaca_client.py", "--positions"])
    equity = float(portfolio.get("equity") or 0)
    cash = float(portfolio.get("cash") or 0)
    drawdown = float(portfolio.get("drawdown_from_hwm") or 0)
    positions = portfolio.get("positions", [])
    held = {str(p["ticker"]).upper(): p for p in positions}

    spy_bars = get_bars("SPY")
    if len(spy_bars) < 25:
        raise RuntimeError("Not enough SPY bars for benchmark signal")
    spy_closes = [float(b["c"]) for b in spy_bars]
    spy_ret20 = spy_closes[-1] / spy_closes[-21] - 1
    spy_vol = annualized_vol(spy_closes, 20)

    # Read the room before buying anything.
    regime, regime_reason = "normal", "market trend up, stress normal"
    if len(spy_closes) >= 200 and spy_closes[-1] < sma(spy_closes, 200):
        regime, regime_reason = "defensive", "SPY is below its 200d average — no new buys"
    elif drawdown >= DRAWDOWN_BRAKE:
        regime, regime_reason = "defensive", (
            f"account is {drawdown:.0%} below its high-water mark — drawdown brake, no new buys")
    elif spy_vol >= SPY_VOL_CAUTION:
        regime, regime_reason = "cautious", (
            f"market volatility is elevated ({spy_vol:.0%} annualized) — buys at half size")

    sectors = load_sectors()

    def sector_of(ticker: str) -> str:
        return sectors.get(ticker, f"unknown:{ticker}")

    diagnostics: list[dict] = []
    # One ticker's broken data feed must never kill the whole run: tolerate
    # per-ticker failures, record them, and keep going.
    sells: list[Signal] = []
    for p in positions:
        try:
            if (s := sell_signal_for_position(p, params)):
                sells.append(s)
        except Exception as exc:
            diagnostics.append(
                {"ticker": str(p.get("ticker", "?")).upper(), "error": f"sell check failed: {exc}"})
    selling = {s.ticker for s in sells}
    trims = trim_signals(positions, equity, selling)

    cooldown = recently_sold(params["cooldown_days"])
    buy_candidates: list[Signal] = []
    if regime == "defensive":
        diagnostics.append({"skip_all_buys": regime_reason})
    elif len(held) < max_holdings:
        for ticker in load_watchlist():
            if ticker in held:
                continue
            if ticker in cooldown:
                diagnostics.append(
                    {"ticker": ticker, "skip": f"sold within last {params['cooldown_days']} days (cooldown)"})
                continue
            try:
                signal, data = analyze_ticker(ticker, spy_ret20, params)
            except Exception as exc:
                diagnostics.append({"ticker": ticker, "error": f"analysis failed: {exc}"})
                continue
            diagnostics.append(data)
            if signal:
                buy_candidates.append(signal)

    buy_candidates.sort(key=lambda s: s.score, reverse=True)
    buys: list[Signal] = []
    remaining_slots = max(0, max_holdings - len(held))
    size_factor = 0.5 if regime == "cautious" else 1.0
    available_cash = cash
    # Sector counts include everything held right now (even names being sold
    # today — they are still exposure until the sell fills).
    sector_count = Counter(sector_of(t) for t in held if sector_of(t) != "index")
    # Index ETFs are deliberately exempt from the similarity check: they are
    # the diversified "core" (core-satellite style), and almost every large
    # stock correlates with them. The sector cap is what limits piling into
    # one industry alongside the core.
    held_for_corr = [t for t in held if sector_of(t) != "index" and t not in selling]

    for signal in buy_candidates:
        if len(buys) >= min(max_buys, remaining_slots):
            break
        ticker = signal.ticker
        sec = sector_of(ticker)
        try:
            if sec != "index" and sector_count[sec] >= params["max_per_sector"]:
                diagnostics.append(
                    {"ticker": ticker, "skip": f"already {sector_count[sec]} holdings in {sec} (sector cap)"})
                continue

            cand_rets = daily_returns([float(b["c"]) for b in get_bars(ticker)], 60)
            too_similar = None
            for other in held_for_corr + [b.ticker for b in buys]:
                corr = correlation(cand_rets, daily_returns([float(b["c"]) for b in get_bars(other)], 60))
                if corr > params["max_corr"]:
                    too_similar = (other, corr)
                    break
            if too_similar:
                diagnostics.append(
                    {"ticker": ticker,
                     "skip": f"moves too much like {too_similar[0]} already held (correlation {too_similar[1]:.2f})"})
                continue

            price = get_quote_price(ticker, "BUY", signal.limit_price)
            if price > signal.limit_price * MAX_CHASE:
                diagnostics.append(
                    {"ticker": ticker, "skip": f"ask ${price:.2f} gapped >2% above signal close — not chasing"})
                continue

            stop_distance = params["atr_stop_mult"] * atr(get_bars(ticker))
            if stop_distance <= 0:
                diagnostics.append({"ticker": ticker, "skip": "cannot size position (no volatility data)"})
                continue
            # Risk-based sizing: every position risks about the same slice of
            # the account if its stop is hit. Calm stocks get more shares,
            # jumpy ones fewer — capped by position size and available cash.
            qty_risk = math.floor(equity * params["risk_per_trade"] * size_factor / stop_distance)
            qty_cash = math.floor(min(equity * position_pct, available_cash) / price)
            qty = min(qty_risk, qty_cash)
            if qty < 1:
                diagnostics.append({"ticker": ticker, "skip": "position would be too small to bother"})
                continue
        except Exception as exc:
            diagnostics.append({"ticker": ticker, "error": f"buy checks failed: {exc}"})
            continue

        signal.qty = qty
        signal.limit_price = price
        signal.initial_stop = round(price - stop_distance, 2)
        buys.append(signal)
        available_cash -= qty * price
        sector_count[sec] += 1

    planned = sells + trims + buys
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
        "spy_vol": round(spy_vol, 3),
        "drawdown_from_hwm": round(drawdown, 4),
        "regime": regime,
        "regime_reason": regime_reason,
        "params": params,
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
