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

### Step 4: Set Up Your Routine's Environment Variables

Your bot runs in the cloud as a **Claude Code routine** (so your computer doesn't need to stay on). The routine clones this repo into a fresh sandbox and runs with the **environment variables you set on its cloud environment** — it does **not** read GitHub repository secrets. Put your Alpaca keys there:

1. Go to **https://claude.ai/code/routines** and open your paper-trader routine.
2. Click the **pencil** icon (**Edit routine**).
3. Below the **Instructions** box, click the **cloud icon** showing the environment name (e.g. **Default**), hover over the environment in the list, and click the **settings gear** to open **Update cloud environment**.
4. In the **Environment variables** section, add each of these:
   - `ALPACA_API_KEY` → (paste your paper API key)
   - `ALPACA_SECRET_KEY` → (paste your paper secret key)
   - `ALPACA_ENDPOINT` → `https://paper-api.alpaca.markets`
   - `LIVE_TRADING_AUTHORIZED` → `false`
   - `RISK_FREE_RATE` → `0.045`
5. Click **Save changes**. Values apply on the next run — click **Run now** to test immediately.

> These are **paper** credentials (no real money), and `constitution.py` refuses any non-paper endpoint regardless, so storing them on the cloud environment is safe here.
>
> **Running locally instead?** Copy `.env.example` to `.env` and put the same values there — `load_dotenv()` reads them at startup. (`.env` is git-ignored and never committed.)

### Step 5: Make It Run Every Morning

This is the step most people get stuck on, so it has its own section below:
**[Make It Run Every Morning](#make-it-run-every-morning-pick-one)**. Pick one
of the three options there and you're done.

---

## Make It Run Every Morning (Pick ONE)

You only need **one** of these three options. Here they are, easiest first.

### Option A: GitHub Actions (recommended — free, runs in the cloud, laptop can be off)

This repo already contains the schedule file (`.github/workflows/daily-trade.yml`).
GitHub will run the bot every weekday morning for free. You just need to give
GitHub your Alpaca paper keys, one time:

1. Push this repo to GitHub (it already points at `github.com/rellanchayan/paper-trader`):
   ```bash
   git add -A && git commit -m "enable daily schedule" && git push
   ```
2. Open your repo on github.com → **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret** and add these two (paper keys only):
   - Name: `ALPACA_API_KEY` → Value: your paper API key
   - Name: `ALPACA_SECRET_KEY` → Value: your paper secret key
4. Test it right now without waiting for the morning: repo → **Actions** tab →
   **Daily paper trade** → **Run workflow** button. Watch the log live.

After that, it runs by itself every weekday around 9:35–10:35am New York time.
Each run also commits its records back to the repo, so you can read the full
history of what the bot did from any device.

> If your repo is public, note that your trade history (not your keys) will be
> visible. Make the repo private if you'd rather keep it to yourself:
> repo → Settings → General → Danger Zone → Change visibility.

### Option B: Claude Code Routine (the bot gets a brain)

Option A runs the script exactly as written. Option B has Claude *run* the
routine — so it can also notice problems, read the logs, and adapt. In any
Claude Code session in this folder, type:

```text
/schedule every weekday at 10:00am New York time, in the Trading repo, run /daily-paper-trader. This is paper trading only. Alpaca paper credentials are supplied as environment variables on the routine's cloud environment. After trading, commit and push the state/ folder so records persist.
```

Then add your Alpaca keys to the routine's **cloud environment** (see Step 4
above for the click-by-click). Manage runs at https://claude.ai/code/routines.

### Option C: Your Own Mac (simple, but the laptop must be awake)

If you'd rather keep everything on your machine, schedule it with `launchd`
(the Mac's built-in scheduler). One-time setup — paste this into Terminal:

```bash
cat > ~/Library/LaunchAgents/com.paper-trader.daily.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.paper-trader.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/chayanrellan/Desktop/paper-trader/code/run_daily.sh</string>
    <string>--execute</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/chayanrellan/Desktop/paper-trader/state/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/chayanrellan/Desktop/paper-trader/state/launchd.log</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.paper-trader.daily.plist
```

Notes for Option C:
- The hour (`10`) is **your Mac's local time** — adjust it so it lands inside
  US market hours (9:30am–4:00pm New York time). The bot checks the market
  clock anyway, so a wrong hour just means "no trades", never a bad trade.
- It also runs on holidays/weekends if you don't add weekday logic — that's
  fine, the bot sees the market is closed and does nothing.
- If the Mac is asleep at 10:00, the job fires when it next wakes up — but if
  it's powered off (or wakes after 4pm), that day is missed. That's the
  weakness of this option, and why Option A is recommended.
- To stop it: `launchctl unload ~/Library/LaunchAgents/com.paper-trader.daily.plist`
- To check the last run: `cat state/launchd.log`

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
- OR loses more than 8% from when we bought it (cutting losses early —
  this number is a setting the bot can tune as it learns, see "How the Bot Learns")

### The Market Mood Check (Regime Filter)

Before buying anything, the bot checks the overall market's health: is SPY
above its own 200-day average? If not, the whole market is in a downtrend,
and buying "strong" stocks in a falling market is how accounts bleed. In that
case the bot goes **defensive**: no new buys, but it still watches and sells
your existing positions by the normal rules.

### How the Bot Learns

After every run, the bot does homework (`code/learn.py`):

1. **Grades every finished trade.** When a buy and its later sell have both
   really filled, that's one finished trade. It records the profit or loss,
   how long it was held, and which rule ended it — in `state/trade_reviews.jsonl`.
2. **Looks for patterns** in its last 20 finished trades: Is it losing more
   often than winning? Are the losses bigger than the wins?
3. **Adjusts at most ONE setting by ONE small step** — for example, "require
   stocks to beat SPY by 2.5% instead of 2%" — and writes down exactly why in
   `state/strategy_changes.jsonl`. You can read its reasoning anytime.

Three rules keep the learning honest:

- **It needs evidence.** No changes until at least 10 finished trades.
  (Changing strategy after 2 trades is superstition, not learning.)
- **It moves slowly.** One setting, one small step, per day at most.
- **It has hard walls.** The settings live in `state/strategy_params.json`,
  and the trading code clamps them to fixed safe ranges (for example, the
  stop loss can never be looser than -12% or tighter than -5%). Neither the
  learner nor a typo can make the bot reckless.

Check what it's thinking anytime:

```bash
python3 code/learn.py --dry-run   # shows the analysis, changes nothing
cat state/strategy_changes.jsonl  # every change it ever made, with reasons
```

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

### "Am I beating the market?" (one command)

```bash
python3 code/performance.py
```

Prints a plain-English report card:

```
PERFORMANCE REPORT — as of 2026-06-11 (started 2026-06-02)
  Account value:   $99,432.87 (started at $100,000.00)
  Your return:     -0.57%
  SPY return:      +1.20% over the same period
  Verdict:         You are BEHIND SPY by 1.77%.
  Biggest drop from your peak so far: 0.57%
  Orders: 4 filled, 1 expired/canceled, 0 not yet checked
```

The whole point of this project is to beat SPY (the "just buy the whole
market" option). This command tells you honestly whether you are.

### "Did my orders actually fill?" (one command)

```bash
python3 code/alpaca_client.py --reconcile
```

A limit order can sit all day and expire without buying anything. This command
asks Alpaca what really happened to every order and writes the truth (filled,
expired, canceled, and at what price) into your trade records. The bot also
does this automatically at the start of every morning run.

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
│   ├── constitution.py        ← Safety checks (can't be overridden)
│   └── performance.py         ← Report card: are you beating SPY?
│
├── .github/workflows/
│   └── daily-trade.yml        ← The free cloud schedule (Option A)
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
| `performance.py` | The report card. Compares your returns to SPY |
| `daily-trade.yml` | The alarm clock. Tells GitHub to run the bot every weekday morning |
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

**Not yet — and that's on purpose.** The code has three separate locks that all refuse real money:

1. `alpaca_client.py` creates the connection with `paper=True` hard-coded — the Alpaca library then talks to the paper system no matter what.
2. `_require_paper()` checks that your endpoint setting contains `paper-api.alpaca.markets` and exits if it doesn't.
3. `constitution.py` re-checks the same thing before every single trade.

**The honest path to real money looks like this:**

1. **Run on paper for at least 3–6 months.** Anyone can get lucky for two weeks. Months of data is what tells you something.
2. **Check `python3 code/performance.py` regularly.** If you're not beating SPY on paper, real money would just lose to SPY with extra steps — you'd be better off buying SPY and walking away.
3. **Understand every trade.** Read the daily logs until nothing the bot does surprises you. If you can't explain why it bought something, you're not ready to fund it.
4. **Only then**, if you still want to: the locks are the three places listed above, and you would remove them yourself, deliberately, understanding that you're taking the training wheels off. Start with a small amount you can fully afford to lose. Real trading also brings things paper trading hides: taxes on every sale, real fills that are worse than paper fills, and your own emotions when the number is real.

This README won't walk you through removing the locks — that's a decision you should make slowly, not copy-paste. But everything else (the strategy, the limits, the daily routine, the logs) is exactly what you'd use with real money. That's the point of practicing this way.

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
| Credentials rejected | Env vars set on the routine? | At claude.ai/code/routines, edit the routine's cloud environment and verify `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_ENDPOINT` are set |

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
3. ✅ Add Alpaca env vars to your routine's cloud environment
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
