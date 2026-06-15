# CLAUDE.md — Autonomous Paper Trading

This project trades with **paper money only**.

Goal: run a simple daily paper-trading process and try to beat SPY over time.

No system can guarantee profit. This repo should make disciplined paper trades, track results, and keep the rules simple.

---

## 1. Trading Mode

- Paper trading only.
- Use the Alpaca paper endpoint only.
- No live trading.
- No options, futures, shorts, margin, or crypto.
- No market orders.
- Use LIMIT + DAY orders only.

---

## 2. What We Can Trade

- Stocks and normal ETFs are allowed.
- Avoid obvious junk: penny stocks, OTC tickers, leveraged ETFs, inverse ETFs, and volatility ETFs.
- Prefer liquid, well-known companies.

---

## 3. Portfolio Limits

- Max 25% of the portfolio in one ticker.
- Default new buy size is 10% of equity.
- Default max holdings is 8.
- Do not spend more cash than the account has.
- Do not place more than 20 trades in one day.
- Do not place more than 50 trades in one week.

---

## 4. Trade File

Autopilot creates one JSON file in `state/pending_trades/` for each trade.

Use this shape:

```json
{
  "trade_id": "T-YYYYMMDD-001",
  "ticker": "AAPL",
  "side": "BUY",
  "qty": 1,
  "limit_price": 100.00,
  "order_type": "LIMIT",
  "time_in_force": "DAY",
  "reason": "Short reason for the trade.",
  "risk": "Main risk.",
  "status": "ready",
  "strategy": "daily_momentum"
}
```

No reason means no trade.

---

## 5. Commands

Useful commands:

```bash
python3 code/alpaca_client.py --healthcheck
python3 code/alpaca_client.py --positions
python3 code/alpaca_client.py --quote AAPL
bash code/run_daily.sh --dry-run
bash code/run_daily.sh --execute
python3 code/constitution.py --check state/pending_trades/T-YYYYMMDD-001.json
python3 code/alpaca_client.py --submit state/pending_trades/T-YYYYMMDD-001.json
python3 code/alpaca_client.py --order-status <order_id>
```

---

## 6. Stop Rules

Do not trade if:

- `.HALT_TRADING` exists
- Alpaca paper API is down
- The trade fails `python3 code/constitution.py --check <trade_json>`
- The order is not LIMIT + DAY
- The account does not have enough cash

If `.HALT_TRADING` exists:

- Do not place new orders.
- Do not delete the halt file.
- Log that trading is halted.

---

## 7. Logging Rules

- No markdown logs.
- Alpaca stores order history.
- Trade JSON files store local notes.
- Submitted trades are moved to `state/completed_trades/`.
- Daily autopilot summaries go in `state/autopilot_runs/`.

---

## 8. Honesty Rules

- Never say an order filled if it did not.
- Never hide a loss.
- Never invent data.
- Never claim certainty about future prices.
- If data is missing, say what is missing.
- If the rules do not cover a situation, stop and ask Chayan.

---

## 9. Core Idea

Learn by doing, but keep it honest.

Paper money is for practice. Keep the system simple.

---

## 10. Active Strategy — DTA (Diversified Trend Allocator)

The active strategy is the **DTA**, in `code/dta_autopilot.py`. The old momentum
bot (`code/autopilot.py`) is **legacy**, kept only for paper comparison
(`bash code/run_daily.sh --momentum`).

**What it is (plain English):** instead of picking hot stocks, hold a small
basket of broad ETFs — US stocks (SPY), international (VEA), Treasuries (IEF),
gold (GLD), real estate (VNQ), equal-weighted — plus a permanent cash-like
cushion in ultra-short T-bill ETFs (SGOV/BIL/USFR…). Each sleeve has one rule:
above its 200-day average → hold it; below → sell it and park the money in
T-bills (which earn the bill rate). Checked **daily for selling**, rebalanced
**monthly for buying**. A small momentum "satellite" (the `watchlist.txt`
stocks) is **off** by default and only turns on after the core proves itself.

**Files:** `dta_signals.py` (pure math), `dta_engine.py` (pure planner),
`dta_autopilot.py` (entry point), `dta_metrics.py` (risk-adjusted report).
Config: `state/dta_universe.json` (what it trades) + `state/dta_config.json`
(frozen constants). State: `state/dta_state.json` (trend-gate memory). Runs in
`state/dta_runs/`.

**No learning loop.** DTA parameters are frozen on purpose — tuning a weak
signal on a few dozen trades fits noise. Do not re-enable auto-tuning for DTA.

**Read "beat SPY" as beat SPY RISK-ADJUSTED.** The DTA is built to lose less in
bad markets, not to win more in good ones. It will trail SPY in raw return
during bull markets — possibly for years — and that is the design working, not
failing. Judge it on Sharpe / max-drawdown / volatility vs SPY (see
`dta_metrics.py`), never on raw monthly return.

**Real money:** not yet. See `MIGRATION_CHECKLIST.md` for what must be proven on
paper first. `.HALT_TRADING` stays a manual, human-only kill switch; the
automated drawdown brake rotates to T-bills, it does NOT halt.
