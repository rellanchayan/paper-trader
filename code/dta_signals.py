"""
dta_signals.py — the pure math behind the Diversified Trend Allocator (DTA).

Three layers, all computed from daily closing prices only (nothing else is in
the data feed). Every function here is pure: same inputs -> same output, no
network, no files. That is deliberate — it makes the strategy's brain fully
unit-testable without touching Alpaca.

1. Per-sleeve TREND GATE (SMA200 with a hysteresis band) — the one real signal.
2. Portfolio VOL BRAKE — scales risk down (never up) when markets get wild.
3. DRAWDOWN BRAKE — last-resort rotate-to-cash when the account is deep underwater.
"""

from __future__ import annotations

import math


def sma(closes: list[float], n: int) -> float:
    """Simple moving average of the last n closes. 0.0 if not enough data."""
    if len(closes) < n or n <= 0:
        return 0.0
    return sum(closes[-n:]) / n


def daily_returns(closes: list[float], n: int | None = None) -> list[float]:
    """Daily simple returns. If n given, use the last n returns."""
    if len(closes) < 2:
        return []
    series = closes if n is None else closes[-(n + 1):]
    return [series[i] / series[i - 1] - 1 for i in range(1, len(series)) if series[i - 1] > 0]


def annualized_vol(closes: list[float], n: int = 20) -> float:
    """Realized volatility of daily returns, annualized (0.20 == 20%/yr)."""
    rets = daily_returns(closes, n)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def trend_gate(
    closes: list[float],
    prior_state: str | None,
    *,
    lookback: int = 200,
    on_threshold: float = 1.02,
    off_threshold: float = 0.98,
) -> tuple[str, str]:
    """Per-sleeve trend gate with hysteresis.

    Returns (state, reason) where state is "on" or "off".
      - "on"  when close > SMA200 * on_threshold  (clearly in an uptrend)
      - "off" when close < SMA200 * off_threshold (clearly broken down)
      - inside the band: hold the prior state (this kills one-day whipsaw).

    Conservative default: if we have no prior state and we're inside the band,
    or we don't yet have `lookback` bars, we return "off" — we do not buy until
    a sleeve has clearly earned it.
    """
    if len(closes) < lookback:
        return "off", f"only {len(closes)} bars, need {lookback} for SMA{lookback} — default off"

    close = closes[-1]
    avg = sma(closes, lookback)
    hi = avg * on_threshold
    lo = avg * off_threshold

    if close > hi:
        return "on", f"close {close:.2f} > SMA{lookback} {avg:.2f} x {on_threshold} ({hi:.2f}) — uptrend"
    if close < lo:
        return "off", f"close {close:.2f} < SMA{lookback} {avg:.2f} x {off_threshold} ({lo:.2f}) — downtrend"

    prior = prior_state if prior_state in ("on", "off") else "off"
    return prior, f"close {close:.2f} in hysteresis band [{lo:.2f}, {hi:.2f}] — holding prior state '{prior}'"


def vol_brake_factor(
    spy_closes: list[float],
    *,
    threshold: float = 0.18,
    lookback: int = 20,
) -> tuple[float, str]:
    """Down-only volatility targeting.

    Returns (factor in (0, 1], reason). factor == 1.0 means normal sizing;
    factor < 1.0 scales every risk sleeve down so realized portfolio vol tracks
    the threshold. It NEVER returns > 1.0 (we never lever up).
    """
    vol = annualized_vol(spy_closes, lookback)
    if vol <= threshold or vol <= 0:
        return 1.0, f"market vol {vol:.0%} <= {threshold:.0%} target — full risk sizing"
    factor = threshold / vol
    return factor, f"market vol {vol:.0%} > {threshold:.0%} target — scaling risk sleeves to {factor:.0%}"


def drawdown_brake(
    drawdown_from_hwm: float,
    *,
    threshold: float = -0.15,
) -> tuple[bool, str]:
    """Last-resort de-risk. `drawdown_from_hwm` is a NEGATIVE-or-zero fraction
    (e.g. -0.12 means 12% below the high-water mark; the alpaca client reports
    it as a positive magnitude, so callers pass -abs(value)).

    Returns (active, reason). When active, the engine rotates ALL risk sleeves
    to the T-bill harbor. This is NOT the .HALT_TRADING kill switch — selling and
    rotating MUST keep working here; that's the whole point.
    """
    if drawdown_from_hwm <= threshold:
        return True, f"account {drawdown_from_hwm:.0%} below high-water mark <= {threshold:.0%} brake — rotate to T-bills"
    return False, f"account {drawdown_from_hwm:.0%} from high-water mark > {threshold:.0%} brake — normal operation"
