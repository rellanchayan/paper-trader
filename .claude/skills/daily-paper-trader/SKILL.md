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
4. Uses a wealth-manager-style momentum strategy (tunables in `state/strategy_params.json`):
   - regime checks first: no new buys if SPY is below its 200d average OR the
     account is >10% below its high-water mark; half-size buys when market
     volatility is elevated
   - buy candidates above their 50d and 200d averages that are up at least
     `min_outperformance` over 20 trading days AND beat SPY by the same
     margin, ranked by risk-adjusted momentum (1m/3m/6m blend / volatility)
   - diversification gates: max `max_per_sector` holdings per sector
     (`state/sectors.json`), skip candidates correlated > `max_corr` with a
     holding, never chase a >2% gap, cooldown `cooldown_days` after a sell
   - position sizing by risk: each buy risks ~`risk_per_trade` of equity
     (ATR-based), capped at 10% of equity per position
   - exits: hard stop `stop_loss_pct`, trailing stop `atr_stop_mult` ATRs
     below the 22d high, 50d trend break, and overweight trims above 20%
5. Creates trade JSON files.
6. Runs `constitution.py`.
7. Submits LIMIT + DAY paper orders.
8. Saves a daily summary in `state/autopilot_runs/`.

After the run, let the bot learn from finished trades and report performance:

```bash
python3 code/learn.py
python3 code/performance.py
```

`learn.py` grades every finished BUY→SELL pair and may adjust ONE setting in
`state/strategy_params.json` by one small step (within hard bounds enforced by
`autopilot.py`). Every change is logged with its reason in
`state/strategy_changes.jsonl`. Do not hand-edit strategy settings in the same
run; let the evidence-based loop do it.

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
