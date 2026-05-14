# SPY LEAPS — Repo Map

A research repo for a tactical SPY-LEAPS strategy that supplements monthly VOO DCA. The **production daily-email path** lives in [`final_leaps/`](final_leaps/README.md); everything else is exploratory / historical.

## Quick start — "send me daily buy emails"

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r final_leaps/requirements.txt

# Manual run (prints full diagnostics + sends one focused email if any
# of the 10 strategies fires today).  Default scan = past 5 years.
python final_leaps/daily_signal_top10.py --force
```

Automate it for free with **GitHub Actions** (already wired in `.github/workflows/daily_signal.yml`) or **macOS launchd** (`com.briansang.leaps-signal.plist`). Setup details in [`final_leaps/FREE_CLOUD_SETUP.md`](final_leaps/FREE_CLOUD_SETUP.md).

## Repo layout

```
stock_80_20_leaps/
├── final_leaps/                  ← ⭐ everything on the daily-email path
│   ├── README.md                   detailed user guide
│   ├── daily_signal_top10.py       primary scanner
│   ├── notify.py                   email / push notifications
│   ├── strategy_alternatives.py    the 10 entry rules
│   ├── strategy_backtest.py        BS pricing + features + load_data
│   ├── strategy_fresh_capital.py   FreshLot + capital-deployment harness
│   ├── strategy_otm.py             OTM-strike variant
│   ├── recent_signals.py           most-recent fire per strategy
│   ├── leaps_sizing_guide.py       today's strike / cost table
│   ├── simulate_email_smart.py     replay past N years of emails
│   ├── simulate_email_history.py   per-month fire histograms
│   ├── top10_2yr_vs_10yr.py        2-yr vs 10-yr edge comparison
│   ├── data_cache/                 cached SPY+VIX (canonical:term_structure.csv)
│   ├── results/                    daily logs (auto-generated)
│   ├── graphs/                     key reference visualisations
│   ├── FINAL_STRATEGY.md           the playbook (what/when/why)
│   └── *.md                        setup guides (cloud, sheets, etc.)
│
├── sell_signals/                  exit-rule scanner + backtest
│   ├── daily_sell_check.py         daily sell-side scanner
│   ├── sell_backtest.py            tested 10 sell rules on 145 historical entries
│   └── sell_rules.py               rule definitions
│
├── .github/workflows/daily_signal.yml   GitHub Action: BUY + SELL at 5pm ET
├── com.briansang.leaps-signal.plist    launchd: same job on your Mac
│
├── backtest.py                    legacy: 80/20 VOO + LEAPS sleeve simulator
├── config.yaml                    config for backtest.py
├── plot_*.py                      exploratory chart scripts (not on email path)
├── strategy_all_otm.py            multi-OTM sweep (research)
├── strategy_high_conviction.py    P/Q/R/S/T high-conviction variants (research)
├── compare_*.py                   research comparisons
├── walk_forward*.py               walk-forward validation (research)
├── HONEST_FINAL_REPORT.md         older summary doc
└── results/                       legacy / exploratory outputs
```

## What `final_leaps/` does

- Pulls SPY + VIX from Yahoo Finance (≈ 5-yr buffer so the historical scan works).
- Computes RSI / MACD / Bollinger Bands / 60-day high / VIX-crush / 200-DMA features.
- Runs all 10 backtested strategies on today's market state.
- Scans the **past 5 years** for HIGH-CONVICTION days (≥ 3 strategies agreeing same day — historically +44 %/trade, 88 % win rate).
- Sends one concise email per market day **only when a fresh signal fires**.

The full strategy logic and after-tax expectations are documented in [`final_leaps/FINAL_STRATEGY.md`](final_leaps/FINAL_STRATEGY.md).

## What `backtest.py` (root) is

The **original** 80/20 VOO + LEAPS sleeve simulator (regime-based, ATM strikes, VIX-as-IV-proxy). Predates the top-10 strategy work; kept for reference. Not on the email path.

```bash
python backtest.py --config config.yaml
```

## Don't see what you need?

Read [`final_leaps/README.md`](final_leaps/README.md) — it has the full strategy ranking table, file map, run commands, and automation setup.
