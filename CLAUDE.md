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
