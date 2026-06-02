# Paper Trader

A simple automated stock trading bot that trades with **fake money only**. It runs every weekday morning and automatically buys and sells stocks based on market trends.

## What It Does

Every morning at 10:00 AM (Eastern Time), the bot:
1. Checks which stocks are in an uptrend (going up)
2. Buys stocks that are performing well
3. Sells stocks that are no longer going up
4. Keeps a record of all trades

Think of it like a robot that watches the market and makes small trades automatically.

## Important Rules

- **Paper money only** — No real money is used. All trades are pretend.
- **Simple trades** — Only buys and sells. No options, shorts, or risky stuff.
- **Limit orders only** — Never pays a price higher than what we set.
- **Portfolio limits** — Never puts more than 25% in one stock.
- **Daily limit** — Never places more than 20 trades per day.

## Setup

### 1. Get Alpaca Account
- Go to https://app.alpaca.markets
- Sign up for a free account
- Get your **Paper Trading API Key** and **Secret Key**

### 2. Add Credentials to GitHub
Go to your GitHub repo settings and add these **Agents secrets**:
- `ALPACA_API_KEY` — Your API key from Alpaca
- `ALPACA_SECRET_KEY` — Your secret key from Alpaca
- `ALPACA_ENDPOINT` — `https://paper-api.alpaca.markets`

And these **Agents variables**:
- `LIVE_TRADING_AUTHORIZED` — `false` (always false)
- `RISK_FREE_RATE` — `0.045` (optional, for Sharpe ratio)

### 3. The Bot Runs Automatically
Once set up, the bot runs every weekday at 10:00 AM ET. You don't need to do anything.

## How to Check It

### View Your Trades
Go to https://app.alpaca.markets/paper/dashboard and see all your orders.

### View the Bot's Logs
Check the `state/autopilot_runs/` folder to see what the bot did each day.

## Testing Locally

If you want to test it yourself:

```bash
# Dry run — shows what it would do (no actual trades)
bash code/run_daily.sh --dry-run

# Execute — actually places trades
bash code/run_daily.sh --execute
```

## The Strategy

The bot looks for stocks that are:
1. **Going up** — Price is above the 50-day and 200-day averages
2. **Hot** — Gained more than the market average in the last 20 days
3. **Popular** — Well-known companies (AAPL, MSFT, NVDA, etc.)

It sells stocks when they:
1. Start going down (price below 50-day average)
2. Have lost more than 8% from when we bought it

## Stopping the Bot

To pause trading, create a file called `.HALT_TRADING` in the main folder:

```bash
touch .HALT_TRADING
```

The bot will see this file and stop placing new orders. Delete it to resume.

## Files That Matter

- `code/run_daily.sh` — The main script that runs every morning
- `code/constitution.py` — The safety checks (prevents real money trades)
- `code/alpaca_client.py` — Talks to Alpaca API
- `state/watchlist.txt` — List of stocks to watch
- `state/pending_trades/` — Trades waiting to be checked
- `state/completed_trades/` — Trades that were placed
- `state/autopilot_runs/` — Daily logs of what happened

## Troubleshooting

**Bot isn't running?**
- Check that GitHub secrets are set correctly
- Go to https://claude.ai/code/routines and look for errors

**Trades not placing?**
- Check if `.HALT_TRADING` file exists (delete it if it does)
- Verify Alpaca credentials are correct
- Check `state/autopilot_runs/` for the log

**Want to change the strategy?**
- Edit `code/run_daily.sh` to change which stocks it buys/sells
- Test with `--dry-run` first

## Contact

Questions? Check the `CLAUDE.md` file for technical details.
