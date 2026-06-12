"""
performance.py — answers one question in plain English: "Am I beating SPY?"

Reads the account history this repo already records (state/portfolio_history.jsonl)
and compares your return to SPY over the same period.

Usage:
    python3 code/performance.py          # plain-English report
    python3 code/performance.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "state" / "portfolio_history.jsonl"
COMPLETED_DIR = ROOT / "state" / "completed_trades"


def load_history() -> list[dict]:
    """One equity point per calendar day (the last snapshot of each day)."""
    if not HISTORY_FILE.exists():
        return []
    by_day: dict[str, dict] = {}
    for line in HISTORY_FILE.read_text().splitlines():
        try:
            d = json.loads(line)
            day = d["timestamp_utc"][:10]
            by_day[day] = {"date": day, "equity": float(d["equity"])}
        except Exception:
            continue
    return [by_day[k] for k in sorted(by_day)]


def spy_return(start_day: str, end_day: str) -> float | None:
    """SPY close-to-close return between the two dates (nearest trading days)."""
    span_days = (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days + 10
    try:
        proc = subprocess.run(
            [sys.executable, "code/alpaca_client.py", "--bars", "SPY", "--days", str(max(span_days, 15))],
            cwd=ROOT, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            return None
        bars = json.loads(proc.stdout).get("bars", [])
    except Exception:
        return None
    if not bars:
        return None
    start_close = None
    end_close = None
    for b in bars:
        if b["date"] <= start_day:
            start_close = float(b["c"])
        if b["date"] <= end_day:
            end_close = float(b["c"])
    if start_close is None:
        start_close = float(bars[0]["c"])  # history starts before our data; use earliest bar
    if not (start_close and end_close):
        return None
    return end_close / start_close - 1


def fill_stats() -> dict:
    """Count what actually happened to submitted orders (needs --reconcile to have run)."""
    stats = {"filled": 0, "expired_or_canceled": 0, "awaiting_check": 0}
    if not COMPLETED_DIR.exists():
        return stats
    for path in COMPLETED_DIR.glob("*.json"):
        try:
            status = json.loads(path.read_text()).get("status", "")
        except Exception:
            continue
        if status == "filled":
            stats["filled"] += 1
        elif status in {"expired", "canceled", "rejected", "done_for_day"}:
            stats["expired_or_canceled"] += 1
        elif status == "submitted":
            stats["awaiting_check"] += 1
    return stats


def build_report() -> dict:
    history = load_history()
    if len(history) < 2:
        return {"error": "Not enough history yet. Run the bot for at least two days first."}

    first, last = history[0], history[-1]
    my_return = last["equity"] / first["equity"] - 1
    spy_ret = spy_return(first["date"], last["date"])

    peak = max(h["equity"] for h in history)
    drawdown = (peak - last["equity"]) / peak if peak > 0 else 0.0

    report = {
        "as_of": last["date"],
        "started": first["date"],
        "starting_equity": round(first["equity"], 2),
        "current_equity": round(last["equity"], 2),
        "my_return": round(my_return, 4),
        "spy_return": round(spy_ret, 4) if spy_ret is not None else None,
        "vs_spy": round(my_return - spy_ret, 4) if spy_ret is not None else None,
        "max_drawdown": round(drawdown, 4),
        "orders": fill_stats(),
    }
    return report


def print_plain(report: dict) -> None:
    if "error" in report:
        print(report["error"])
        return
    print(f"PERFORMANCE REPORT — as of {report['as_of']} (started {report['started']})")
    print(f"  Account value:   ${report['current_equity']:,.2f} (started at ${report['starting_equity']:,.2f})")
    print(f"  Your return:     {report['my_return']:+.2%}")
    if report["spy_return"] is None:
        print("  SPY return:      unavailable (could not fetch SPY data — no verdict today)")
    else:
        print(f"  SPY return:      {report['spy_return']:+.2%} over the same period")
        diff = report["vs_spy"]
        if diff >= 0:
            print(f"  Verdict:         You are BEATING SPY by {diff:.2%}.")
        else:
            print(f"  Verdict:         You are BEHIND SPY by {abs(diff):.2%}.")
    print(f"  Biggest drop from your peak so far: {report['max_drawdown']:.2%}")
    o = report["orders"]
    print(f"  Orders: {o['filled']} filled, {o['expired_or_canceled']} expired/canceled, "
          f"{o['awaiting_check']} not yet checked (run: python3 code/alpaca_client.py --reconcile)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print JSON instead of plain English")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_plain(report)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
