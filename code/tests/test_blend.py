"""
Unit tests for the BLEND strategy brain (75% stocks / 25% ETFs) and the new
constitution guards. Pure logic + a tmp-dir constitution test — no network.

Run:  python3 code/tests/test_blend.py
"""

import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blend_engine  # noqa: E402
import constitution  # noqa: E402

CONFIG = {
    "stock_sleeve_pct": 0.75, "etf_sleeve_pct": 0.25, "daily_invest_cap_usd": 5000,
    "etf_ballast": {"SPY": 0.125, "IEF": 0.0625, "GLD": 0.0625},
    "harbor_tickers": ["SGOV", "BIL", "USFR", "TBLL", "GBIL"],
    "max_stock_names": 12, "max_per_name_pct": 0.10, "max_per_sector": 2,
    "max_corr": 0.85, "min_outperformance": 0.0,
    "trend_lookback_days": 200, "trend_gate_on_threshold": 1.02, "trend_gate_off_threshold": 0.98,
    "stock_trend_break_sma": 50, "vol_brake_threshold": 0.18, "vol_brake_lookback_days": 20,
    "drawdown_brake_threshold": -0.15, "no_trade_drift_band": 0.20, "max_single_holding_pct": 0.24,
    "limit_buy_above_ask_pct": 0.003, "limit_sell_below_bid_pct": 0.003,
}

# A diversified set of uptrending stocks across sectors.
STOCKS = {
    "AAPL": "technology", "MSFT": "technology", "JPM": "financials", "BAC": "financials",
    "XOM": "energy", "CVX": "energy", "JNJ": "healthcare", "UNH": "healthcare",
    "CAT": "industrials", "BA": "industrials", "PG": "consumer-staples", "KO": "consumer-staples",
    "AMZN": "consumer-discretionary", "MCD": "consumer-discretionary",
}
SECTORS = {**STOCKS, "SPY": "index", "IEF": "index", "GLD": "index",
           "SGOV": "index", "BIL": "index", "USFR": "index", "TBLL": "index", "GBIL": "index"}
WATCHLIST = sorted(STOCKS)


def up(n=260, start=80.0, step=0.4, seed=0):
    # Strong uptrend (step*i dominates so close>sma50>sma200) plus an
    # idiosyncratic wiggle per seed, so cross-name correlation is realistic
    # (< the 0.85 gate) instead of a pathological 1.0 from straight lines.
    f = 0.10 + 0.045 * seed
    ph = seed * 1.7
    return [start + step * i + 5.0 * math.sin(i * f + ph) for i in range(n)]


def down(n=260, start=200.0, step=0.5):
    return [start - step * i for i in range(n)]


def providers(closes_map):
    def bars(t):
        if t in closes_map:
            return closes_map[t]
        return up(seed=(hash(t) % 7))
    def price(t, side, fallback):
        return float(fallback) if fallback else 0.0
    return bars, price


def base_closes():
    m = {t: up(seed=i) for i, t in enumerate(WATCHLIST)}
    m["SPY"] = up(seed=3); m["IEF"] = up(seed=4); m["GLD"] = up(seed=5)
    return m


def plan(**over):
    bars, price = providers(over.pop("closes", base_closes()))
    args = dict(equity=100000, cash=100000, positions={}, drawdown_from_hwm=0.0,
                prior_gates={}, invested_today=0.0, sold_today=set(), bought_today=set(),
                config=CONFIG, sectors=SECTORS, watchlist=WATCHLIST,
                bars_provider=bars, price_provider=price)
    args.update(over)
    return blend_engine.plan_trades(**args)


# ---- engine tests ----

def test_daily_cap_respected():
    p = plan()
    spend = sum(t["qty"] * t["limit_price"] for t in p["trades"] if t["side"] == "BUY")
    assert spend <= 5000 + 1, f"daily buys {spend} exceed $5000 cap"


def test_daily_cap_accounts_for_prior_buys():
    p = plan(invested_today=4800.0)
    spend = sum(t["qty"] * t["limit_price"] for t in p["trades"] if t["side"] == "BUY")
    assert spend <= 200 + 1, f"only ~$200 of headroom should remain, got {spend}"


def test_sector_cap_in_stock_sleeve():
    # Give it cash so many names qualify; ensure <=2 per sector among stock buys.
    p = plan(cash=100000, invested_today=0)
    # inspect targets (not just today's capped buys) via diagnostics-free path: re-derive from targets
    stock_targets = {k: v for k, v in p["targets"].items() if k in STOCKS}
    from collections import Counter
    sec = Counter(SECTORS[t] for t in stock_targets)
    assert all(c <= 2 for c in sec.values()), f"sector cap violated: {sec}"


def test_per_name_cap():
    for t, v in plan()["targets"].items():
        if t in STOCKS:
            assert v <= 100000 * 0.10 + 1, f"{t} target {v} exceeds 10% name cap"


def test_75_25_tilt():
    # The stock sleeve should clearly dominate (~3:1 tilt). It may under-fill
    # toward T-bills when too few diversified names qualify — that's by design.
    p = plan()
    stock = sum(v for k, v in p["targets"].items() if k in STOCKS)
    etf = sum(v for k, v in p["targets"].items() if k in ("SPY", "IEF", "GLD"))
    assert etf > 0, "ETF ballast should be funded"
    assert stock > 0.5 * 100000, f"stock sleeve should be the majority of the book, got {stock:.0f}"
    assert stock > 2 * etf, f"stock sleeve should be ~3x the ETF sleeve, got stock={stock:.0f} etf={etf:.0f}"


def test_market_riskoff_no_stock_picking():
    closes = base_closes(); closes["SPY"] = down()
    p = plan(closes=closes)
    assert p["regime"] == "risk_off"
    assert not any(t["side"] == "BUY" and t["ticker"] in STOCKS for t in p["trades"]), \
        "must not pick stocks while the market is below its 200d"


def test_drawdown_brake_sells_risk_and_buys_no_stocks():
    closes = base_closes()
    pos = {"AAPL": {"qty": 50, "market_value": 8000, "price": 160.0}}
    p = plan(closes=closes, positions=pos, cash=0, drawdown_from_hwm=0.20)
    assert p["regime"] == "defensive"
    assert any(t["ticker"] == "AAPL" and t["side"] == "SELL" for t in p["trades"])
    assert not any(t["side"] == "BUY" and t["ticker"] in STOCKS for t in p["trades"])


def test_no_same_day_round_trip():
    # If we sold AAPL today, the engine must not buy it back today.
    p = plan(sold_today={"AAPL"})
    assert not any(t["ticker"] == "AAPL" and t["side"] == "BUY" for t in p["trades"])
    # If we bought MSFT today, the engine must not sell it today.
    pos = {"MSFT": {"qty": 10, "market_value": 3000, "price": 300.0}}
    closes = base_closes(); closes["MSFT"] = down()  # would normally trigger a trend-break sell
    p2 = plan(closes=closes, positions=pos, bought_today={"MSFT"})
    assert not any(t["ticker"] == "MSFT" and t["side"] == "SELL" for t in p2["trades"])


def test_stock_trend_break_exit():
    pos = {"AAPL": {"qty": 50, "market_value": 8000, "price": 160.0}}
    closes = base_closes(); closes["AAPL"] = down()  # AAPL now below its 50d
    p = plan(closes=closes, positions=pos)
    assert any(t["ticker"] == "AAPL" and t["side"] == "SELL" and t["exit_rule"] == "trend_break"
               for t in p["trades"])


def test_sells_before_buys():
    pos = {"NVDA": {"qty": 10, "market_value": 5000, "price": 500.0}}  # not in watchlist -> exit
    closes = base_closes(); closes["NVDA"] = up(seed=2)
    p = plan(closes=closes, positions=pos)
    sides = [t["side"] for t in p["trades"]]
    last_sell = max((i for i, s in enumerate(sides) if s == "SELL"), default=-1)
    first_buy = min((i for i, s in enumerate(sides) if s == "BUY"), default=len(sides))
    assert last_sell < first_buy


# ---- constitution guard tests (tmp dir, no real state touched) ----

def _with_tmp_completed(fn):
    with tempfile.TemporaryDirectory() as d:
        orig = constitution.COMPLETED_DIR
        constitution.COMPLETED_DIR = Path(d)
        try:
            fn(Path(d))
        finally:
            constitution.COMPLETED_DIR = orig


def test_constitution_daily_cap():
    def body(d):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).isoformat()
        (d / "a.json").write_text(json.dumps({"ticker": "AAPL", "side": "BUY", "qty": 10,
            "limit_price": 480.0, "submitted_at_utc": today}))  # $4,800 already
        r = constitution.check_daily_invest_cap({"ticker": "MSFT", "side": "BUY", "qty": 1, "limit_price": 300.0})
        assert not r.passed, "should reject buy that pushes past $5,000/day"
        r2 = constitution.check_daily_invest_cap({"ticker": "MSFT", "side": "BUY", "qty": 1, "limit_price": 150.0})
        assert r2.passed, "a $150 buy fits under the remaining ~$200"
    _with_tmp_completed(body)


def test_constitution_no_day_trade():
    def body(d):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).isoformat()
        (d / "a.json").write_text(json.dumps({"ticker": "AAPL", "side": "SELL", "qty": 5,
            "limit_price": 200.0, "submitted_at_utc": today}))
        r = constitution.check_no_day_trade({"ticker": "AAPL", "side": "BUY", "qty": 1, "limit_price": 200.0})
        assert not r.passed, "buying a ticker sold earlier today is a day trade — reject"
        r2 = constitution.check_no_day_trade({"ticker": "MSFT", "side": "BUY", "qty": 1, "limit_price": 200.0})
        assert r2.passed
    _with_tmp_completed(body)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}"); passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} tests passed")
    raise SystemExit(0 if passed == len(fns) else 1)
