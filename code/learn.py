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

One asymmetry is deliberate: the learner may CUT risk per trade after a bad
streak, and may RESTORE it toward normal when results improve — but it can
never raise risk above the default. Machines don't get to get greedy.

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
MIN_NEW_EVIDENCE = 3       # finished trades required since the last settings
                           # change — the same old losses must not move a
                           # setting twice
STEPS = {                  # one adjustment step per setting
    "min_outperformance": 0.005,
    "stop_loss_pct": 0.005,
    "atr_stop_mult": 0.25,
    "risk_per_trade": 0.001,
}

PARAMS_COMMENT = (
    "Tunable strategy settings. code/learn.py adjusts these slowly based on "
    "closed-trade results. code/autopilot.py clamps every value to hard bounds, "
    "so edits (by human or learner) can never make the strategy reckless."
)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_fills() -> list[dict]:
    """All trades that really filled, oldest first. A malformed file is skipped, never fatal."""
    fills = []
    if not COMPLETED_DIR.exists():
        return fills
    for path in COMPLETED_DIR.glob("*.json"):
        try:
            t = json.loads(path.read_text())
            if t.get("status") == "filled" and float(t.get("filled_qty") or 0) > 0 and float(t.get("filled_avg_price") or 0) > 0:
                fills.append(t)
        except Exception:
            continue
    fills.sort(key=lambda t: str(t.get("submitted_at_utc") or ""))
    return fills


def exit_rule_of(sell: dict) -> str:
    rule = sell.get("exit_rule")
    if rule:
        return rule
    reason = str(sell.get("reason", "")).lower()
    if ("loss" in reason and "stop" in reason) or "exceeded" in reason:
        return "stop_loss"
    if "trail" in reason or "protecting gains" in reason:
        return "trail_stop"
    if "below" in reason and "50d" in reason:
        return "trend_break"
    if "trim" in reason or "rebalanc" in reason:
        return "rebalance_trim"
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

    by_rule: dict[str, dict] = {}
    for t in trips:
        rule = t["exit_rule"]
        entry = by_rule.setdefault(rule, {"trades": 0, "wins": 0, "pnl_pct_sum": 0.0})
        entry["trades"] += 1
        entry["wins"] += 1 if t["win"] else 0
        entry["pnl_pct_sum"] += t["pnl_pct"]
    for rule, entry in by_rule.items():
        entry["win_rate"] = round(entry["wins"] / entry["trades"], 3)
        entry["avg_pnl_pct"] = round(entry.pop("pnl_pct_sum") / entry["trades"], 4)

    by_ticker: dict[str, dict] = {}
    for t in trips:
        ticker = t["ticker"]
        entry = by_ticker.setdefault(ticker, {"trades": 0, "wins": 0, "total_pnl": 0.0})
        entry["trades"] += 1
        entry["wins"] += 1 if t["win"] else 0
        entry["total_pnl"] += t["pnl"]
    for ticker, entry in by_ticker.items():
        entry["win_rate"] = round(entry["wins"] / entry["trades"], 3) if entry["trades"] > 0 else 0

    losing_streak = 0
    for t in reversed(trips):
        if t["win"]:
            break
        losing_streak += 1

    return {
        "trades": len(trips),
        "win_rate": round(len(wins) / len(trips), 3) if trips else None,
        "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 4) if wins else None,
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 4) if losses else None,
        "profit_factor": round(sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)), 3) if losses and sum(t["pnl"] for t in losses) != 0 else None,
        "losing_streak": losing_streak,
        "total_pnl": round(sum(t["pnl"] for t in trips), 2),
        "by_rule": by_rule,
        "by_ticker": by_ticker,
    }


def _clamp(key: str, value: float) -> float:
    lo, hi = PARAM_BOUNDS[key]
    return round(min(max(value, lo), hi), 4)


def propose_change(stats: dict, params: dict) -> tuple[dict, str] | None:
    """At most ONE setting moves by ONE step, with a plain-English reason."""
    win_rate = stats["win_rate"]
    avg_win = stats["avg_win_pct"]
    avg_loss = stats["avg_loss_pct"]
    profit_factor = stats.get("profit_factor")
    by_rule = stats.get("by_rule", {})
    by_ticker = stats.get("by_ticker", {})

    # 0. Capital preservation first: a losing streak means risk less per trade.
    #    (The learner can cut risk and later restore it, but never raise it
    #    above the default.)
    if stats.get("losing_streak", 0) >= 4:
        new = _clamp("risk_per_trade", params["risk_per_trade"] - STEPS["risk_per_trade"])
        if new != params["risk_per_trade"]:
            reason = (f"{stats['losing_streak']} losses in a row. Risking less per trade "
                      f"({new:.1%} of the account, was {params['risk_per_trade']:.1%}) until results improve.")
            return {"risk_per_trade": new}, reason

    # 1. Losing most trades -> be pickier about what we buy.
    if win_rate is not None and win_rate < 0.40:
        new = _clamp("min_outperformance", params["min_outperformance"] + STEPS["min_outperformance"])
        if new != params["min_outperformance"]:
            reason = (f"Only {win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Raising the bar to buy: a stock must now beat SPY by {new:.1%} "
                      f"(was {params['min_outperformance']:.1%}).")
            return {"min_outperformance": new}, reason

    # 2. Losses much bigger than wins -> cut losers sooner.
    if avg_win is not None and avg_loss is not None and abs(avg_loss) > 1.5 * avg_win:
        new = _clamp("stop_loss_pct", params["stop_loss_pct"] + STEPS["stop_loss_pct"])
        if new != params["stop_loss_pct"]:
            reason = (f"Average loss ({avg_loss:.1%}) is much bigger than average win ({avg_win:.1%}). "
                      f"Tightening the stop loss to {new:.1%} (was {params['stop_loss_pct']:.1%}) "
                      f"to cut losers sooner.")
            return {"stop_loss_pct": new}, reason

    # 3. Trailing stop keeps selling at a loss -> it is too tight (whipsaw).
    trail = by_rule.get("trail_stop")
    if trail and trail["trades"] >= 4 and trail["win_rate"] < 0.40:
        new = _clamp("atr_stop_mult", params["atr_stop_mult"] + STEPS["atr_stop_mult"])
        if new != params["atr_stop_mult"]:
            reason = (f"The trailing stop ended {trail['trades']} trades and most lost money "
                      f"(win rate {trail['win_rate']:.0%}) — it is cutting too early. "
                      f"Widening it to {new:g} ATRs (was {params['atr_stop_mult']:g}).")
            return {"atr_stop_mult": new}, reason

    # 3b. If profit factor is strong (wins > 1.5x losses), ease off restrictions.
    if profit_factor is not None and profit_factor > 1.5:
        if params["min_outperformance"] > PARAM_DEFAULTS["min_outperformance"]:
            new = max(_clamp("min_outperformance", params["min_outperformance"] - STEPS["min_outperformance"]),
                      PARAM_DEFAULTS["min_outperformance"])
            reason = (f"Profit factor is strong ({profit_factor:.2f}x) — easing the entry bar "
                      f"to {new:.1%} (was {params['min_outperformance']:.1%}) to capture more setups.")
            return {"min_outperformance": new}, reason

    # 4. Strategy is working well -> relax one previously-tightened setting,
    #    one step back toward its default (risk gets restored first).
    if win_rate is not None and win_rate >= 0.60:
        if params["risk_per_trade"] < PARAM_DEFAULTS["risk_per_trade"]:
            new = min(_clamp("risk_per_trade", params["risk_per_trade"] + STEPS["risk_per_trade"]),
                      PARAM_DEFAULTS["risk_per_trade"])
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Restoring risk per trade to {new:.1%} (was {params['risk_per_trade']:.1%}).")
            return {"risk_per_trade": new}, reason
        if params["min_outperformance"] > PARAM_DEFAULTS["min_outperformance"]:
            new = max(_clamp("min_outperformance", params["min_outperformance"] - STEPS["min_outperformance"]),
                      PARAM_DEFAULTS["min_outperformance"])
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Easing the buy bar back to {new:.1%} (was {params['min_outperformance']:.1%}).")
            return {"min_outperformance": new}, reason
        if params["stop_loss_pct"] > PARAM_DEFAULTS["stop_loss_pct"]:
            new = max(_clamp("stop_loss_pct", params["stop_loss_pct"] - STEPS["stop_loss_pct"]),
                      PARAM_DEFAULTS["stop_loss_pct"])
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Easing the stop loss back to {new:.1%} (was {params['stop_loss_pct']:.1%}).")
            return {"stop_loss_pct": new}, reason
        if params["atr_stop_mult"] > PARAM_DEFAULTS["atr_stop_mult"]:
            new = max(_clamp("atr_stop_mult", params["atr_stop_mult"] - STEPS["atr_stop_mult"]),
                      PARAM_DEFAULTS["atr_stop_mult"])
            reason = (f"{win_rate:.0%} of the last {stats['trades']} trades made money. "
                      f"Easing the trailing stop back to {new:g} ATRs (was {params['atr_stop_mult']:g}).")
            return {"atr_stop_mult": new}, reason

    return None


def last_change_evidence_count() -> int | None:
    """How many finished trades existed when the settings were last changed."""
    if not CHANGES_FILE.exists():
        return None
    last = None
    for line in CHANGES_FILE.read_text().splitlines():
        try:
            last = json.loads(line)
        except Exception:
            continue
    if last is None:
        return None
    try:
        return int(last.get("evidence_trades_total", 0))
    except (TypeError, ValueError):
        return 0


def apply_change(change: dict, reason: str, stats: dict, evidence_trades_total: int) -> None:
    params = load_params()
    params.update(change)
    body = {k: params[k] for k in PARAM_DEFAULTS}
    body["_comment"] = PARAMS_COMMENT
    PARAMS_FILE.write_text(json.dumps(body, indent=2) + "\n")
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "change": change,
        "reason": reason,
        "based_on": stats,
        "evidence_trades_total": evidence_trades_total,
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

    # Rebalancing trims are bookkeeping, not strategy verdicts — they mostly
    # realize gains on big winners and would flatter the win rate. Judge the
    # strategy only on real exits.
    strategy_trips = [t for t in trips if t["exit_rule"] != "rebalance_trim"]
    recent = strategy_trips[-RECENT_WINDOW:]
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
        if s.get("profit_factor"):
            parts.append(f"profit factor {s['profit_factor']:.2f}x")
        parts.append(f"total P&L ${s['total_pnl']:,.2f}")
        print(f"  Last {s['trades']} trades: " + ", ".join(parts))

        # Show exit rule breakdown
        for rule, r in sorted(s["by_rule"].items()):
            print(f"    exits via {rule}: {r['trades']} trades, "
                  f"win rate {r['win_rate']:.0%}, avg {r['avg_pnl_pct']:+.1%}")

        # Show best/worst performers
        if s.get("by_ticker"):
            by_ticker = s["by_ticker"]
            best = sorted(by_ticker.items(), key=lambda x: x[1]["total_pnl"], reverse=True)[:3]
            worst = sorted(by_ticker.items(), key=lambda x: x[1]["total_pnl"])[:3]

            def _fmt(items):
                return ", ".join(f"{t[0]} ({t[1]['trades']}t, ${t[1]['total_pnl']:+.0f})" for t in items)

            if best:
                print(f"  Best performers: {_fmt(best)}")
            if worst and worst[0][1]["total_pnl"] < 0:
                print(f"  Worst performers: {_fmt(worst)}")
    print(f"  Current settings: buy bar {params['min_outperformance']:.1%} over SPY, "
          f"stop loss {params['stop_loss_pct']:.1%}, trail {params['atr_stop_mult']:g} ATRs, "
          f"risk/trade {params['risk_per_trade']:.1%}, cooldown {params['cooldown_days']}d")

    if len(recent) < MIN_TRADES_TO_LEARN:
        print(f"  Learning: not yet — need {MIN_TRADES_TO_LEARN} finished trades to draw conclusions, "
              f"have {len(recent)}. No settings changed.")
        return 0

    # New-evidence rule: the same old trades must not move a setting twice.
    prior = last_change_evidence_count()
    new_evidence = len(strategy_trips) - prior if prior is not None else None
    if new_evidence is not None and new_evidence < MIN_NEW_EVIDENCE:
        print(f"  Learning: waiting for new evidence — only {max(new_evidence, 0)} finished trade(s) "
              f"since the last settings change (need {MIN_NEW_EVIDENCE}). No settings changed.")
        return 0

    proposal = propose_change(stats, params)
    if proposal is None:
        print("  Learning: results look balanced — no settings changed today.")
        return 0

    change, reason = proposal
    if args.dry_run:
        print(f"  Learning (dry run): WOULD change {change}. {reason}")
        return 0

    apply_change(change, reason, stats, evidence_trades_total=len(strategy_trips))
    print(f"  Learning: changed {change}. {reason}")
    print(f"  (full history in {CHANGES_FILE.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
