"""
dta_engine.py — the DTA planner. Pure logic: given the portfolio, the config,
and two injected data accessors (bars + price), it returns a concrete trade
plan. No network, no files here — so it is fully unit-testable.

The plan respects every hard rule:
- Long-only LIMIT+DAY orders, whole shares.
- Sells are emitted BEFORE buys (de-risking is never blocked by a pending buy).
- Buys are sized against SETTLED cash only, so the constitution cash check never
  rejects them (sale proceeds redeploy on the next run, once settled).
- The T-bill harbor is allocated waterfall-style across several ultra-short ETFs
  so no single holding ever needs to exceed the 25% position cap.

Cadence:
- DAILY (every run): protective only — exit any risk sleeve whose trend gate is
  off, exit holdings not in the plan, rotate to T-bills under the drawdown brake,
  and park settled cash in the harbor.
- MONTHLY (first run of a new month): full rebalance — buy under-weight risk
  sleeves whose gate is on, trim over-weight ones, refresh the satellite.
"""

from __future__ import annotations

import math

from dta_signals import (
    annualized_vol,
    daily_returns,
    drawdown_brake,
    sma,
    trend_gate,
    vol_brake_factor,
)


def _round2(x: float) -> float:
    return round(x, 2)


def _buy_limit(price: float, cfg: dict) -> float:
    return _round2(price * (1 + cfg.get("limit_buy_above_ask_pct", 0.003)))


def _sell_limit(price: float, cfg: dict) -> float:
    return _round2(price * (1 - cfg.get("limit_sell_below_bid_pct", 0.003)))


def plan_satellite(
    *,
    budget: float,
    spy_ret20: float,
    cfg: dict,
    sectors: dict,
    held: dict,
    bars_provider,
) -> tuple[dict, list[dict]]:
    """Optional momentum satellite. Returns (targets$, diagnostics).

    Only called when satellite_enabled is true AND the market regime is risk-on.
    Hard-capped: <= satellite_max_book_pct of equity (via `budget`), per-name and
    per-sector limits, top-N by risk-adjusted momentum.
    """
    diags: list[dict] = []
    if budget <= 0:
        return {}, diags
    min_out = cfg.get("satellite_min_outperformance", 0.02)
    max_names = int(cfg.get("satellite_max_names", 4))
    per_name_cap = cfg.get("satellite_max_per_name_pct", 0.08)
    per_sector = int(cfg.get("satellite_max_per_sector", 2))
    equity = budget / max(cfg.get("satellite_max_book_pct", 0.20), 1e-9)

    candidates = []
    for ticker in sorted(sectors):
        if sectors.get(ticker) == "index":
            continue
        closes = bars_provider(ticker)
        if len(closes) < 210:
            continue
        close = closes[-1]
        s50, s200 = sma(closes, 50), sma(closes, 200)
        if not (close > s50 > s200):
            continue
        ret20 = close / closes[-21] - 1
        if ret20 - spy_ret20 < min_out:
            continue
        ret63 = close / closes[-64] - 1
        ret126 = close / closes[-127] - 1
        vol = annualized_vol(closes, 60)
        score = (0.2 * ret20 + 0.5 * ret63 + 0.3 * ret126) / max(vol, 0.10)
        candidates.append((score, ticker, close))

    candidates.sort(reverse=True)
    targets: dict[str, float] = {}
    sector_count: dict[str, int] = {}
    spent = 0.0
    for score, ticker, close in candidates:
        if len(targets) >= max_names or spent >= budget:
            break
        sec = sectors.get(ticker, f"unknown:{ticker}")
        if sector_count.get(sec, 0) >= per_sector:
            diags.append({"ticker": ticker, "skip": f"sector cap ({sec})"})
            continue
        alloc = min(equity * per_name_cap, budget - spent)
        if alloc < close:  # can't afford even one share
            continue
        targets[ticker] = alloc
        spent += alloc
        sector_count[sec] = sector_count.get(sec, 0) + 1
        diags.append({"ticker": ticker, "satellite_target": _round2(alloc), "score": round(score, 3)})
    return targets, diags


def plan_trades(
    *,
    equity: float,
    cash: float,
    positions: dict,
    drawdown_from_hwm: float,
    prior_gates: dict,
    is_new_buy_month: bool,
    universe: dict,
    config: dict,
    bars_provider,
    price_provider,
    sectors: dict | None = None,
) -> dict:
    """Compute the full DTA trade plan. See module docstring.

    positions: {ticker: {"qty": float, "market_value": float, "price": float}}
    bars_provider(ticker) -> list[float] closes (newest last)
    price_provider(ticker, side, fallback) -> float (ask for BUY, bid for SELL)
    Returns a dict with trades, gates (to persist), targets, regime, diagnostics.
    """
    sectors = sectors or {}
    cfg = config
    risk = [s["ticker"] for s in universe["sleeves"]["risk"]]
    weights = {s["ticker"]: s["target_weight"] for s in universe["sleeves"]["risk"]}
    harbor_list = universe["sleeves"]["harbor"]["tickers"]
    cushion = universe.get("permanent_cushion_pct", 0.20)
    pos_cap = universe.get("max_single_holding_pct", 0.24)
    drift = cfg.get("no_trade_drift_band", 0.20)

    lookback = int(cfg.get("trend_lookback_days", 200))
    on_t = cfg.get("trend_gate_on_threshold", 1.02)
    off_t = cfg.get("trend_gate_off_threshold", 0.98)

    diagnostics: list[dict] = []

    # --- 1. Per-sleeve trend gates -----------------------------------------
    gates: dict[str, str] = {}
    gate_reasons: dict[str, str] = {}
    for t in risk:
        state, reason = trend_gate(
            bars_provider(t), prior_gates.get(t),
            lookback=lookback, on_threshold=on_t, off_threshold=off_t,
        )
        gates[t] = state
        gate_reasons[t] = reason

    # --- 2. Regime brakes ---------------------------------------------------
    spy_closes = bars_provider("SPY")
    brake_factor, brake_reason = vol_brake_factor(
        spy_closes, threshold=cfg.get("vol_brake_threshold", 0.18),
        lookback=int(cfg.get("vol_brake_lookback_days", 20)),
    )
    dd_active, dd_reason = drawdown_brake(
        -abs(drawdown_from_hwm), threshold=cfg.get("drawdown_brake_threshold", -0.15),
    )
    spy_ret20 = (spy_closes[-1] / spy_closes[-21] - 1) if len(spy_closes) >= 21 else 0.0
    spy_risk_on = gates.get("SPY") == "on"

    # --- 3. Target dollar allocations --------------------------------------
    targets: dict[str, float] = {}
    risk_alloc = 0.0
    for t in risk:
        if dd_active or gates[t] == "off":
            targets[t] = 0.0
        else:
            targets[t] = equity * weights[t] * brake_factor
        risk_alloc += targets[t]

    # Satellite (optional, off by default; only when risk-on and not braked).
    sat_targets: dict[str, float] = {}
    if cfg.get("satellite_enabled") and not dd_active and spy_risk_on:
        sat_budget = equity * cfg.get("satellite_max_book_pct", 0.20)
        sat_targets, sat_diags = plan_satellite(
            budget=sat_budget, spy_ret20=spy_ret20, cfg=cfg, sectors=sectors,
            held=positions, bars_provider=bars_provider,
        )
        diagnostics.extend(sat_diags)
    sat_alloc = sum(sat_targets.values())
    for t, v in sat_targets.items():
        targets[t] = v

    # Harbor absorbs everything not in risk/satellite, spread waterfall so no
    # single T-bill ETF needs to exceed the position cap.
    harbor_total = max(equity - risk_alloc - sat_alloc, 0.0)
    remaining = harbor_total
    cap_dollars = equity * pos_cap
    for ht in harbor_list:
        if remaining <= 0:
            targets[ht] = 0.0
            continue
        alloc = min(remaining, cap_dollars)
        targets[ht] = alloc
        remaining -= alloc
    if remaining > 1.0:
        diagnostics.append({"warning": f"harbor capacity short by ${remaining:,.0f}; add T-bill ETFs to universe"})

    full_rebalance = is_new_buy_month and not dd_active
    harbor_set = set(harbor_list)

    # --- 4. SELLS first -----------------------------------------------------
    trades: list[dict] = []

    def sell(ticker, qty, ref_price, reason, exit_rule):
        price = price_provider(ticker, "SELL", ref_price)
        trades.append({
            "ticker": ticker, "side": "SELL", "qty": int(qty),
            "limit_price": _sell_limit(price, cfg), "order_type": "LIMIT",
            "time_in_force": "DAY", "reason": reason, "exit_rule": exit_rule,
        })

    for ticker, pos in positions.items():
        qty_held = math.floor(float(pos.get("qty", 0)))
        held_val = float(pos.get("market_value", 0))
        ref = float(pos.get("price") or (held_val / qty_held if qty_held else 0))
        if qty_held < 1 or held_val <= 0:
            continue
        tgt = targets.get(ticker, 0.0)

        # Never churn the harbor on a non-rebalance day.
        if ticker in harbor_set and not full_rebalance:
            continue

        if tgt <= 0:
            if ticker in risk and dd_active:
                why, rule = f"{ticker}: drawdown brake — {dd_reason}", "drawdown_brake"
            elif ticker in risk:
                why, rule = f"{ticker}: trend gate off — {gate_reasons.get(ticker, '')}", "trend_break"
            else:
                why, rule = f"{ticker}: not in the DTA plan — liquidating to conform to target allocation.", "not_in_universe"
            sell(ticker, qty_held, ref, why, rule)
        elif full_rebalance and held_val > tgt * (1 + drift):
            excess = held_val - tgt
            qty = min(math.floor(excess / ref) if ref > 0 else 0, qty_held)
            if qty >= 1:
                sell(ticker, qty, ref,
                     f"{ticker}: {held_val / equity:.0%} of book, above its {tgt / equity:.0%} target — trimming (rebalance).",
                     "rebalance_trim")

    # --- 5. BUYS (sized against settled cash only) -------------------------
    available = cash

    def buy(ticker, deficit, ref_price, reason):
        nonlocal available
        if available <= 0 or deficit <= 0:
            return
        price = price_provider(ticker, "BUY", ref_price)
        limit = _buy_limit(price, cfg)
        if limit <= 0:
            return
        qty = math.floor(min(deficit, available) / limit)
        if qty < 1:
            return
        trades.append({
            "ticker": ticker, "side": "BUY", "qty": int(qty),
            "limit_price": limit, "order_type": "LIMIT", "time_in_force": "DAY",
            "reason": reason,
        })
        available -= qty * limit

    if not dd_active and full_rebalance:
        # Risk sleeves up to target (only those in an uptrend).
        for t in risk:
            if gates[t] != "on":
                continue
            cur = float(positions.get(t, {}).get("market_value", 0))
            tgt = targets[t]
            if cur < tgt * (1 - drift):
                ref = bars_provider(t)[-1] if bars_provider(t) else 0
                buy(t, tgt - cur, ref,
                    f"{t}: risk-on ({gate_reasons[t]}); building toward {tgt / equity:.0%} target. {brake_reason}.")
        # Satellite names.
        for t, tgt in sat_targets.items():
            cur = float(positions.get(t, {}).get("market_value", 0))
            if cur < tgt * (1 - drift):
                ref = bars_provider(t)[-1] if bars_provider(t) else 0
                buy(t, tgt - cur, ref, f"{t}: momentum satellite (capped) toward {tgt / equity:.0%} of book.")

    # Park remaining settled cash in the harbor (waterfall by target), any day.
    for ht in harbor_list:
        cur = float(positions.get(ht, {}).get("market_value", 0))
        tgt = targets.get(ht, 0.0)
        if tgt - cur > equity * 0.005:  # only if meaningfully under (>0.5% of book)
            ref = (bars_provider(ht)[-1] if bars_provider(ht) else 0) or 1.0
            buy(ht, tgt - cur, ref, f"{ht}: parking cash in the T-bill harbor (earns the bill rate while risk-off).")

    regime = "defensive" if dd_active else ("cautious" if brake_factor < 1.0 else "normal")
    return {
        "trades": trades,
        "gates": gates,
        "gate_reasons": gate_reasons,
        "targets": {k: _round2(v) for k, v in targets.items() if v > 0},
        "regime": regime,
        "regime_detail": {
            "drawdown_brake": dd_reason, "vol_brake": brake_reason,
            "spy_risk_on": spy_risk_on, "full_rebalance": full_rebalance,
        },
        "diagnostics": diagnostics,
    }
