"""
constitution.py — programmatic guardrails.

Small paper-trading guardrail check.

Run as a script:
    python3 code/constitution.py --check state/pending_trades/<trade_id>.json

Exit code 0 = PASS, non-zero = FAIL. Stdout is human-readable; stderr captures violations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
HALT_FILE = ROOT / ".HALT_TRADING"
PORTFOLIO_FILE = ROOT / "state" / "portfolio.json"
COMPLETED_DIR = ROOT / "state" / "completed_trades"

# ------ Hard limits from CLAUDE.md ------

MAX_POSITION_PCT = 0.25
MAX_TRADES_PER_WEEK = 50
MAX_TRADES_PER_DAY = 20
# Most we will deploy into the market (BUY orders) in a single day. Selling to
# raise cash / de-risk is never capped. Matches the user's "no more than $5,000
# invested per day" rule and keeps the bot from front-loading risk.
MAX_DAILY_INVEST_USD = 5000.0

FORBIDDEN_SUBSTRINGS_IN_SYMBOL = {
    # leveraged
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "TNA", "TZA", "LABU", "LABD",
    "FAS", "FAZ", "TMF", "TMV", "UVXY", "VIXY", "SVXY", "VXX",
    # inverse
    "SH", "PSQ", "DOG", "RWM", "SEF",
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    note: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"  [{status}] {self.name}{(': ' + self.note) if self.note else ''}"


class ConstitutionViolation(Exception):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConstitutionViolation(f"required file missing: {path}")
    with path.open() as f:
        return json.load(f)


def check_halt_switch() -> CheckResult:
    if HALT_FILE.exists():
        return CheckResult("halt_switch", False, f"{HALT_FILE.name} exists")
    return CheckResult("halt_switch", True)


def check_live_mode_forbidden() -> CheckResult:
    """Hard refuse if env says live."""
    endpoint = os.environ.get("ALPACA_ENDPOINT", "")
    if "paper-api.alpaca.markets" not in endpoint:
        return CheckResult(
            "live_mode_forbidden",
            False,
            f"ALPACA_ENDPOINT must be paper-api.alpaca.markets, got: {endpoint!r}",
        )
    if os.environ.get("LIVE_TRADING_AUTHORIZED", "").lower() == "true":
        # Even with the flag set, this code refuses. The flag is read elsewhere
        # for the LIVE promotion path; this script is paper-only.
        return CheckResult(
            "live_mode_forbidden",
            False,
            "LIVE_TRADING_AUTHORIZED is true; constitution.py operates paper-only.",
        )
    return CheckResult("live_mode_forbidden", True)


def check_forbidden_instrument(trade: dict) -> CheckResult:
    ticker = trade.get("ticker", "").upper()
    if ticker in FORBIDDEN_SUBSTRINGS_IN_SYMBOL:
        return CheckResult("forbidden_instrument", False, f"{ticker} is on denylist")
    # Sanity: refuse anything that looks like an option (ticker contains numbers + letters mixed)
    if any(c.isdigit() for c in ticker) and len(ticker) > 6:
        return CheckResult("forbidden_instrument", False, f"{ticker} looks like option/contract")
    return CheckResult("forbidden_instrument", True)


def check_order_type(trade: dict) -> CheckResult:
    order_type = trade.get("order_type", "LIMIT").upper()
    tif = trade.get("time_in_force", "DAY").upper()
    if order_type != "LIMIT":
        return CheckResult("order_type", False, f"order_type must be LIMIT, got {order_type}")
    if tif != "DAY":
        return CheckResult("order_type", False, f"time_in_force must be DAY, got {tif}")
    return CheckResult("order_type", True)


def check_side(trade: dict) -> CheckResult:
    side = trade.get("side", "").upper()
    if side not in {"BUY", "SELL"}:
        return CheckResult("side", False, f"side must be BUY or SELL, got {side!r}")
    return CheckResult("side", True)


def check_position_size(trade: dict, portfolio: dict) -> CheckResult:
    """Buys: post-trade position must be within the paper-practice cap."""
    if trade["side"].upper() != "BUY":
        return CheckResult("position_size", True, "sell — not applicable")

    equity = float(portfolio.get("equity", 0))
    if equity <= 0:
        return CheckResult("position_size", False, "portfolio equity unknown — cannot size-check")

    ticker = trade["ticker"].upper()
    notional = float(trade["limit_price"]) * float(trade["qty"])
    current_pos_value = 0.0
    for pos in portfolio.get("positions", []):
        if pos["ticker"].upper() == ticker:
            current_pos_value = float(pos["market_value"])
            break

    post_value = current_pos_value + notional
    post_pct = post_value / equity
    if post_pct > MAX_POSITION_PCT:
        return CheckResult(
            "position_size",
            False,
            f"post-trade position would be {post_pct:.1%} > {MAX_POSITION_PCT:.0%} cap",
        )
    return CheckResult("position_size", True, f"post-trade position {post_pct:.1%}")


def check_cash_available(trade: dict, portfolio: dict) -> CheckResult:
    if trade["side"].upper() != "BUY":
        return CheckResult("cash_available", True, "sell — not applicable")
    cash = float(portfolio.get("cash", 0))
    notional = float(trade["limit_price"]) * float(trade["qty"])
    if notional > cash:
        return CheckResult("cash_available", False, f"trade needs ${notional:.2f}, cash is ${cash:.2f}")
    return CheckResult("cash_available", True, f"trade needs ${notional:.2f}, cash is ${cash:.2f}")


def _trades_in_window(days: int) -> int:
    """Count trades placed in the last `days` calendar days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    if not COMPLETED_DIR.exists():
        return 0
    count = 0
    for f in COMPLETED_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            submitted = d.get("submitted_at_utc")
            if submitted and datetime.fromisoformat(submitted.replace("Z", "+00:00")) >= cutoff:
                count += 1
        except Exception:
            continue
    return count


def check_frequency(trade: dict) -> CheckResult:
    """Relaxed paper-trading frequency check."""
    day_count = _trades_in_window(1)
    week_count = _trades_in_window(7)
    if day_count >= MAX_TRADES_PER_DAY:
        return CheckResult("frequency", False, f"already {day_count} trades today (max {MAX_TRADES_PER_DAY})")
    if week_count >= MAX_TRADES_PER_WEEK:
        return CheckResult("frequency", False, f"already {week_count} trades this week (max {MAX_TRADES_PER_WEEK})")
    return CheckResult("frequency", True, f"day {day_count}/{MAX_TRADES_PER_DAY}, week {week_count}/{MAX_TRADES_PER_WEEK}")


def check_trade_reason(trade: dict) -> CheckResult:
    reason = trade.get("reason") or trade.get("thesis")
    if not reason or len(str(reason).strip()) < 10:
        return CheckResult("trade_reason", False, "missing short reason/thesis")
    return CheckResult("trade_reason", True)


def _todays_completed() -> list[dict]:
    """Trades submitted today (UTC), read from completed_trades."""
    today = datetime.now(timezone.utc).date().isoformat()
    out: list[dict] = []
    if not COMPLETED_DIR.exists():
        return out
    for f in COMPLETED_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            if str(d.get("submitted_at_utc", ""))[:10] == today:
                out.append(d)
        except Exception:
            continue
    return out


def check_daily_invest_cap(trade: dict) -> CheckResult:
    """Total BUY notional submitted today + this order must stay under the cap.
    Sells are exempt — reducing risk is never throttled."""
    if trade["side"].upper() != "BUY":
        return CheckResult("daily_invest_cap", True, "sell — not applicable")
    notional = float(trade["limit_price"]) * float(trade["qty"])
    prior = sum(
        float(d.get("limit_price", 0)) * float(d.get("qty", 0))
        for d in _todays_completed() if str(d.get("side", "")).upper() == "BUY"
    )
    if prior + notional > MAX_DAILY_INVEST_USD + 1e-6:
        return CheckResult("daily_invest_cap", False,
                           f"today's buys ${prior:,.0f} + ${notional:,.0f} would exceed ${MAX_DAILY_INVEST_USD:,.0f}/day cap")
    return CheckResult("daily_invest_cap", True, f"${prior + notional:,.0f} of ${MAX_DAILY_INVEST_USD:,.0f}/day")


def check_no_day_trade(trade: dict) -> CheckResult:
    """No same-day round trips: if the opposite side already traded this ticker
    today, refuse. Keeps the bot out of 'day trading' (Robinhood agentic = none)."""
    ticker = trade.get("ticker", "").upper()
    side = trade.get("side", "").upper()
    opposite = "SELL" if side == "BUY" else "BUY"
    for d in _todays_completed():
        if str(d.get("ticker", "")).upper() == ticker and str(d.get("side", "")).upper() == opposite:
            return CheckResult("no_day_trade", False,
                               f"{ticker} already had a {opposite} today — a {side} now would be a same-day round trip (day trade)")
    return CheckResult("no_day_trade", True)


def run_all_checks(trade_path: Path) -> tuple[bool, list[CheckResult]]:
    trade = _load_json(trade_path)
    portfolio = _load_json(PORTFOLIO_FILE) if PORTFOLIO_FILE.exists() else {"equity": 0, "cash": 0, "positions": []}

    results: list[CheckResult] = [
        check_halt_switch(),
        check_live_mode_forbidden(),
        check_forbidden_instrument(trade),
        check_side(trade),
        check_order_type(trade),
        check_position_size(trade, portfolio),
        check_cash_available(trade, portfolio),
        check_frequency(trade),
        check_daily_invest_cap(trade),
        check_no_day_trade(trade),
        check_trade_reason(trade),
    ]
    passed = all(r.passed for r in results)
    return passed, results


def main() -> int:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--check", required=True, help="path to trade JSON")
    args = p.parse_args()
    trade_path = Path(args.check)
    if not trade_path.exists():
        print(f"ERROR: trade file not found: {trade_path}", file=sys.stderr)
        return 2

    passed, results = run_all_checks(trade_path)
    print(f"Constitution check: {'PASS' if passed else 'FAIL'} — {trade_path.name}")
    for r in results:
        print(r)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
