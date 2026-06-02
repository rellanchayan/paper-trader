---
name: daily-paper-trader
description: Run the autonomous daily Alpaca paper-trading routine. Use this skill directly with /daily-paper-trader or in a scheduled Claude Code routine to plan and optionally execute paper trades.
allowed-tools: Bash(bash code/run_daily.sh *), Bash(python3 code/autopilot.py *), Bash(python3 code/alpaca_client.py *), Bash(python3 code/constitution.py *), Bash(python3 -m pip install -r requirements.txt), Read, Edit(state/**)
---

# Daily Paper Trader

Use this for the daily paper-money routine.

## Default Scheduled Run

Run:

```bash
bash code/run_daily.sh --execute
```

The script:
1. Refreshes Alpaca paper positions.
2. Reads `state/watchlist.txt`.
3. Uses a simple momentum strategy:
   - buy candidates above their 50d and 200d moving averages
   - prefer names outperforming SPY over 20 trading days
   - sell held names below their 50d average or with paper loss worse than 8%
4. Creates trade JSON files.
5. Runs `constitution.py`.
6. Submits LIMIT + DAY paper orders.
7. Saves a daily summary in `state/autopilot_runs/`.

## Dry Run

Use this before changing strategy:

```bash
bash code/run_daily.sh --dry-run
```

## Schedule

Use Claude Code `/schedule` for managed cloud execution.

Suggested prompt:

```text
/schedule every weekday at 10:00am New York time, in the Trading repo, run /daily-paper-trader. This is paper trading only. Use the repository environment secrets for Alpaca paper credentials. Do not edit strategy files unless the run fails because a file is missing.
```

Managed routines are preferred because they run in Claude Code cloud infrastructure and do not require the laptop to stay awake.
