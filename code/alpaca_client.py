"""
alpaca_client.py — the ONLY module that talks to Alpaca.

Hard-wired to paper. The constitution check inside refuses to operate against
the live endpoint.

CLI usage:
    python3 code/alpaca_client.py --healthcheck
    python3 code/alpaca_client.py --positions
    python3 code/alpaca_client.py --quote AAPL
    python3 code/alpaca_client.py --bars AAPL --days 5
    python3 code/alpaca_client.py --is-open
    python3 code/alpaca_client.py --submit state/pending_trades/<trade_id>.json
    python3 code/alpaca_client.py --order-status <order_id>
    python3 code/alpaca_client.py --cancel-all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from constitution import run_all_checks

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

PORTFOLIO_FILE = ROOT / "state" / "portfolio.json"
HISTORY_FILE = ROOT / "state" / "portfolio_history.jsonl"
HALT_FILE = ROOT / ".HALT_TRADING"

# Hard-wired safety: this constant is checked against the live env. The script
# REFUSES to run anything that targets live, regardless of the env variable.
PAPER_HOST_REQUIRED = "paper-api.alpaca.markets"


def _require_paper() -> None:
    endpoint = os.environ.get("ALPACA_ENDPOINT", "")
    if PAPER_HOST_REQUIRED not in endpoint:
        print(
            f"REFUSED: ALPACA_ENDPOINT must contain '{PAPER_HOST_REQUIRED}'. "
            f"Got: {endpoint!r}. Live trading disabled in this build.",
            file=sys.stderr,
        )
        sys.exit(99)


def _client():
    """Lazy import so this module can be imported without alpaca-py installed for tests."""
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        print("alpaca-py not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(98)

    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY missing. See .env.example.", file=sys.stderr)
        sys.exit(97)
    # paper=True is enforced; the SDK then uses paper endpoint regardless of env var.
    return TradingClient(key, secret, paper=True)


def _data_client():
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError:
        print("alpaca-py not installed.", file=sys.stderr)
        sys.exit(98)
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    return StockHistoricalDataClient(key, secret)


def healthcheck() -> int:
    _require_paper()
    try:
        c = _client()
        acct = c.get_account()
        print(f"OK — paper account: {acct.account_number}, equity ${float(acct.equity):,.2f}, status {acct.status}")
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def is_market_open() -> int:
    _require_paper()
    try:
        c = _client()
        clock = c.get_clock()
        print(json.dumps({
            "is_open": bool(clock.is_open),
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
            "timestamp": clock.timestamp.isoformat(),
        }))
        return 0 if clock.is_open else 0  # informational, not a failure
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def positions() -> int:
    _require_paper()
    try:
        c = _client()
        acct = c.get_account()
        pos_list = c.get_all_positions()
        equity = float(acct.equity)
        cash = float(acct.cash)

        out_positions = []
        for p in pos_list:
            mv = float(p.market_value)
            out_positions.append({
                "ticker": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": mv,
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "pct_of_portfolio": mv / equity if equity > 0 else 0.0,
            })

        snapshot = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "equity": equity,
            "cash": cash,
            "buying_power": float(acct.buying_power),
            "positions": out_positions,
            "drawdown_from_hwm": _compute_drawdown(equity),
        }

        PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORTFOLIO_FILE.write_text(json.dumps(snapshot, indent=2))
        with HISTORY_FILE.open("a") as f:
            f.write(json.dumps({"timestamp_utc": snapshot["timestamp_utc"], "equity": equity, "cash": cash}) + "\n")

        print(json.dumps(snapshot, indent=2))
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def _compute_drawdown(current_equity: float) -> float:
    if not HISTORY_FILE.exists():
        return 0.0
    hwm = current_equity
    try:
        with HISTORY_FILE.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    e = float(d.get("equity", 0))
                    if e > hwm:
                        hwm = e
                except Exception:
                    continue
    except Exception:
        return 0.0
    if hwm <= 0:
        return 0.0
    return max(0.0, (hwm - current_equity) / hwm)


def quote(ticker: str) -> int:
    _require_paper()
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.data.enums import DataFeed
        dc = _data_client()
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker, feed=DataFeed.IEX)
        result = dc.get_stock_latest_quote(req)
        q = result[ticker]
        print(json.dumps({
            "ticker": ticker,
            "bid": float(q.bid_price),
            "ask": float(q.ask_price),
            "bid_size": int(q.bid_size),
            "ask_size": int(q.ask_size),
            "timestamp": q.timestamp.isoformat(),
        }))
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def bars(ticker: str, days: int) -> int:
    _require_paper()
    try:
        from datetime import timedelta
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.enums import DataFeed
        from alpaca.data.timeframe import TimeFrame
        dc = _data_client()
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days * 2 + 5)  # buffer for non-trading days
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start, end=end, feed=DataFeed.IEX)
        bars_data = dc.get_stock_bars(req).data.get(ticker, [])
        out = []
        for b in bars_data[-days:]:
            out.append({
                "date": b.timestamp.date().isoformat(),
                "o": float(b.open), "h": float(b.high), "l": float(b.low), "c": float(b.close),
                "v": int(b.volume),
            })
        print(json.dumps({"ticker": ticker, "days": len(out), "bars": out}, indent=2))
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def submit(trade_json_path: str) -> int:
    _require_paper()
    if HALT_FILE.exists():
        print("REFUSED: .HALT_TRADING exists", file=sys.stderr)
        return 95

    path = Path(trade_json_path)
    if not path.exists():
        print(f"FAIL: trade file not found: {path}", file=sys.stderr)
        return 2
    trade = json.loads(path.read_text())

    required = ["trade_id", "ticker", "side", "qty", "limit_price", "reason"]
    for k in required:
        if k not in trade:
            print(f"FAIL: trade JSON missing required key: {k}", file=sys.stderr)
            return 3

    passed, checks = run_all_checks(path)
    if not passed:
        print("REFUSED: trade failed checks", file=sys.stderr)
        for check in checks:
            print(check, file=sys.stderr)
        return 90

    # Order type sanity
    if trade.get("order_type", "LIMIT").upper() != "LIMIT":
        print("REFUSED: only LIMIT orders supported", file=sys.stderr)
        return 94
    if trade.get("time_in_force", "DAY").upper() != "DAY":
        print("REFUSED: only DAY orders supported", file=sys.stderr)
        return 94

    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        c = _client()
        side = OrderSide.BUY if trade["side"].upper() == "BUY" else OrderSide.SELL
        req = LimitOrderRequest(
            symbol=trade["ticker"].upper(),
            qty=float(trade["qty"]),
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(trade["limit_price"]),
        )
        order = c.submit_order(req)

        # Update trade JSON with submission state
        trade["status"] = "submitted"
        trade["alpaca_order_id"] = str(order.id)
        trade["submitted_at_utc"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(trade, indent=2))

        completed = ROOT / "state" / "completed_trades" / path.name
        completed.parent.mkdir(parents=True, exist_ok=True)
        path.replace(completed)

        print(json.dumps({
            "trade_id": trade["trade_id"],
            "alpaca_order_id": str(order.id),
            "status": str(order.status),
            "submitted_at": trade["submitted_at_utc"],
            "saved_to": str(completed.relative_to(ROOT)),
        }))
        return 0
    except Exception as e:
        print(f"SUBMIT FAILED — {e}", file=sys.stderr)
        return 1


def order_status(order_id: str) -> int:
    _require_paper()
    try:
        c = _client()
        o = c.get_order_by_id(order_id)
        out = {
            "order_id": str(o.id),
            "symbol": o.symbol,
            "status": str(o.status),
            "qty": float(o.qty),
            "filled_qty": float(o.filled_qty or 0),
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "limit_price": float(o.limit_price) if o.limit_price else None,
            "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        }
        print(json.dumps(out, indent=2))
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def cancel_all() -> int:
    _require_paper()
    try:
        c = _client()
        canceled = c.cancel_orders()
        print(json.dumps({"canceled_count": len(canceled) if canceled else 0}))
        return 0
    except Exception as e:
        print(f"FAIL — {e}", file=sys.stderr)
        return 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--healthcheck", action="store_true")
    p.add_argument("--positions", action="store_true")
    p.add_argument("--quote", metavar="TICKER")
    p.add_argument("--bars", metavar="TICKER")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--is-open", action="store_true")
    p.add_argument("--submit", metavar="TRADE_JSON")
    p.add_argument("--order-status", metavar="ORDER_ID")
    p.add_argument("--cancel-all", action="store_true")
    args = p.parse_args()

    if args.healthcheck:
        return healthcheck()
    if args.is_open:
        return is_market_open()
    if args.positions:
        return positions()
    if args.quote:
        return quote(args.quote.upper())
    if args.bars:
        return bars(args.bars.upper(), args.days)
    if args.submit:
        return submit(args.submit)
    if args.order_status:
        return order_status(args.order_status)
    if args.cancel_all:
        return cancel_all()

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
