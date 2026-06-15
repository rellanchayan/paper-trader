---
name: daily-paper-trader
description: Run the autonomous daily Alpaca paper-trading routine. Use this skill directly with /daily-paper-trader or in a scheduled Claude Code routine to plan and optionally execute paper trades.
allowed-tools: Bash(bash code/run_daily.sh *), Bash(python3 code/autopilot.py *), Bash(python3 code/alpaca_client.py *), Bash(python3 code/constitution.py *), Bash(python3 code/metrics.py *), Bash(python3 -m pip install -r requirements.txt), Read, Edit(state/**)
---

# Daily Paper Trader

Use this for the daily paper-money routine.

## Default Scheduled Run

Run:

```bash
bash code/run_daily.sh --execute
```

The script (`code/autopilot.py` → `code/engine.py`):
1. Reconciles yesterday's orders (records what really filled, expired, or was canceled).
2. Refreshes Alpaca paper positions.
3. Reads `state/watchlist.txt` (stock candidates) and `state/config.json` (frozen params).
4. Plans the **75% hand-picked stocks + 25% ETF ballast** strategy:
   - market regime gate first: no stock-picking when SPY is below its 200d average;
     drawdown brake rotates everything to T-bills below −15% from the high-water mark;
     volatility brake scales risk down when markets are jumpy
   - stock sleeve: names above their 50d and 200d averages that beat SPY, ranked by
     risk-adjusted momentum, then filtered by diversification gates — **max 2 per
     sector** (`state/sectors.json`), **≤10% per name**, correlation gate
   - ETF ballast: SPY + IEF + GLD, each trend-gated; risk-off cash goes to the
     T-bill harbor (SGOV/BIL/USFR…)
   - exits: 50d trend break, regime/drawdown brakes, overweight trims
5. Sizes buys against settled cash, **capped at the daily invest limit**
   (`daily_invest_cap_usd` in `state/config.json`; hard-enforced by `constitution.py`).
6. Runs `constitution.py` on every order (incl. no-day-trade + daily-cap checks).
7. Submits LIMIT + DAY paper orders, sells before buys.
8. Saves a daily summary in `state/runs/`.

After the run it prints a **risk-adjusted** report (Sharpe / drawdown / vol vs SPY):

```bash
python3 code/metrics.py
```

There is **no learning loop** — strategy params are frozen on purpose. Change them
by hand in `state/config.json` only, with a reason, and re-validate on paper.

## Dry Run

Use this before changing strategy:

```bash
bash code/run_daily.sh --dry-run
```

## Schedule

Use Claude Code `/schedule` for managed cloud execution.

Suggested prompt:

```text
/schedule every weekday at 10:00am New York time, in the Trading repo, run /daily-paper-trader. This is paper trading only. Alpaca paper credentials are supplied as environment variables on the routine's cloud environment.
```

Set `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and `ALPACA_ENDPOINT` as **environment variables on the routine's cloud environment** (Edit routine → click the environment → Update cloud environment → Environment variables). Routines do **not** read GitHub repository secrets.

Managed routines run in Claude Code cloud infrastructure and do not require the laptop to stay awake.
