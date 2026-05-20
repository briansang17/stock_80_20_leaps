# SPY LEAPS — Production Buy-Signal System

> Daily scan of 10 backtested strategies → one focused email when it's time to buy a +15 % OTM 2-year SPY call.

This is the **production** code path: every script, data file, doc, and chart that the daily 5pm-ET email depends on lives in this folder. Everything outside `final_leaps/` (root-level `plot_*.py`, `backtest.py`, etc.) is exploratory and not on the email path.

## What the email tells you

A concise daily summary, ordered for fastest decision-making:

| Section | What it tells you |
|---|---|
| **Verdict** | `🔥 HIGH CONVICTION BUY` (≥3 strategies agree), `🟢 BUY`, or `⚪️ NO ACTION` |
| **Market snapshot** | SPY, VIX, RSI, 50/200DMA, drawdown — all on one line |
| **Suggested contract** | Strike, expiry, mid premium, cost/contract, limit price |
| **Execution checklist** | 5 steps for tomorrow morning's order |
| **Why** | Each firing strategy with win-rate, 10-yr $ edge, and the exact conditions that passed |
| **Strategy ranking** | All 10 strategies sorted by historical $ edge — the firing ones are flagged 🟢 |
| **Recent 🔥 HC days** | Last 5 high-conviction days from the past 5-year scan (the same logic that flagged the **April 8, 2026** buy) |

A full per-condition breakdown of every non-firing strategy *plus* the entire 5-year HC history is also produced — but only on the console (`--force` to see it without sending an email).

## The 10 strategies — ranked best to worst by historical $$ edge

| # | Key | Win % | 10-yr after-tax edge | Fires/yr | Idea |
|---:|:---|---:|---:|---:|:---|
| 1 | `A_CHEAP_IV` | 82 % | +$390 073 | 8.0 | VIX < 16 + RSI 40–65 + uptrend |
| 2 | `B_TREND_FOLLOW` | 74 % | +$378 454 | 11.6 | 50 > 200 DMA + MACD > 0 + RSI < 65 |
| 3 | `C_BREAKOUT` | 81 % | +$372 959 | 8.1 | New 60-day high + VIX < 20 |
| 4 | `D_QUAL_BREAKOUT` | 79 % | +$341 693 | 5.8 | Breakout + VIX < 18 + 50 > 200 DMA |
| 5 | `E_A_OR_SQUEEZE` | 77 % | +$181 197 | 4.3 | `H_CURRENT` **OR** `G_BB_SQUEEZE` fires |
| 6 | `F_VIX_CRUSH` | 68 % | +$132 260 | 2.8 | VIX dropped ≥30 % in 10 days |
| 7 | `G_BB_SQUEEZE` | 78 % | +$114 301 | 2.3 | BB-width < 20th %ile + upper-band break |
| 8 | `H_CURRENT` | 70 % | +$64 727 | 2.3 | 2-of-3 momentum signals + filters |
| 9 | `I_FILTER_CURR` | 75 % | +$63 826 | 1.6 | `H_CURRENT` + strict VIX/MACD filters |
| 10 | `J_OVERSOLD` | 71 % | +$58 465 | 2.8 | RSI < 35 in established uptrend |

Prefix letter = rank: `A_` is the best historical strategy, `J_` is the worst. Numbers are from `FINAL_STRATEGY.md` (10-yr SPY+VIX backtest, +15 % OTM 2-yr LEAPS, $2 500/mo VOO DCA + rotation).

**The 3+ confluence rule.** Days where ≥3 of the 10 strategies fire same-day average **+44 % per LEAPS trade with an 88 % win rate** across the 10-yr backtest. The April 8 2026 buy (`E_A_OR_SQUEEZE` + `F_VIX_CRUSH` + `H_CURRENT`) is the most recent example.

## File map

### Production code (the daily-email path)

| File | Purpose |
|---|---|
| **`daily_signal_top10.py`** | Daily scanner. Fetches SPY/VIX, runs all 10 rules, prints full report, sends concise email if any strategy fires |
| `notify.py` | macOS / SMTP / Pushover notification helpers |
| `strategy_alternatives.py` | The 10 entry rules (`rule_A_current`, `rule_C_cheap_iv`, …) |
| `strategy_backtest.py` | Black-Scholes pricing + features (RSI, MACD, BB, 60-day high, vix_crush, …) |
| `strategy_fresh_capital.py` | `FreshLot` dataclass + fresh-capital backtest harness |
| `strategy_otm.py` | OTM-strike variant of the backtest (used by `recent_signals.py`) |
| `requirements.txt` | Python deps (yfinance, pandas, numpy, scipy, tqdm) |

### Analysis helpers (run ad-hoc, not on the email path)

| File | Purpose |
|---|---|
| `recent_signals.py` | "Most recent buy" + open-trade MTM for each of the 10 strategies |
| `leaps_sizing_guide.py` | Print today's +OTM strike / cost / sizing table |
| `simulate_email_smart.py` | Replay last N years and show every email the system would have sent |
| `simulate_email_history.py` | Per-month / per-week / per-strategy fire histograms |
| `top10_2yr_vs_10yr.py` | Compare the 10 strategies' 2-yr vs 10-yr edge |
| `plot_high_conviction_returns.py` | Back-tests "buy +15% OTM LEAPS on every HC day" — saves 3-panel chart + per-trade CSV |

### Data, results, docs, charts

| Path | Contents |
|---|---|
| `data_cache/` | Cached SPY+VIX CSVs (10-yr `term_structure.csv` is the canonical one) |
| `results/` | Auto-generated daily log + launchd stdout/stderr |
| `graphs/` | Key reference visualisations (equity curves, BB squeeze, +15 % OTM 5-yr) |
| `FINAL_STRATEGY.md` | The full strategy playbook (what / when / how / why) |
| `TOP10_SIGNAL_SETUP.md` | Python + Google Sheets setup deep-dive |
| `FREE_CLOUD_SETUP.md` | GitHub Actions / PythonAnywhere / Oracle Cloud setup |
| `GOOGLE_SHEETS_SETUP.md` | Spreadsheet dashboard setup |
| `DAILY_SIGNAL_README.md` | Earlier notification-setup notes |

## How to run

All commands run from the repo root (`stock_80_20_leaps/`):

```bash
# Daily check (what the cron actually runs).  Default scan = past 5 years.
python final_leaps/daily_signal_top10.py

# Force the email (handy after a config change to confirm delivery)
python final_leaps/daily_signal_top10.py --force

# Console only — no email
python final_leaps/daily_signal_top10.py --quiet

# Shorter scan if you're impatient (1 year of history instead of 5)
python final_leaps/daily_signal_top10.py --scan 252

# One-off analyses
python final_leaps/recent_signals.py        # most recent fire per strategy
python final_leaps/leaps_sizing_guide.py    # today's +OTM strike table
python final_leaps/simulate_email_smart.py --years 2     # replay 2 yrs of emails
python final_leaps/plot_high_conviction_returns.py        # ⭐ HC-day LEAPS back-test chart
python final_leaps/plot_high_conviction_returns.py --years 5   # 5-year window
```

## Long-history validation (20–30 years of real data)

The canonical `data_cache/term_structure.csv` only covers 2016–2026. To stress-test the rules against the dot-com bust, GFC, COVID, and the 2022 bear, three additional scripts fetch and replay real SPY+VIX data back to **1993** (~33 years).

| File | Purpose |
|---|---|
| `config_long_history.yaml` | All parameters in one place (date range, OTM %, exit rules, tax rates, period definitions) |
| `fetch_long_history.py` | Downloads SPY, ^VIX, ^VIX3M, ^VIX6M from Yahoo Finance and writes `data_cache/term_structure_long.csv` (same schema as the 10-yr file) |
| `backtest_long_history.py` | Runs all 10 strategies over the full history; reports per-decade and per-crisis after-tax edge vs pure VOO DCA |
| `plot_long_history.py` | 33-year equity-curve PNGs per strategy (with crisis shading + edge sub-panel) |

```bash
cd final_leaps

# 1. Pull 33 years of real data (one time, ~30s)
python fetch_long_history.py

# 2. Run the backtest over every period in the config
python backtest_long_history.py

# 3. Plot equity curves (one PNG per strategy + optional combined chart)
python plot_long_history.py --combined

# Common tweaks
python backtest_long_history.py --otm 0.20                          # 20% OTM
python backtest_long_history.py --strategies C_CHEAP_IV,D_BREAKOUT  # just these two
python backtest_long_history.py --periods full,crisis_gfc,covid_2020
python fetch_long_history.py --start 2000-01-01                     # shorter window
```

Caveats specific to the pre-2007 era:
- `^VIX3M` and `^VIX6M` didn't exist before Sep 2007 / Aug 2008. The fetcher proxies 1-year IV from `^VIX` using a multiplier+offset calibrated on the 2016–2026 overlap (defaults in the YAML). This slightly overpays for LEAPS in low-vol regimes — a conservative bias for backtesting.
- LEAPS bid-ask spreads were wider in the 90s/00s than today; the fetcher widens the modelled spread when VIX > 18 to approximate this.

## Daily automation

Two free options, pick one (or both). End result: **at most one signal email per ET day**, sent between **5–9 pm ET** when the usual BUY/SELL criteria pass (not a blank digest every day).

### A. GitHub Actions (recommended — runs in the cloud, zero ongoing work)

`.github/workflows/daily_signal.yml` is already wired to:

1. Schedule on `cron: '0 21 * * *'` and `'0 22 * * *'` (covers both EDT and EST, every day).
2. **Delay-tolerant guard:** runs if the job actually starts between **5pm and 9pm ET** (GitHub often starts scheduled jobs 1–3 hours late).
3. **Once per ET day:** cache records the date only after a **successful signal email** (not when the scan finds nothing to send).
4. Install deps from `final_leaps/requirements.txt`.
5. Run `python final_leaps/daily_signal_top10.py` (scheduled: signal rules only). **Run workflow** in Actions defaults to `--force` so you can test SMTP anytime; that test does not count as “already sent today” for the evening cron.
6. Upload `final_leaps/results/daily_top10_log.csv` as an artifact (90-day retention).

Add `SMTP_USER`, `SMTP_PASS`, `SMTP_TO`, `SMTP_HOST`, `SMTP_PORT` to repo *Secrets → Actions* (see `FREE_CLOUD_SETUP.md`). Done.

### B. macOS launchd (runs on your Mac whenever it's on)

The plist at the repo root already points at this folder:

```bash
cp ../com.briansang.leaps-signal.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
```

## What I changed when promoting to `final_leaps/`

- Strategies are now **ranked A → J by 10-yr $$ edge** so the prefix matches rank (`A_CHEAP_IV` is #1, `J_OVERSOLD` is #10).
- `daily_signal_top10.py` now always emits a **strategy ranking table** + a **5-yr historical scan** (`--scan 1260` by default, can be tuned with `--scan N`). The scan is what flagged the April 8 2026 confluence — see it for yourself.
- The **email body** is now concise (verdict + snapshot + action + reasons + ranking + recent HC days) — full per-condition breakdowns stay on the console only.
- `strategy_backtest.load_data` defaults to a path resolved next to the file, so the scripts work from any CWD.
- Sell-side scanner (`sell_signals/daily_sell_check.py`) auto-finds these modules via `sys.path`.

## Honest caveats

- Real-world execution diverges from backtest by ~10–15 % due to slippage and fills.
- The 10-yr backtest is a 2016–2026 sample; markets change. Re-evaluate annually.
- This is a **rule-based supplement to VOO DCA**, not a get-rich scheme. The whole point is to harvest a small statistical edge with discipline.
