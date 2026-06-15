"""
Unit tests for the DTA brain — pure logic, no network.

Run:  python3 -m pytest code/tests/test_dta.py -q
  or: python3 code/tests/test_dta.py   (falls back to a tiny runner)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dta_engine  # noqa: E402
from dta_signals import (  # noqa: E402
    annualized_vol, drawdown_brake, sma, trend_gate, vol_brake_factor,
)

UNIVERSE = {
    "sleeves": {
        "risk": [
            {"ticker": "SPY", "target_weight": 0.16},
            {"ticker": "VEA", "target_weight": 0.16},
            {"ticker": "IEF", "target_weight": 0.16},
            {"ticker": "GLD", "target_weight": 0.16},
            {"ticker": "VNQ", "target_weight": 0.16},
        ],
        "harbor": {"tickers": ["SGOV", "BIL", "USFR", "TBLL", "GBIL"]},
    },
    "permanent_cushion_pct": 0.20,
    "max_single_holding_pct": 0.24,
}
CONFIG = {
    "trend_lookback_days": 200, "trend_gate_on_threshold": 1.02, "trend_gate_off_threshold": 0.98,
    "vol_brake_threshold": 0.18, "vol_brake_lookback_days": 20, "drawdown_brake_threshold": -0.15,
    "no_trade_drift_band": 0.20, "limit_buy_above_ask_pct": 0.003, "limit_sell_below_bid_pct": 0.003,
    "satellite_enabled": False,
}


def uptrend(n=260, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


def downtrend(n=260, start=200.0, step=0.5):
    return [start - step * i for i in range(n)]


def make_providers(closes_by_ticker):
    def bars(t):
        return closes_by_ticker.get(t, uptrend())
    def price(t, side, fallback):
        return float(fallback) if fallback else 0.0
    return bars, price


# ----- signal tests --------------------------------------------------------

def test_sma_and_vol():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2], 5) == 0.0
    assert annualized_vol([100, 100, 100, 100]) == 0.0


def test_trend_gate_on_off_hysteresis():
    up = uptrend()
    state, _ = trend_gate(up, None)
    assert state == "on", "clear uptrend must be on"
    down = downtrend()
    state, _ = trend_gate(down, None)
    assert state == "off", "clear downtrend must be off"
    # Inside the band -> holds prior. Build a series whose last close ~= SMA200.
    flat = [100.0] * 260
    state_prior_on, _ = trend_gate(flat, "on")
    state_prior_off, _ = trend_gate(flat, "off")
    assert state_prior_on == "on" and state_prior_off == "off", "band holds prior state"


def test_trend_gate_insufficient_bars_defaults_off():
    state, _ = trend_gate([100.0] * 50, None)
    assert state == "off"


def test_vol_brake_down_only():
    calm = [100 + 0.1 * i for i in range(30)]
    f, _ = vol_brake_factor(calm, threshold=0.18)
    assert f == 1.0, "calm market = no brake, never > 1"
    wild = [100 * (1.05 if i % 2 else 0.95) for i in range(30)]
    f, _ = vol_brake_factor(wild, threshold=0.18)
    assert 0 < f < 1.0, "wild market scales down"


def test_drawdown_brake():
    active, _ = drawdown_brake(-0.16, threshold=-0.15)
    assert active
    active, _ = drawdown_brake(-0.10, threshold=-0.15)
    assert not active


# ----- engine tests --------------------------------------------------------

def test_all_riskon_allocates_80pct_and_harbor_rest():
    closes = {t: uptrend() for t in ["SPY", "VEA", "IEF", "GLD", "VNQ"]}
    bars, price = make_providers(closes)
    plan = dta_engine.plan_trades(
        equity=100000, cash=100000, positions={}, drawdown_from_hwm=0.0,
        prior_gates={}, is_new_buy_month=True, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    assert all(plan["gates"][t] == "on" for t in ["SPY", "VEA", "IEF", "GLD", "VNQ"])
    buys = [t for t in plan["trades"] if t["side"] == "BUY"]
    tickers = {t["ticker"] for t in buys}
    # all 5 risk sleeves should be bought toward 16% each
    assert {"SPY", "VEA", "IEF", "GLD", "VNQ"}.issubset(tickers)
    # harbor target should be ~20% cushion
    assert plan["targets"].get("SGOV", 0) > 0
    assert plan["regime"] == "normal"


def test_riskoff_sleeve_is_exited_daily():
    # SPY in a downtrend -> gate off -> held SPY must be SOLD even on a non-rebalance day.
    closes = {"SPY": downtrend(), "VEA": uptrend(), "IEF": uptrend(), "GLD": uptrend(), "VNQ": uptrend()}
    bars, price = make_providers(closes)
    positions = {"SPY": {"qty": 30, "market_value": 16000, "price": 533.0}}
    plan = dta_engine.plan_trades(
        equity=100000, cash=0, positions=positions, drawdown_from_hwm=0.0,
        prior_gates={"SPY": "on"}, is_new_buy_month=False, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    sells = [t for t in plan["trades"] if t["side"] == "SELL"]
    assert any(t["ticker"] == "SPY" and t["exit_rule"] == "trend_break" for t in sells), \
        "a risk sleeve that lost its trend must be exited daily"


def test_drawdown_brake_rotates_everything_to_harbor():
    closes = {t: uptrend() for t in ["SPY", "VEA", "IEF", "GLD", "VNQ"]}
    bars, price = make_providers(closes)
    positions = {"SPY": {"qty": 30, "market_value": 16000, "price": 533.0}}
    plan = dta_engine.plan_trades(
        equity=100000, cash=0, positions=positions, drawdown_from_hwm=0.20,  # 20% underwater
        prior_gates={t: "on" for t in ["SPY", "VEA", "IEF", "GLD", "VNQ"]},
        is_new_buy_month=True, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    assert plan["regime"] == "defensive"
    # No risk buys under the brake; SPY should be sold.
    assert not any(t["side"] == "BUY" and t["ticker"] in ("SPY", "VEA", "IEF", "GLD", "VNQ") for t in plan["trades"])
    assert any(t["ticker"] == "SPY" and t["side"] == "SELL" and t["exit_rule"] == "drawdown_brake" for t in plan["trades"])


def test_legacy_holding_is_liquidated():
    # A non-universe stock (e.g. leftover AMD) must be sold to conform to the plan.
    closes = {t: uptrend() for t in ["SPY", "VEA", "IEF", "GLD", "VNQ", "AMD"]}
    bars, price = make_providers(closes)
    positions = {"AMD": {"qty": 18, "market_value": 8000, "price": 444.0}}
    plan = dta_engine.plan_trades(
        equity=100000, cash=0, positions=positions, drawdown_from_hwm=0.0,
        prior_gates={}, is_new_buy_month=False, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    assert any(t["ticker"] == "AMD" and t["side"] == "SELL" and t["exit_rule"] == "not_in_universe" for t in plan["trades"])


def test_sells_before_buys_and_position_cap():
    closes = {t: uptrend() for t in ["SPY", "VEA", "IEF", "GLD", "VNQ", "AMD"]}
    bars, price = make_providers(closes)
    positions = {"AMD": {"qty": 18, "market_value": 8000, "price": 444.0}}
    plan = dta_engine.plan_trades(
        equity=100000, cash=50000, positions=positions, drawdown_from_hwm=0.0,
        prior_gates={}, is_new_buy_month=True, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    sides = [t["side"] for t in plan["trades"]]
    # every SELL index < every BUY index
    last_sell = max((i for i, s in enumerate(sides) if s == "SELL"), default=-1)
    first_buy = min((i for i, s in enumerate(sides) if s == "BUY"), default=len(sides))
    assert last_sell < first_buy, "sells must be ordered before buys"
    # no single buy exceeds the 24% cap
    for t in plan["trades"]:
        if t["side"] == "BUY":
            assert t["qty"] * t["limit_price"] <= 100000 * 0.24 + 1


def test_buys_never_exceed_cash():
    closes = {t: uptrend() for t in ["SPY", "VEA", "IEF", "GLD", "VNQ"]}
    bars, price = make_providers(closes)
    plan = dta_engine.plan_trades(
        equity=100000, cash=5000, positions={}, drawdown_from_hwm=0.0,
        prior_gates={}, is_new_buy_month=True, universe=UNIVERSE, config=CONFIG,
        bars_provider=bars, price_provider=price,
    )
    spend = sum(t["qty"] * t["limit_price"] for t in plan["trades"] if t["side"] == "BUY")
    assert spend <= 5000 + 1, "buys must fit in settled cash"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} tests passed")
    raise SystemExit(0 if passed == len(fns) else 1)
