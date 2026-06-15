# DTA → Real Money Checklist

The DTA (Diversified Trend Allocator) is the strategy intended for eventual real
money. **Do not fund it until every box below is checked.** This list exists
because the dominant failure mode is human impatience, not the model.

---

## A. Code that must be true (and tested)

- [x] **Drawdown brake rotates, it does not halt.** A −15%-from-high-water-mark
  event SELLS risk sleeves into T-bills; only a manual `.HALT_TRADING` freezes
  the book. (Tested: `test_drawdown_brake_rotates_everything_to_harbor`.)
- [x] **Daily de-risk works.** Any sleeve below its 200-day trend is exited on the
  next run, not just at month-end. (Tested: `test_riskoff_sleeve_is_exited_daily`.)
- [x] **Sells before buys; never over-spend cash; no single holding > 25%.**
  (Tested: `test_sells_before_buys_and_position_cap`, `test_buys_never_exceed_cash`.)
- [x] **No learning loop for DTA.** Parameters are frozen in `dta_config.json`.
- [ ] **Unfilled-order retry.** Re-price a DAY limit that didn't fill and resubmit
  next session; log the fill rate. (Today: orders simply expire; the next daily
  run re-creates any still-needed trade at a fresh limit — acceptable for paper,
  build the explicit retry before real money.)
- [ ] **Missed-run catch-up.** If a scheduled run is skipped, the next run must do
  the protective (sell) leg first.

## B. Paper track record required (minimum 12–24 months)

- [ ] At least **one full regime cycle** actually observed: a trend gate flipping a
  sleeve to risk-off → into T-bills → and back to risk-on later. If the gate
  never fires, the core mechanism is untested.
- [ ] **Sharpe ≥ SPY's** and **MAR (return ÷ max-drawdown) ≥ SPY's**, with
  **materially lower volatility and max drawdown.** (Raw return may trail — that
  is fine and expected.)
- [ ] **Max drawdown stays within the designed ~15–20% band.**
- [ ] **LIMIT fill rate ≥ ~90%**, logged weekly (`dta_metrics.py`).
- [ ] **Zero accidental trade-cap violations** (20/day, 50/week) even on a full
  rotation week.

## C. Operational guardrails before the first real dollar

- [ ] **Account ≥ ~$25–50k**, so whole-share lots and the ±20% drift band work.
- [ ] **Written rule, pre-committed:** multi-year SPY lag in a bull market is
  expected and will NOT trigger abandonment, re-enabling the learning loop, or
  weakening the trend gate. (This is the #1 real-money risk: you.)
- [ ] **Stage the capital:** fund 10–25% first; scale up only after a real
  de-risk → re-risk cycle has completed with real fills.
- [ ] `.HALT_TRADING` documented as a manual kill switch that freezes the whole
  book on purpose (no once-daily system can stop an overnight gap).

---

_Sign-off:_ once all A items are done and B is achieved on paper, DTA is approved
for **staged** real-money deployment.
