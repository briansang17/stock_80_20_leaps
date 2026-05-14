# Hybrid VOO + Tactical LEAPS Backtester

Evaluates whether a **$100,000 portfolio** split as **$80k passive VOO**
(SPY adjusted close as proxy) + **$20k tactical 2-year ATM SPY LEAPS calls**
priced via **Black-Scholes** and sized by a **VIX-based volatility regime
score** improves risk-adjusted returns vs. pure passive investing.

## What it does

1. Pulls 10 years of `SPY` and `^VIX` from **yfinance** (cached locally).
2. Builds a composite **VolScore (0–100)** from:
   - 252-day VIX percentile (45%)
   - 252-day percentile of 20-day realized SPY vol (35%)
   - VIX 10-day slope component (20%)
3. Builds a **LEAPS conviction score (0–10)** from VIX percentile, VIX slope,
   SPY drawdown band, 200DMA proximity, and new-low penalties.
4. Maps the score to a sleeve allocation:
   - **8–10** → 100% of $20k sleeve in LEAPS
   - **5–7**  → 50%
   - **<5**   → 0% (cash earning the risk-free rate)
5. Buys **2-year ATM SPY calls**, re-prices them daily with Black-Scholes
   using `σ = (VIX/100) * (1 + 0.15 * tanh(slope/5))`, and rolls when 1
   year remains.
6. Outputs equity curves, CAGR/Sharpe/MaxDD, per-cycle LEAPS diagnostics,
   and regime-bucket analysis.

## Files

| File | Purpose |
|---|---|
| `backtest.py` | Main simulator (data, vol model, BS, portfolio, metrics, plots) |
| `config.yaml` | All tunable parameters (capital, weights, thresholds, options, output) |
| `requirements.txt` | Python dependencies |
| `data_cache/` | Auto-created CSV cache of yfinance pulls |
| `results/` | Auto-created — equity curves, metrics JSON, plots, trades |

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (example)

```bash
python backtest.py --config config.yaml
```

That's it. First run downloads ~10 years of SPY + VIX (cached to
`data_cache/`); subsequent runs are instant.

To customize, edit `config.yaml`:

```bash
# Use a longer window
sed -i '' 's/years: 10/years: 15/' config.yaml
python backtest.py --config config.yaml
```

Or run with full-period start/end overrides:

```yaml
# config.yaml
data:
  start_date: "2008-01-01"
  end_date:   "2024-12-31"
```

## Outputs

After a run, `results/` contains:

- `equity_curves.csv` — daily VOO-only / VOO-sleeve / LEAPS-sleeve / Combined values
- `daily_state.csv`   — full per-day state (scores, exposures, marks)
- `leaps_trades.csv`  — every LEAPS cycle (entry, exit, premium, PnL, reason)
- `metrics.json`      — CAGR, Sharpe, max DD, regime diagnostics
- `equity_curves.png` — VOO vs LEAPS sleeve vs Combined
- `vix_vs_allocation.png` — VIX overlaid with LEAPS deployment %
- `vol_score.png`     — Volatility score and LEAPS score over time
- `drawdown.png`      — Drawdown comparison

## Notes / caveats

- This is a **regime-based simulation**, not a real options-chain backtest.
  VIX is used as the implied-vol proxy. Bid/ask spread, skew, term structure
  beyond a single multiplier, early assignment, and dividends-on-options are
  **not** modeled.
- ATM strike is rounded to the nearest dollar. Contracts are allowed to be
  fractional for clean dollar-targeting (this is equivalent to assuming you
  can size the position precisely).
- `SPY` adjusted close is used as a proxy for `VOO` to maximize history
  (VOO began trading in 2010; SPY in 1993).
- Risk-free rate is a flat 3% (configurable). For more rigor, swap in
  `^IRX` (13-week T-bill) historical series.

## Tuning ideas

- Change `weights` in `vol_model` to emphasize realized vs implied vol.
- Tighten `drawdown_min/max` to require deeper crashes before sizing up.
- Lower `roll_remaining_years` to 0.5 to extract more theta-decay-avoidance.
- Add a second sleeve allocation tier (e.g. 25% / 75% / 100%) by extending
  `target_exposure()` and `allocation_rules`.
