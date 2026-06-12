---
name: daily-paper-trader
description: Run the autonomous daily Alpaca paper-trading routine. Use this skill directly with /daily-paper-trader or in a scheduled Claude Code routine to plan and optionally execute paper trades.
allowed-tools: Bash(bash code/run_daily.sh *), Bash(python3 code/autopilot.py *), Bash(python3 code/alpaca_client.py *), Bash(python3 code/constitution.py *), Bash(python3 code/performance.py *), Bash(python3 -m pip install -r requirements.txt), Read, Edit(state/**)
---

# Daily Paper Trader

Use this for the daily paper-money routine.

## Default Scheduled Run

Run:

```bash
bash code/run_daily.sh --execute
```

The script:
1. Reconciles yesterday's orders (records what really filled, expired, or was canceled).
2. Refreshes Alpaca paper positions.
3. Reads `state/watchlist.txt`.
4. Uses a simple momentum strategy:
   - buy candidates above their 50d and 200d moving averages
   - prefer names outperforming SPY over 20 trading days
   - skip names sold within the last 5 days (cooldown against whipsaw)
   - sell held names below their 50d average or with paper loss worse than 8%
5. Creates trade JSON files.
6. Runs `constitution.py`.
7. Submits LIMIT + DAY paper orders.
8. Saves a daily summary in `state/autopilot_runs/`.

After the run, report performance in plain English:

```bash
python3 code/performance.py
```

## Dry Run

Use this before changing strategy:

```bash
bash code/run_daily.sh --dry-run
```

## Schedule

Use Claude Code `/schedule` for managed cloud execution.

Suggested prompt:

```text
/schedule every weekday at 10:00am New York time, in the Trading repo, run /daily-paper-trader. This is paper trading only. Alpaca paper credentials are supplied as environment variables on the routine's cloud environment. Do not edit strategy files unless the run fails because a file is missing.
```

Set `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and `ALPACA_ENDPOINT` as **environment variables on the routine's cloud environment** (Edit routine → click the environment → Update cloud environment → Environment variables). Routines do **not** read GitHub repository secrets. See README "Step 4" for click-by-click steps.

Managed routines are preferred because they run in Claude Code cloud infrastructure and do not require the laptop to stay awake.
