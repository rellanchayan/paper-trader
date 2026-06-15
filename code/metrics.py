"""
metrics.py — honest, risk-adjusted scorekeeping for the strategy.

Judging an automated strategy on "did it beat SPY this month" gets it abandoned
at the worst time. So this reports the metrics that actually matter: Sharpe
ratio, MAR (return per unit of drawdown), max drawdown, realized vol, and
LIMIT-order fill rate — each next to SPY for context.

Usage:
    python3 code/metrics.py          # plain-English report
    python3 code/metrics.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "state" / "portfolio_history.jsonl"
COMPLETED_DIR = ROOT / "state" / "completed_trades"

RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.045"))


def load_equity_series() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    by_day: dict[str, float] = {}
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            d = json.loads(line)
            by_day[d["timestamp_utc"][:10]] = float(d["equity"])
        except Exception:
            continue
    return [{"date": k, "equity": by_day[k]} for k in sorted(by_day)]


def daily_returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]


def sharpe(values: list[float], rf: float = RISK_FREE_RATE) -> float | None:
    rets = daily_returns(values)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    daily_rf = (1 + rf) ** (1 / 252) - 1
    return (mean - daily_rf) / sd * math.sqrt(252)


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    hwm, mdd = values[0], 0.0
    for v in values:
        hwm = max(hwm, v)
        if hwm > 0:
            mdd = min(mdd, v / hwm - 1)
    return mdd


def annualized_vol(values: list[float]) -> float:
    rets = daily_returns(values)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def cagr(values: list[float], n_days: int) -> float | None:
    if len(values) < 2 or values[0] <= 0 or n_days <= 0:
        return None
    years = len(values) / 252
    if years <= 0:
        return None
    return (values[-1] / values[0]) ** (1 / years) - 1


def fill_rate() -> tuple[float | None, dict]:
    details = {"final_orders": 0, "filled": 0, "partial": 0, "unfilled": 0, "pending": 0}
    if not COMPLETED_DIR.exists():
        return None, details
    for path in COMPLETED_DIR.glob("*.json"):
        try:
            t = json.loads(path.read_text())
        except Exception:
            continue
        status = str(t.get("status", "")).lower()
        if status in ("submitted", "accepted", "pending_new", "new"):
            details["pending"] += 1
            continue
        details["final_orders"] += 1
        fq = float(t.get("filled_qty") or 0)
        q = float(t.get("qty") or 0)
        if fq <= 0:
            details["unfilled"] += 1
        elif q and fq >= q:
            details["filled"] += 1
        else:
            details["partial"] += 1
    final = details["final_orders"]
    rate = (details["filled"] / final) if final else None
    return rate, details


def spy_series_for(dates: list[str]) -> list[float] | None:
    """Fetch SPY closes aligned to our history window (for a fair comparison)."""
    if len(dates) < 2:
        return None
    span = 30
    try:
        from datetime import date
        span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 15
    except Exception:
        pass
    try:
        out = subprocess.run(
            [sys.executable, "code/alpaca_client.py", "--bars", "SPY", "--days", str(max(span, 20))],
            cwd=ROOT, text=True, capture_output=True,
        )
        if out.returncode != 0:
            return None
        bars = json.loads(out.stdout).get("bars", [])
    except Exception:
        return None
    by_day = {b["date"]: float(b["c"]) for b in bars}
    aligned = []
    last = None
    for d in dates:
        for bd in sorted(by_day):
            if bd <= d:
                last = by_day[bd]
        if last is not None:
            aligned.append(last)
    return aligned if len(aligned) >= 2 else None


def build_report() -> dict:
    series = load_equity_series()
    if len(series) < 2:
        return {"error": "Not enough history yet — run the bot for at least two days."}
    values = [s["equity"] for s in series]
    dates = [s["date"] for s in series]

    rate, fill_details = fill_rate()
    spy = spy_series_for(dates)

    report = {
        "as_of": dates[-1],
        "started": dates[0],
        "days_of_history": len(series),
        "equity": round(values[-1], 2),
        "total_return": round(values[-1] / values[0] - 1, 4),
        "sharpe": round(sharpe(values), 2) if sharpe(values) is not None else None,
        "max_drawdown": round(max_drawdown(values), 4),
        "annualized_vol": round(annualized_vol(values), 4),
        "fill_rate": round(rate, 3) if rate is not None else None,
        "fill_details": fill_details,
    }
    cg = cagr(values, len(values))
    report["mar"] = round(cg / abs(max_drawdown(values)), 2) if cg is not None and max_drawdown(values) < 0 else None
    if spy:
        report["spy_total_return"] = round(spy[-1] / spy[0] - 1, 4)
        report["spy_sharpe"] = round(sharpe(spy), 2) if sharpe(spy) is not None else None
        report["spy_max_drawdown"] = round(max_drawdown(spy), 4)
        report["spy_annualized_vol"] = round(annualized_vol(spy), 4)
    return report


def print_plain(r: dict) -> None:
    if "error" in r:
        print(r["error"])
        return
    print(f"RISK-ADJUSTED REPORT — as of {r['as_of']} (started {r['started']}, {r['days_of_history']} days)")
    print(f"  Account value:       ${r['equity']:,.2f}   (total return {r['total_return']:+.2%})")
    print(f"  Sharpe ratio:        {r['sharpe']}" + (f"   vs SPY {r['spy_sharpe']}" if r.get('spy_sharpe') is not None else ""))
    print(f"  Max drawdown:        {r['max_drawdown']:.2%}" + (f"   vs SPY {r['spy_max_drawdown']:.2%}" if r.get('spy_max_drawdown') is not None else ""))
    print(f"  Annualized vol:      {r['annualized_vol']:.2%}" + (f"   vs SPY {r['spy_annualized_vol']:.2%}" if r.get('spy_annualized_vol') is not None else ""))
    print(f"  MAR (return/drawdn): {r['mar']}")
    if r["fill_rate"] is not None:
        fd = r["fill_details"]
        print(f"  LIMIT fill rate:     {r['fill_rate']:.0%}  ({fd['filled']} filled / {fd['final_orders']} final, {fd['pending']} pending)")
    else:
        print("  LIMIT fill rate:     no completed orders yet")
    print("  Reminder: judge this on Sharpe / drawdown vs SPY, not on raw monthly return.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_plain(report)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
