"""
blend_engine.py — the BLEND planner: 75% hand-picked stocks + 25% ETF ballast.

Built to fit a Robinhood-style agentic INVESTMENT account:
- Equities only (stocks + ETFs), long-only, cash account (no margin/options).
- No day trading: never buys and sells the same ticker on the same day.
- Buys use settled cash only and are capped at a daily dollar limit ($5,000).
- Safety kept from the DTA: per-sleeve trend gates, a market regime gate (no
  stock-picking while the market is in a downtrend), a volatility brake, and a
  drawdown brake that rotates to T-bills.

Pure logic: data is injected, so it is fully unit-testable with no network.
The stock sleeve is diversified on purpose (max 2 per sector, <=10% per name,
correlation gate) so "75% hand-picked" can never collapse into one sector.
"""

from __future__ import annotations

import math

from dta_signals import (
    annualized_vol, correlation, daily_returns, drawdown_brake, sma,
    trend_gate, vol_brake_factor,
)


def _round2(x: float) -> float:
    return round(x, 2)


def select_stocks(*, budget, equity, spy_ret20, cfg, sectors, watchlist, held, exclude, bars_provider):
    """Pick the hand-picked stock sleeve. Returns (targets$, diagnostics).

    Keeps names you already own that are still in an uptrend (low churn), then
    fills remaining slots with the top-ranked new candidates — all subject to
    the per-sector cap and a pairwise correlation gate. Weights inverse-vol
    (calmer names get a bit more), capped per name. Leftover budget (from caps)
    is left for the harbor by the caller.
    """
    diags = []
    if budget <= 0:
        return {}, diags
    min_out = cfg.get("min_outperformance", 0.0)
    max_names = int(cfg.get("max_stock_names", 12))
    per_sector = int(cfg.get("max_per_sector", 2))
    max_corr = cfg.get("max_corr", 0.85)
    per_name_cap = cfg.get("max_per_name_pct", 0.10)

    qualifying = {}  # ticker -> (score, vol)
    for t in watchlist:
        if t in exclude or sectors.get(t) == "index":
            continue
        closes = bars_provider(t)
        if len(closes) < 210:
            continue
        close = closes[-1]
        s50, s200 = sma(closes, 50), sma(closes, 200)
        if not (close > s50 > s200):
            continue
        ret20 = close / closes[-21] - 1
        if (ret20 - spy_ret20) < min_out:
            continue
        ret63 = close / closes[-64] - 1
        ret126 = close / closes[-127] - 1
        vol = annualized_vol(closes, 60)
        score = (0.2 * ret20 + 0.5 * ret63 + 0.3 * ret126) / max(vol, 0.10)
        qualifying[t] = (score, vol)

    # Order: currently-held qualifiers first (stability), then new by score.
    held_q = sorted((t for t in qualifying if t in held), key=lambda t: qualifying[t][0], reverse=True)
    new_q = sorted((t for t in qualifying if t not in held), key=lambda t: qualifying[t][0], reverse=True)

    selected = []
    sector_count = {}
    for t in held_q + new_q:
        if len(selected) >= max_names:
            break
        sec = sectors.get(t, f"unknown:{t}")
        if sector_count.get(sec, 0) >= per_sector:
            diags.append({"ticker": t, "skip": f"sector cap ({sec})"})
            continue
        cand_rets = daily_returns(bars_provider(t), 60)
        too_similar = next(
            (o for o in selected if correlation(cand_rets, daily_returns(bars_provider(o), 60)) > max_corr),
            None,
        )
        if too_similar:
            diags.append({"ticker": t, "skip": f"correlated > {max_corr} with {too_similar}"})
            continue
        selected.append(t)
        sector_count[sec] = sector_count.get(sec, 0) + 1

    if not selected:
        return {}, diags
    inv = {t: 1.0 / max(qualifying[t][1], 0.05) for t in selected}
    s = sum(inv.values())
    cap = per_name_cap * equity
    targets = {}
    for t in selected:
        targets[t] = min(budget * inv[t] / s, cap)
        diags.append({"ticker": t, "stock_target": _round2(targets[t]), "score": round(qualifying[t][0], 3)})
    return targets, diags


def plan_trades(*, equity, cash, positions, drawdown_from_hwm, prior_gates,
                invested_today, sold_today, bought_today,
                config, sectors, watchlist, bars_provider, price_provider):
    """Compute the BLEND trade plan. See module docstring.

    positions: {ticker: {"qty","market_value","price"}}
    invested_today: $ of BUY orders already submitted today (for the daily cap)
    sold_today / bought_today: sets of tickers already traded today (no day trades)
    Returns dict: trades, gates, targets, regime, diagnostics, compliance.
    """
    cfg = config
    sectors = {k: v for k, v in (sectors or {}).items() if not k.startswith("_")}
    etf_ballast = cfg["etf_ballast"]
    harbor_list = cfg["harbor_tickers"]
    stock_pct = cfg.get("stock_sleeve_pct", 0.75)
    pos_cap = cfg.get("max_single_holding_pct", 0.24)
    drift = cfg.get("no_trade_drift_band", 0.20)
    daily_cap = cfg.get("daily_invest_cap_usd", 5000)
    lookback = int(cfg.get("trend_lookback_days", 200))
    on_t = cfg.get("trend_gate_on_threshold", 1.02)
    off_t = cfg.get("trend_gate_off_threshold", 0.98)
    stock_break_sma = int(cfg.get("stock_trend_break_sma", 50))
    diagnostics = []

    # --- regime + brakes ---
    spy_closes = bars_provider("SPY")
    spy_gate, spy_reason = trend_gate(spy_closes, prior_gates.get("SPY"),
                                      lookback=lookback, on_threshold=on_t, off_threshold=off_t)
    spy_risk_on = spy_gate == "on"
    spy_ret20 = (spy_closes[-1] / spy_closes[-21] - 1) if len(spy_closes) >= 21 else 0.0
    brake_factor, brake_reason = vol_brake_factor(
        spy_closes, threshold=cfg.get("vol_brake_threshold", 0.18),
        lookback=int(cfg.get("vol_brake_lookback_days", 20)))
    dd_active, dd_reason = drawdown_brake(
        -abs(drawdown_from_hwm), threshold=cfg.get("drawdown_brake_threshold", -0.15))

    gates = {"SPY": spy_gate}

    # --- ETF ballast targets (each trend-gated on its own) ---
    targets = {}
    for t, w in etf_ballast.items():
        state, reason = trend_gate(bars_provider(t), prior_gates.get(t),
                                   lookback=lookback, on_threshold=on_t, off_threshold=off_t)
        gates[t] = state
        targets[t] = 0.0 if (dd_active or state == "off") else equity * w * brake_factor

    # --- Stock sleeve (only when the market is risk-on and not drawdown-braked) ---
    stock_targets = {}
    if spy_risk_on and not dd_active:
        budget = equity * stock_pct * brake_factor
        exclude = set(sold_today) | set(bought_today)  # never re-enter a name traded today
        stock_targets, sdiags = select_stocks(
            budget=budget, equity=equity, spy_ret20=spy_ret20, cfg=cfg, sectors=sectors,
            watchlist=watchlist, held=positions, exclude=exclude, bars_provider=bars_provider)
        diagnostics.extend(sdiags)
    else:
        diagnostics.append({"stock_sleeve": "skipped — " + ("drawdown brake" if dd_active else "market risk-off (SPY below 200d)")})
    for t, v in stock_targets.items():
        targets[t] = v

    risk_alloc = sum(targets.values())

    # --- Harbor (waterfall, under the 25% single-name cap) ---
    harbor_total = max(equity - risk_alloc, 0.0)
    remaining, cap_dollars = harbor_total, equity * pos_cap
    for ht in harbor_list:
        alloc = min(remaining, cap_dollars) if remaining > 0 else 0.0
        targets[ht] = alloc
        remaining -= alloc

    risk_tickers = set(etf_ballast) | set(stock_targets)
    harbor_set = set(harbor_list)

    # --- SELLS first (uncapped — getting OUT of risk is never throttled) ---
    trades = []
    selling = set()

    def add_sell(ticker, qty, ref, reason, rule):
        price = price_provider(ticker, "SELL", ref)
        trades.append({"ticker": ticker, "side": "SELL", "qty": int(qty),
                       "limit_price": _round2(price * (1 - cfg.get("limit_sell_below_bid_pct", 0.003))),
                       "order_type": "LIMIT", "time_in_force": "DAY", "reason": reason, "exit_rule": rule})
        selling.add(ticker)

    for ticker, pos in positions.items():
        if ticker in bought_today:        # don't close a position opened today (= day trade)
            continue
        qty_held = math.floor(float(pos.get("qty", 0)))
        held_val = float(pos.get("market_value", 0))
        ref = float(pos.get("price") or (held_val / qty_held if qty_held else 0))
        if qty_held < 1 or held_val <= 0:
            continue
        if ticker in harbor_set:          # harbor only shrinks via redeploy, not daily churn
            continue
        tgt = targets.get(ticker, 0.0)
        in_ballast = ticker in etf_ballast
        in_watch = ticker in watchlist
        closes = bars_provider(ticker)
        trend_broken = in_watch and len(closes) >= stock_break_sma and closes[-1] < sma(closes, stock_break_sma)

        if dd_active and ticker in risk_tickers:
            add_sell(ticker, qty_held, ref, f"{ticker}: drawdown brake — {dd_reason}", "drawdown_brake")
        elif tgt <= 0:
            if in_ballast:
                why, rule = f"{ticker}: ETF ballast below its 200d trend — rotating to T-bills.", "etf_trend_off"
            elif in_watch:
                why = (f"{ticker}: market risk-off (SPY below 200d) — exiting stock to safety." if not spy_risk_on
                       else f"{ticker}: no longer a selected pick — exiting.")
                rule = "trend_break"
            else:
                why, rule = f"{ticker}: not in the blend plan — liquidating to conform to target allocation.", "not_in_universe"
            add_sell(ticker, qty_held, ref, why, rule)
        elif trend_broken:
            add_sell(ticker, qty_held, ref, f"{ticker}: closed below its {stock_break_sma}d average — trend break exit.", "trend_break")
        elif held_val > tgt * (1 + drift):
            excess = held_val - tgt
            qty = min(math.floor(excess / ref) if ref > 0 else 0, qty_held)
            if qty >= 1:
                add_sell(ticker, qty, ref, f"{ticker}: above target weight — trimming (rebalance).", "rebalance_trim")

    # --- BUYS (capped at the daily dollar limit AND settled cash) ---
    remaining_budget = max(min(daily_cap - invested_today, cash), 0.0)
    cap_hit = False

    def add_buy(ticker, deficit, ref, reason):
        nonlocal remaining_budget, cap_hit
        if remaining_budget <= 0:
            cap_hit = True
            return
        if deficit <= 0 or ticker in selling or ticker in sold_today or ticker in bought_today:
            return
        price = price_provider(ticker, "BUY", ref)
        limit = _round2(price * (1 + cfg.get("limit_buy_above_ask_pct", 0.003)))
        if limit <= 0:
            return
        qty = math.floor(min(deficit, remaining_budget) / limit)
        if qty < 1:
            return
        trades.append({"ticker": ticker, "side": "BUY", "qty": int(qty), "limit_price": limit,
                       "order_type": "LIMIT", "time_in_force": "DAY", "reason": reason})
        remaining_budget -= qty * limit

    # Order of deployment: stocks (75%) -> ETF ballast (25%) -> harbor.
    for t in sorted(stock_targets, key=lambda x: stock_targets[x], reverse=True):
        cur = float(positions.get(t, {}).get("market_value", 0))
        if cur < stock_targets[t] * (1 - drift):
            add_buy(t, stock_targets[t] - cur, (bars_provider(t)[-1] if bars_provider(t) else 0),
                    f"{t}: hand-picked stock (momentum/trend), building toward {stock_targets[t] / equity:.0%} of book.")
    for t in etf_ballast:
        cur = float(positions.get(t, {}).get("market_value", 0))
        if gates.get(t) == "on" and not dd_active and cur < targets[t] * (1 - drift):
            add_buy(t, targets[t] - cur, (bars_provider(t)[-1] if bars_provider(t) else 0),
                    f"{t}: ETF ballast toward {targets[t] / equity:.0%} of book.")
    for ht in harbor_list:
        cur = float(positions.get(ht, {}).get("market_value", 0))
        if targets.get(ht, 0) - cur > equity * 0.005:
            add_buy(ht, targets[ht] - cur, (bars_provider(ht)[-1] if bars_provider(ht) else 0) or 1.0,
                    f"{ht}: parking cash in the T-bill harbor.")

    if cap_hit or remaining_budget < min(daily_cap - invested_today, cash):
        diagnostics.append({"daily_cap": f"${invested_today:,.0f} invested earlier today; this run capped buys at ${daily_cap - invested_today:,.0f} (limit ${daily_cap:,.0f}/day)"})

    regime = "defensive" if dd_active else ("risk_off" if not spy_risk_on else ("cautious" if brake_factor < 1 else "normal"))
    return {
        "trades": trades, "gates": gates,
        "targets": {k: _round2(v) for k, v in targets.items() if v > 0},
        "regime": regime,
        "regime_detail": {"spy_gate": spy_reason, "vol_brake": brake_reason,
                          "drawdown_brake": dd_reason, "spy_risk_on": spy_risk_on},
        "compliance": {"equities_only": True, "cash_settled_only": True, "no_options_margin": True,
                       "no_day_trading": True, "daily_invest_cap_usd": daily_cap,
                       "invested_today_before_run": round(invested_today, 2)},
        "diagnostics": diagnostics,
    }
