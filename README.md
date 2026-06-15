# Paper Trader 📈

**An automated stock-trading bot that invests with fake money.**

Every weekday morning it builds and maintains a diversified portfolio — **75% hand-picked stocks + 25% steadying ETFs** — following fixed rules, and places the orders for you. All trades use **paper (fake) money only**. Nothing here is real money or financial advice.

> 📖 There's also a friendly, illustrated docs site: open **`docs/index.html`** in any browser.

---

## What This Does (In Plain English)

Every weekday morning the bot:

1. **Wakes up** — connects to your Alpaca paper account.
2. **Reads the market** — checks the trend of each stock on its watchlist and of the broad market.
3. **Builds a portfolio** — aims for **75% in ~12 diversified hand-picked stocks** and **25% in ETF ballast** (broad market + bonds + gold).
4. **Invests slowly** — buys at most **$50,000 per day**, easing in instead of going all-in.
5. **Protects** — sells anything whose trend breaks, and shifts to safe T-bills when the market turns down.
6. **Logs everything** — saves a record and a risk-adjusted report card.

Think of it as a disciplined, emotionless investor that follows the same rules every day. It is **not** a day trader.

---

## Getting Started

### Step 1 — Install Claude Code
- **Web:** https://claude.ai/code · **Desktop:** Mac/Windows app · **CLI:** `npm install -g @anthropic-ai/claude`

### Step 2 — Get a free Alpaca paper account
1. Go to https://app.alpaca.markets and sign up.
2. **Settings → API Keys** → copy your **paper** API key and secret (the word "PAPER" should be in the key name).
3. Alpaca gives every paper account $100,000 of fake money.

### Step 3 — Give the bot your keys

The bot runs in the cloud as a **Claude Code routine**, so your laptop doesn't need to stay on. The routine clones this repo into a fresh sandbox and reads the **environment variables on its cloud environment** (it does **not** read GitHub secrets):

1. Go to **https://claude.ai/code/routines** and open your paper-trader routine.
2. Click the **pencil** (Edit routine) → click the **cloud/environment** name → **Update cloud environment → Environment variables**.
3. Add:
   - `ALPACA_API_KEY` → your paper API key
   - `ALPACA_SECRET_KEY` → your paper secret key
   - `ALPACA_ENDPOINT` → `https://paper-api.alpaca.markets`
   - `LIVE_TRADING_AUTHORIZED` → `false`
   - `RISK_FREE_RATE` → `0.045` (used by the report card)
4. **Save changes.** Use **Run now** to test immediately.

> These are **paper** credentials — no real money — and `constitution.py` refuses any non-paper endpoint anyway.
>
> **Running locally instead?** Copy `.env.example` to `.env` and put the same values there (`.env` is git-ignored).

### Step 4 — Schedule it (pick one)

See **[Run it every morning](#run-it-every-morning)** below.

---

## Run It Every Morning

### Option A — Claude Code Routine (recommended)

A cloud routine runs the bot for you at a fixed time, with no laptop required and no queue delays. In a Claude Code session in this folder:

```text
/schedule every weekday at 10:00am New York time, in the paper-trader repo, run: bash code/run_daily.sh --execute. Paper trading only; Alpaca paper credentials are environment variables on the routine's cloud environment. After trading, commit and push the state/ folder so records persist.
```

Then set your Alpaca keys on the routine's **cloud environment** (Step 3). Manage runs at https://claude.ai/code/routines.

> Because of daylight saving, a fixed UTC schedule lands at 10:00am ET in winter and 11:00am ET in summer — both safely inside market hours (9:30am–4:00pm ET).

### Option B — Your own Mac (`launchd`)

Keep everything on your machine (the laptop must be awake at run time):

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

The hour is **your Mac's local time** — set it inside market hours. The bot checks the market clock anyway, so a wrong hour just means "no trades", never a bad trade. Stop it with `launchctl unload ...`.

---

## How It Works (The Strategy)

The portfolio is split **75% hand-picked stocks / 25% ETF ballast**, built to fit a Robinhood-style agentic **investment** account: equities only, cash account, **no day trading, no options, no margin**.

### The 75% — hand-picked stocks

Around a dozen individual stocks from the watchlist, chosen for strong, steady uptrends — and **diversified on purpose** so it's never a bet on one industry:

1. **Real uptrend** — price above its 50-day *and* 200-day average.
2. **Beating the market** — outperforming SPY over the last 20 days.
3. **Steady beats wild** — ranked by 1/3/6-month gains divided by volatility (risk-adjusted momentum). Calm climbers rank above wild swingers, and calmer names get a larger slice (inverse-volatility weighting).
4. **Hard diversification limits** — **max 2 stocks per sector** (`state/sectors.json`), **≤10% in any one name**, and a **correlation gate** that skips a stock moving almost identically to one already held.

### The 25% — ETF ballast

A steadying anchor: **SPY** (US market), **IEF** (Treasuries), **GLD** (gold). Each follows the same 200-day trend rule — below its average, its money rotates to T-bills instead of riding it down.

### Cash & safety

- **T-bill harbor** — risk-off or idle cash parks in ultra-short T-bill ETFs (SGOV/BIL/USFR…), spread so no single one breaks the 25%-per-holding cap. Safe, and it earns the bill rate.
- **$50,000/day invest cap** — total buy orders per day are capped (in `state/config.json` *and* enforced by `constitution.py`). Selling to reduce risk is **never** capped.
- **No day trading** — it never buys and sells the same ticker on the same day.

### When it sells

1. **Trend break** — a stock closes below its 50-day average → sell.
2. **Market regime** — SPY below its 200-day average → stop picking stocks and exit them to safety.
3. **Volatility brake** — jumpy markets → shrink risk exposure.
4. **Drawdown brake** — account 15% below its peak → rotate everything to T-bills (this **rotates**, it does not freeze the bot).
5. **Overweight trim** — a position grown well past its target gets trimmed back.

### No learning loop

The strategy's parameters are **frozen on purpose** (in `state/config.json`). Tuning a strategy on a few dozen trades fits random noise, not skill. Change settings by hand, with a reason, and re-validate on paper.

### Safety guardrails (never broken)

| Rule | Limit |
|------|-------|
| Max in one stock | 25% of the account |
| Invested per day | $50,000 (buys only) |
| Trades per day / week | 20 / 50 |
| Day trading | Never (no same-day round trips) |
| Order type | LIMIT + DAY only |
| Account | Paper only |

`constitution.py` checks every order and rejects any that breaks a rule.

---

## Checking Your Results

### Risk-adjusted report card

```bash
python3 code/metrics.py
```

Prints Sharpe ratio, max drawdown, volatility, and order fill rate — each next to SPY:

```
RISK-ADJUSTED REPORT — as of 2026-06-15 (started 2026-06-03, 9 days)
  Account value:       $96,500.00   (total return -2.85%)
  Sharpe ratio:        0.41   vs SPY 0.92
  Max drawdown:        -4.54%   vs SPY -1.66%
  Annualized vol:      8.20%   vs SPY 15.10%
  LIMIT fill rate:     94%
  Reminder: judge this on Sharpe / drawdown vs SPY, not on raw monthly return.
```

**Why risk-adjusted?** A diversified, capped strategy aims to deliver a *smoother ride* — better return per unit of risk — not necessarily a bigger raw number every month. Judge it on Sharpe and drawdown, not on whether it beat SPY this week.

### Did my orders fill?

```bash
python3 code/alpaca_client.py --reconcile
```

A LIMIT order can sit all day and expire. This asks Alpaca what really happened and writes the truth into your records. The bot also does this at the start of every run.

### See it live
- **Alpaca dashboard:** https://app.alpaca.markets/paper/dashboard
- **Daily logs:** `state/runs/` (one JSON per run: regime, gates, planned trades, what was submitted)
- **Trade history:** `state/completed_trades/`

---

## Testing & Safety

### Dry run (no orders)
```bash
bash code/run_daily.sh --dry-run
```
Shows the full plan — what it would buy/sell and how much — without placing anything. Always dry-run first.

### Live run (places paper orders)
```bash
bash code/run_daily.sh --execute
```

### Emergency stop
```bash
touch .HALT_TRADING      # stop placing any new orders
rm .HALT_TRADING         # resume
```
`.HALT_TRADING` is a manual, human-only kill switch that freezes the whole book. (The automated drawdown brake is separate — it rotates to T-bills, it does not freeze trading.)

---

## The Files

```
paper-trader/
├── code/
│   ├── run_daily.sh        ← daily driver (what the schedule runs)
│   ├── autopilot.py        ← entry point: wires data in, submits orders
│   ├── engine.py           ← the planner: picks stocks, sizes positions
│   ├── signals.py          ← pure math: trend gates, brakes, momentum
│   ├── metrics.py          ← risk-adjusted report card
│   ├── alpaca_client.py    ← talks to Alpaca (paper), fetches prices
│   ├── constitution.py     ← safety checks (can't be overridden)
│   └── tests/test_strategy.py  ← unit tests for the strategy brain
│
├── state/
│   ├── config.json         ← frozen strategy settings (75/25, $50k cap, limits)
│   ├── watchlist.txt        ← stock candidates (edit this)
│   ├── sectors.json         ← which sector each ticker is in (for the cap)
│   ├── strategy_state.json  ← trend-gate memory between runs
│   ├── runs/                ← one JSON per run (the journal)
│   ├── pending_trades/      ← orders awaiting the constitution check
│   ├── completed_trades/    ← orders actually sent to Alpaca
│   ├── portfolio.json       ← current positions & balance
│   └── portfolio_history.jsonl ← equity over time (for the report card)
│
├── docs/index.html          ← illustrated strategy docs (open in a browser)
├── CLAUDE.md                ← the official rules
├── MIGRATION_CHECKLIST.md   ← what to prove before real money
├── .env.example             ← local credentials template
└── requirements.txt         ← Python dependencies
```

### Customizing the watchlist

Edit `state/watchlist.txt` (one ticker per line) — these are the candidates for the 75% stock sleeve. **Add each ticker to `state/sectors.json` too**, or the per-sector diversification cap can't protect you. The bot only buys names that pass the strategy's filters.

---

## Going to Real Money

Today this runs on **Alpaca paper money**. The honest path to real money is in **`MIGRATION_CHECKLIST.md`** — prove it on paper through at least one full market downturn-and-recovery, with solid risk-adjusted numbers, before risking a dollar.

**Robinhood note:** Robinhood now offers an "agentic" investment account (equities only) that an AI agent connects to via an **MCP connector** — but there is **no official Robinhood stock API**. To trade there you'd connect the connector at https://claude.ai/customize/connectors, fund a dedicated agentic account, and add a Robinhood execution layer (the strategy logic itself is broker-agnostic and wouldn't change). The strategy is already built to comply with those terms (equities only, cash account, no day trading, no options/margin).

---

## Common Questions

**How much money do I need?** None — it's fake. Alpaca gives paper accounts $100,000.

**Is this guaranteed to make money?** No. The market is unpredictable. This is an experiment in disciplined investing, not a money machine.

**Is it a day trader?** No. It holds positions for weeks/months and never buys and sells the same stock the same day. It's an investor.

**Why only LIMIT orders?** A LIMIT order says "buy only at my price or better." Market orders take whatever price is available — risky in fast markets.

**Why 75% individual stocks?** That's the chosen, growth-tilted setup. It can outperform a plain index fund in good times — and fall harder in bad times. The sector/name/correlation caps and trend exits are what keep that risk contained. Want a calmer ride? Lower the caps or the daily invest amount in `state/config.json`.

**What if the market is closed?** The bot checks first and does nothing on holidays/weekends.

**Can I trade real money?** Not yet, on purpose. `alpaca_client.py` is hard-wired to `paper=True`, and both it and `constitution.py` refuse any non-paper endpoint. See `MIGRATION_CHECKLIST.md`.

---

## Troubleshooting

| Problem | Check | Fix |
|---|---|---|
| Bot isn't trading | Does `.HALT_TRADING` exist? | `rm .HALT_TRADING` |
| No trades placed | `--dry-run` to see the plan | Market may be closed, or nothing passed the filters that day |
| Orders not filling | Alpaca dashboard order status | LIMIT orders only fill at your price; they expire otherwise |
| Python errors | Dependencies installed? | `pip install -r requirements.txt` |
| Credentials rejected | Env vars on the routine? | At claude.ai/code/routines, check `ALPACA_API_KEY/SECRET_KEY/ENDPOINT` |
| Deploying too slowly | `daily_invest_cap_usd` in `state/config.json` | Raise the daily cap |

---

## The Rules (from CLAUDE.md)

- **Paper money only** — never real money
- **LIMIT + DAY orders only** — no market orders, options, margin, shorts, or crypto
- **Position limits** — max 25% in one ticker, ≤20 trades/day, ≤50/week, ≤$50k invested/day
- **No day trading** — no same-day round trips
- **Honest logging** — never hide losses or invent data
- **Ask when stuck** — if the rules don't cover something, ask before acting

---

**Made with ❤️ for learning, not for guaranteed profits.** Paper trading is practice. 🎯
