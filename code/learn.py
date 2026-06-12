"""
learn.py — the bot's after-school review. Runs after each trading run.

What it does, in plain English:
1. Pairs up every finished BUY -> SELL (using real fill prices from --reconcile)
   and grades it: profit or loss, how long it was held, which rule ended it.
2. Writes the full report card to state/trade_reviews.jsonl.
3. If there is enough evidence (at least 10 finished trades), it may adjust
   ONE strategy setting by ONE small step, and writes down why in
   state/strategy_changes.jsonl.

Why so cautious? With only a few trades, "learning" is just chasing noise.
Slow, bounded, explained changes are the honest kind. autopilot.py clamps
every setting to hard bounds, so this script can never make the bot reckless.

Usage:
    python3 code/learn.py            # review trades, maybe adjust one setting
    python3 code/learn.py --dry-run  # show what it would do, change nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autopilot import PARAM_BOUNDS, PARAM_DEFAULTS, PARAMS_FILE, load_params  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMPLETED_DIR = ROOT / "state" / "completed_trades"
REVIEWS_FILE = ROOT / "state" / "trade_reviews.jsonl"
CHANGES_FILE = ROOT / "state" / "strategy_changes.jsonl"

MIN_TRADES_TO_LEARN = 10   # don't draw conclusions from less than this
RECENT_WINDOW = 20         # judge the strategy on its last N finished trades
STEP = 0.005               # one adjustment step


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_fills() -> list[dict]:
    """All trades that really filled, oldest first."""
    fills = []
    if not COMPLETED_DIR.exists():
        return fills
    for path in COMPLETED_DIR.glob("*.json"):
        try:
            t = json.loads(path.read_text())
        except Exception:
            continue
        if t.get("status") == "filled" and float(t.get("filled_qty") or 0) > 0 and t.get("filled_avg_price"):
            fills.append(t)
    fills.sort(key=lambda t: t.get("submitted_at_utc", ""))
    return fills


def exit_rule_of(sell: dict) -> str:
    rule = sell.get("exit_rule")
    if rule:
        return rule
    reason = str(sell.get("reason", "")).lower()
    if "loss" in reason and "stop" in reason or "exceeded" in reason:
        return "stop_loss"
    if "below" in reason and "50d" in reason:
        return "trend_break"
    return "unknown"


def build_round_trips(fills: list[dict]) -> tuple[list[dict], int]:
    """Match SELL fills to earlier BUY fills per ticker (first-in, first-out)."""
    lots: dict[str, list[dict]] = {}
    trips: list[dict] = []
    unmatched_sells = 0

    for trade in fills:
        ticker = str(trade["ticker"]).upper()
        qty = float(trade["filled_qty"])
        price = float(trade["filled_avg_price"])
        when = trade.get("submitted_at_utc", "")

        if str(trade["side"]).upper() == "BUY":
            lots.setdefault(ticker, []).append({"qty": qty, "price": price, "when": when})
            continue

        remaining = qty
        while remaining > 0 and lots.get(ticker):
            lot = lots[ticker][0]
            matched = min(remaining, lot["qty"])
            pnl = (price - lot["price"]) * matched
            pnl_pct = price / lot["price"] - 1
            try:
                days_held = max(0, (_parse_dt(when) - _parse_dt(lot["when"])).days)
            except Exception:
                days_held = None
            trips.append({
                "ticker": ticker,
                "qty": matched,
                "entry_price": round(lot["price"], 4),
                "exit_price": round(price, 4),
                "entry_date": lot["when"][:10],
                "exit_date": when[:10],
                "days_held": days_held,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 4),
                "win": pnl > 0,
                "exit_rule": exit_rule_of(trade),
            })
            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= 1e-9:
                lots[ticker].pop(0)
        if remaining > 1e-9:
            unmatched_sells += 1

    return trips, unmatched_sells


def stats_for(trips: list[dict]) -> dict:
    wins = [t for t in trips if t["win"]]
    losses = [t for t in trips if not t["win"]]
    stop_outs = [t for t in trips if t["exit_rule"] == "stop_loss"]
    return {
        "trades": len(trips),
        "win_rate": round(len(wins) / len(trips), 3) if trips else None,
        "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 4) if wins else None,
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 4) if losses else None,
        "stop_out_share": round(len(stop_outs) / len(trips), 3) if trips else None,
        "total_pnl": round(sum(t["pnl"] for t in trips), 2),
    }


def _clamp(key: str, value: float) -> float:
    lo, hi = PARAM_BOUNDS[key]
    return round(min(max(value, lo), hi), 4)


def propose_change(stats: dict, params: dict) -> tuple[dict, str] | None:
    """At most ONE setting moves by ONE step, with a plain-English reason."""
    win_rate = stats["win_rate"]
    avg_win = stats["avg_win_pct"]
    avg_loss = stats["avg_loss_pct"]

    # 1. Losing most trades -> be pickier about what we buy.
    if win_rate is not None and win_rate < 0.40:
        new = _clamp("min_outperformance", params["min_outperformance"] + STEP)
        if new != params["min_outperformance"]:
            reason = (f"Only {win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Raising the bar to buy: a stock must now beat SPY by {new:.1%} (was {params['min_outperformance']:.1%}).")
            return {"min_outperformance": new}, reason

    # 2. Losses much bigger than wins -> cut losers sooner.
    if avg_win is not None and avg_loss is not None and abs(avg_loss) > 1.5 * avg_win:
        new = _clamp("stop_loss_pct", params["stop_loss_pct"] + STEP)
        if new != params["stop_loss_pct"]:
            reason = (f"Average loss ({avg_loss:.1%}) is much bigger than average win ({avg_win:.1%}). "
                      f"Tightening the stop loss to {new:.1%} (was {params['stop_loss_pct']:.1%}) to cut losers sooner.")
            return {"stop_loss_pct": new}, reason

    # 3. Strategy is working well -> relax any setting we previously tightened,
    #    one step back toward its default.
    if win_rate is not None and win_rate >= 0.60:
        if params["min_outperformance"] > PARAM_DEFAULTS["min_outperformance"]:
            new = _clamp("min_outperformance", params["min_outperformance"] - STEP)
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Easing the buy bar back to {new:.1%} (was {params['min_outperformance']:.1%}).")
            return {"min_outperformance": new}, reason
        if params["stop_loss_pct"] > PARAM_DEFAULTS["stop_loss_pct"]:
            new = _clamp("stop_loss_pct", params["stop_loss_pct"] - STEP)
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Easing the stop loss back to {new:.1%} (was {params['stop_loss_pct']:.1%}).")
            return {"stop_loss_pct": new}, reason

    return None


def apply_change(change: dict, reason: str, stats: dict) -> None:
    params = load_params()
    params.update(change)
    body = {k: params[k] for k in PARAM_DEFAULTS}
    body["_comment"] = ("Tunable strategy settings. code/learn.py adjusts these slowly based on "
                        "closed-trade results. code/autopilot.py clamps every value to hard bounds, "
                        "so edits (by human or learner) can never make the strategy reckless.")
    PARAMS_FILE.write_text(json.dumps(body, indent=2) + "\n")
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "change": change,
        "reason": reason,
        "based_on": stats,
    }
    with CHANGES_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show analysis, change nothing")
    args = parser.parse_args()

    fills = load_fills()
    trips, unmatched = build_round_trips(fills)

    # Rebuild the journal from the trade files every run — one source of truth.
    REVIEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEWS_FILE.write_text("".join(json.dumps(t) + "\n" for t in trips))

    recent = trips[-RECENT_WINDOW:]
    stats = stats_for(recent)
    params = load_params()

    print(f"TRADE REVIEW — {len(trips)} finished trades on record"
          + (f" ({unmatched} sell(s) had no matching buy on file — skipped)" if unmatched else ""))
    if recent:
        s = stats
        parts = [f"win rate {s['win_rate']:.0%}"]
        if s["avg_win_pct"] is not None:
            parts.append(f"avg win {s['avg_win_pct']:+.1%}")
        if s["avg_loss_pct"] is not None:
            parts.append(f"avg loss {s['avg_loss_pct']:+.1%}")
        parts.append(f"total P&L ${s['total_pnl']:,.2f}")
        print(f"  Last {s['trades']} trades: " + ", ".join(parts))
    print(f"  Current settings: buy bar {params['min_outperformance']:.1%} over SPY, "
          f"stop loss {params['stop_loss_pct']:.1%}, cooldown {params['cooldown_days']}d")

    if len(recent) < MIN_TRADES_TO_LEARN:
        print(f"  Learning: not yet — need {MIN_TRADES_TO_LEARN} finished trades to draw conclusions, "
              f"have {len(recent)}. No settings changed.")
        return 0

    proposal = propose_change(stats, params)
    if proposal is None:
        print("  Learning: results look balanced — no settings changed today.")
        return 0

    change, reason = proposal
    if args.dry_run:
        print(f"  Learning (dry run): WOULD change {change}. {reason}")
        return 0

    apply_change(change, reason, stats)
    print(f"  Learning: changed {change}. {reason}")
    print(f"  (full history in {CHANGES_FILE.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
