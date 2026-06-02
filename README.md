# Paper Trader 📈

**An automated stock trading bot that buys and sells stocks using fake money.**

This is a simple, smart robot that watches the stock market every weekday morning, finds good trading opportunities, and places orders automatically. All trades use **fake money only** — nothing is real.

---

## What This Does (In Plain English)

Every weekday morning at **10:00 AM Eastern Time**, the bot:

1. **Wakes up** — Connects to your Alpaca paper trading account
2. **Looks at the market** — Checks 10+ stocks to see which ones are hot
3. **Finds opportunities** — Identifies stocks that are going up strong
4. **Makes smart trades** — Buys good stocks, sells weak ones
5. **Logs everything** — Saves a record of what it did
6. **Sleeps** — Waits until tomorrow to do it again

Think of it like having a mini trader in your computer who works while you sleep. No emotions, no second-guessing — just following the rules.

---

## Getting Started (5 Minutes)

### Step 1: Install Claude Code

First, make sure you have Claude Code installed:
- **Web:** Go to https://claude.ai/code
- **Desktop:** Download Claude Code for Mac or Windows
- **CLI:** `npm install -g @anthropic-ai/claude`

### Step 2: Add the Paper Trader Package

```bash
claude add paper-trader
```

This downloads the complete trading bot into a new folder.

### Step 3: Get Alpaca Credentials

1. Go to https://app.alpaca.markets
2. Sign up (free account)
3. Click **Settings** → **API Keys**
4. Copy your **paper trading API key** and **secret key**
   - The word "PAPER" should be visible in your key name
   - This is fake money only — that's important!

### Step 4: Set Up GitHub Secrets

Your bot runs in the cloud (so your computer doesn't need to stay on). You need to tell GitHub your Alpaca keys:

1. Go to https://github.com/rellanchayan/paper-trader
2. Click **Settings** → **Secrets and variables** → **Agents**
3. Click **New repository secret** and add:
   - **Name:** `ALPACA_API_KEY` → **Value:** (paste your API key)
   - **Name:** `ALPACA_SECRET_KEY` → **Value:** (paste your secret key)
   - **Name:** `ALPACA_ENDPOINT` → **Value:** `https://paper-api.alpaca.markets`

4. Add these **Variables** (plain text, not secret):
   - **Name:** `LIVE_TRADING_AUTHORIZED` → **Value:** `false`
   - **Name:** `RISK_FREE_RATE` → **Value:** `0.045`

### Step 5: Start Trading

That's it! The bot will run automatically every weekday at 10am ET.

---

## How It Works (The Smart Part)

### The Trading Strategy

The bot looks for stocks that meet **all three** conditions:

1. **Uptrend** — Price is above its 50-day and 200-day averages
   - Imagine a stock is climbing steadily uphill
   - We only buy when it's clearly going up, not down

2. **Outperforming the market** — Gained more than SPY in the last 20 days
   - SPY is like "the average market"
   - We want stocks doing better than average
   - If SPY is up 5% but a stock is up 15%, that stock is hot

3. **Well-known companies** — From our watchlist (AAPL, MSFT, NVDA, etc.)
   - No penny stocks or weird stuff
   - We stick to popular, liquid stocks

### Buying Rules

When the bot finds a good stock:
- **Amount:** Buys ~10% of account balance per trade
- **Price:** Uses a limit order (never pays more than we set)
- **Time:** Only during market hours (9:30am - 4:00pm ET)
- **Order type:** Limit order with DAY time-in-force (expires if not filled by end of day)

### Selling Rules

The bot sells when a stock:
- Falls below its 50-day average (trend is broken)
- OR loses more than 8% from when we bought it (cutting losses early)

### Safety Guardrails

Hard limits that can **never** be broken:

| Rule | Limit | Why |
|------|-------|-----|
| Max in one stock | 25% of portfolio | Don't over-concentrate risk |
| Trades per day | 20 maximum | Avoid overtrading |
| Trades per week | 50 maximum | Stay disciplined |
| Order type | LIMIT only | Never market orders |
| Account type | Paper only | Never real money |

If any rule would be broken, the trade is automatically rejected.

---

## Checking Your Results

### View Live Trades
Go to https://app.alpaca.markets/paper/dashboard
- See all your open positions
- Watch orders fill in real-time
- Check profit/loss on each trade
- Track your paper account balance

### View Bot's Daily Log
In your folder: `state/autopilot_runs/`

Each day gets a file like `20260602-124530.json` showing:
- Market conditions (SPY return, etc.)
- All stocks it analyzed
- Which trades it planned
- Which orders were actually placed
- Portfolio balance after trades

### View All Trade History
In your folder: `state/completed_trades/`

Each file shows:
- Buy or sell order
- Stock symbol and quantity
- Limit price set
- When it was placed
- Order ID from Alpaca

---

## Testing (Before Running Live)

Want to see what the bot would do without actually trading? Easy:

### Dry Run (Simulation - No Real Orders)
```bash
bash code/run_daily.sh --dry-run
```

Shows you the complete plan:
- Which trades it would place
- How much each would cost
- Exactly what would happen
- **But doesn't actually order anything**

### Live Run (Real Orders)
```bash
bash code/run_daily.sh --execute
```

Actually places orders on Alpaca for real.

**Pro tip:** Always run `--dry-run` first to see what's coming.

---

## Stopping the Bot (Emergency Only)

If you ever need to pause trading instantly:

```bash
touch .HALT_TRADING
```

This creates a "stop sign" file. The bot sees it and:
- Stops placing new orders immediately
- Still processes existing orders
- Doesn't delete any open positions

To resume trading:
```bash
rm .HALT_TRADING
```

---

## Understanding the Files

When you install with `claude add paper-trader`, you get this structure:

```
paper-trader/
├── code/
│   ├── run_daily.sh          ← Main script (runs every morning at 10am ET)
│   ├── autopilot.py          ← The trading strategy and decision logic
│   ├── alpaca_client.py       ← Communicates with Alpaca's API
│   └── constitution.py        ← Safety checks (can't be overridden)
│
├── state/
│   ├── pending_trades/        ← Trades waiting to be checked by constitution.py
│   ├── completed_trades/      ← Trades that were actually placed with Alpaca
│   ├── autopilot_runs/        ← Daily log of what happened each morning
│   ├── portfolio.json         ← Your current positions and balance
│   ├── portfolio_history.jsonl ← Record of all past positions
│   └── watchlist.txt          ← Stocks to watch (you can edit this)
│
├── .claude/
│   ├── settings.json          ← Permissions (what Claude can do)
│   ├── skills/                ← The daily-paper-trader skill
│   └── agents/                ← Helper bots (research, risk check)
│
├── .env.example               ← Template (copy to .env and add your keys)
├── CLAUDE.md                  ← The official rules (read this!)
├── README.md                  ← This file
└── requirements.txt           ← Python libraries needed
```

### What Each File Does

| File | Purpose |
|------|---------|
| `run_daily.sh` | Main entry point. Calls the strategy, checks rules, places orders |
| `autopilot.py` | The brain. Analyzes stocks, decides what to buy/sell |
| `alpaca_client.py` | The messenger. Sends orders to Alpaca, gets market data |
| `constitution.py` | The guardian. Checks every trade against safety rules |
| `watchlist.txt` | The targets. Which stocks to analyze each morning |
| `portfolio.json` | Your positions. Updated after every trade |
| `autopilot_runs/` | The journal. One file per morning showing what happened |

---

## Customizing the Watchlist

The bot watches specific stocks by default. Want to change them?

Edit `state/watchlist.txt` with your favorite stocks:

```
AAPL
MSFT
NVDA
GOOGL
TSLA
META
```

One stock per line. The bot will automatically analyze your custom list. Add as many as you want — it will only trade if they meet the strategy criteria.

---

## Common Questions

### How much money do I need?

None! It's fake money. Alpaca gives everyone $100,000 in paper trading credit. You can trade like it's real without any risk.

### Can it lose money?

Yes. Paper trading is practice, but the losses are fake. If the bot loses, you learn. If it wins, great! Either way, no real money is at risk.

### Is this guaranteed to make money?

No. The stock market is unpredictable. This bot uses a simple momentum strategy that works sometimes and fails sometimes. Think of it as an experiment, not a guaranteed money maker.

### Why LIMIT orders only?

LIMIT orders let you say "I'll buy AAPL but only at $150 or less." Market orders just buy at whatever price is available — which can be bad during volatile markets. We're disciplined.

### What if market is closed?

The bot checks if the market is open before placing orders. If it's a holiday or weekend, it does nothing. Orders are only for market hours (9:30am - 4:00pm ET).

### Can I run it without Claude Code?

Yes, if you're comfortable with the command line:
```bash
python3 -m pip install -r requirements.txt
bash code/run_daily.sh --execute
```

But Claude Code makes it easier because it runs in the cloud (you don't need to keep your laptop on).

### What if I find a bug?

Check the `state/autopilot_runs/` folder for the log. It shows exactly what happened. If something looks wrong:
1. Report it with the log
2. You can edit the strategy in `code/autopilot.py`
3. Test changes with `--dry-run` first
4. Use Claude Opus 4.8 to help debug and improve

### Can I trade real money with this?

**No.** The code has built-in safeguards that refuse real money accounts. Even if you try to change it, `constitution.py` blocks it. This is intentional — if you want real trading later, write new code with proper real-money protections.

### Will it work during market crashes?

Yes, but be aware: during huge crashes, limit prices might not fill (stock falls faster than your limit). The bot handles this correctly — it just keeps the order open or lets it expire.

---

## Power User Features

### Use Claude Opus 4.8 for Analysis

Claude Opus 4.8 is the best model for:
- Understanding market trends
- Tuning the strategy
- Analyzing why trades succeeded or failed
- Getting smarter about which stocks to watch

Ask it questions about your logs:
> "Why did the bot sell MSFT yesterday? Was it the right call?"

### Custom Strategy

The trading logic is in `code/autopilot.py`. You can edit:
- How much to buy/sell per trade
- When to buy (different moving averages, different momentum thresholds)
- When to sell (different loss thresholds)
- Which stocks to ignore

Just test with `--dry-run` before running `--execute`.

### Manual Trades

You can place trades manually via Alpaca's web dashboard. The bot won't interfere — it just adds to what you do.

### Monitoring Routines

The bot uses Claude Code routines to run automatically. Check:
- https://claude.ai/code/routines to see scheduled runs
- View logs of past runs
- See any error messages
- Re-run manually if needed

---

## Troubleshooting

| Problem | Check First | Solution |
|---------|-----|----------|
| Bot isn't running | `.HALT_TRADING` file exists? | Delete it: `rm .HALT_TRADING` |
| No trades placed | Run `--dry-run` to see plan | Check if market is open. Check logs. |
| Orders not filling | Limit price set too low? | Check Alpaca dashboard for order status |
| Python errors | Requirements installed? | Run `pip install -r requirements.txt` |
| Settings not working | `.claude/settings.json` correct? | Check paths are relative (not hardcoded) |
| Credentials rejected | Secrets added to GitHub? | Go to Secrets and Variables, verify they're there |

### Reading the Logs

When something goes wrong, check `state/autopilot_runs/` for the daily log:

```json
{
  "timestamp_utc": "2026-06-02T16:43:22",
  "mode": "execute",
  "market_open": true,
  "blocked": null,
  "equity": 99873.67,
  "cash": 80344.75,
  "held": ["AVGO", "NVDA"],
  "planned": [...],
  "submitted": [...],
  "diagnostics": [...]
}
```

- **equity** = Total account value
- **cash** = Money not invested
- **held** = Stocks you own
- **planned** = Trades the bot wanted to make
- **submitted** = Orders actually placed
- **diagnostics** = Data on each stock analyzed

---

## The Rules (From CLAUDE.md)

Read `CLAUDE.md` in the folder for the official rules. Quick summary:

- **Paper money only** — Never real money
- **Simple orders** — LIMIT + DAY only (no risky stuff)
- **Position limits** — Max 25% in one stock, max 20 trades/day
- **Honest logging** — Never hide losses or fake data
- **Ask when stuck** — If rules don't cover something, ask before doing it

---

## Tips for Success

1. **Start with dry-runs** — Run `--dry-run` first to get comfortable
2. **Watch the logs** — Read `state/autopilot_runs/` every day to learn
3. **Check your portfolio** — Log into Alpaca daily to see positions
4. **Be patient** — Momentum trading takes time. Don't expect instant profits
5. **Keep learning** — Edit the strategy, test changes, experiment
6. **Use Claude Opus 4.8** — It's the best brain for market analysis
7. **Journal your trades** — Write down why each trade happened and what you learned

---

## Next Steps

1. ✅ Install with `claude add paper-trader`
2. ✅ Set up Alpaca account and get API keys
3. ✅ Add secrets to GitHub
4. ✅ Run a dry-run to see the strategy
5. ✅ Let it run automatically (10am ET weekdays)
6. ✅ Check results in Alpaca and logs folder
7. ✅ Tune the strategy as you learn

---

## Need Help?

- **About the code** — Ask Claude Code (type `/help`)
- **About trading strategy** — Ask Claude Opus 4.8 (best model for this)
- **About Alpaca API** — Visit https://docs.alpaca.markets
- **About this bot** — Read `CLAUDE.md` for official rules and tech details

---

**Made with ❤️ for learning, not for guaranteed profits.**

Paper trading is practice. Have fun. Learn. Experiment. 🎯
