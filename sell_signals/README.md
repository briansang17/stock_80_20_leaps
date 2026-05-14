# SPY LEAPS — When to SELL

Mirror of the buy-side top-10 monitor, focused on **exits**.

## TL;DR — what the backtest revealed

We tested 10 candidate sell rules + the current production exit rules
across **145 historical LEAPS entries** from the buy-side strategies
(2016–2026). The conclusions are counter-intuitive but consistent with
options math:

### Ranked results (10-year backtest, mean P&L per trade)

| Rank | Policy                | Mean P&L | Win Rate | Big-Loss Rate (>20% loss) | Avg Days Held |
|------|----------------------|----------|----------|---------------------------|---------------|
| 1    | **HOLD_ONLY**         | **+54.9%** | 75%      | 20.3%                     | 501           |
| 2    | S5_NEW_60D_LOW       | +42.0%   | **83%**  | **7.6%**                  | 335           |
| 3    | S10_VIX_REGIME       | +40.6%   | 79%      | 11.7%                     | 312           |
| 4    | COMBO_top2 (S5+S10)  | +37.8%   | 77%      | 8.3%                      | 272           |
| 5    | **COMBO_only_extreme** (S5+S1) | **+35.0%** | 82%   | **6.9%**                  | 286           |
| 6    | S2_VIX_PANIC         | +34.5%   | 77%      | 8.3%                      | 256           |
| 7    | S1_VIX_SPIKE         | +31.5%   | 74%      | 15.2%                     | 310           |
| ...  | (other rules)        | +20-31%  | 64-81%   | 7-15%                     | 235-350       |
| 12   | BASELINE_current     | +28.2%   | 76%      | 8.3%                      | 234           |
| 16   | S7_RSI_REVERSE       | +19.9%   | 79%      | 12.0%                     | 243           |

### Two big takeaways

1. **HOLD has the highest mean return.** For 2-year LEAPS in a mostly-bullish
   SPY regime, time in market beats trying to time exits. Options' gamma
   profile means selling early caps upside but gives little downside protection
   once you're up.

2. **The current production exit rules HURT performance.** `BASELINE_current`
   (the 3 rules currently used in the buy-side backtest) returned +28% vs HOLD's
   +55% — exiting too aggressively burns 27 pts of edge per trade.

### The "best practical" answer: COMBO_only_extreme

The recommended sell rule = **S5_NEW_60D_LOW OR S1_VIX_SPIKE** (with safety nets):

- **Gives up 20 pts mean** vs HOLD_ONLY (+35% vs +55%)
- But **cuts big-loss rate from 20.3% to 6.9%** — 13 pts less catastrophic-loss exposure
- **82% win rate** (vs 75% for HOLD)
- Sleeps better — only sells on extreme bearish setups

This is the trade you make when you'd rather give up some upside to avoid
the occasional -82% trade.

## The 10 sell rules

| Key | What it watches for | When it fires |
|-----|---------------------|---------------|
| 🔥 **S1_VIX_SPIKE** | Fear regime | VIX > 30 |
| **S2_VIX_PANIC** | Accelerating fear | VIX 5-day slope > +6 pts |
| **S3_TREND_BREAK** | Medium-term uptrend lost | SPY < 50DMA × 0.97 |
| **S4_DEATH_CROSS** | Long-term trend reversal | 50DMA crosses below 200DMA |
| 🔥 **S5_NEW_60D_LOW** | Bearish breakout | SPY at new 60-day low |
| **S6_MACD_BEAR** | Momentum lost | MACD < 0 AND crossed below in last 5d |
| **S7_RSI_REVERSE** | Overbought reversal | 5d RSI hit >70 then now <55 |
| **S8_DRAWDOWN_10** | Material correction | SPY DD < -10% from ATH |
| **S9_BB_LOWER_BREAK** | Volatility breakdown | SPY < lower Bollinger band |
| **S10_VIX_REGIME** | Vol regime shift | VIX > 1.5× its 30d avg AND VIX > 22 |

🔥 = priority rule (part of recommended COMBO_only_extreme)

## Final sell rules

You always sell when:

1. **Near expiry** — option has <4 months to expiration (safety, always on)
2. **Max hold** — 500 days since entry (safety, always on)
3. **S5_NEW_60D_LOW fires** — SPY made a new 60-day low
4. **S1_VIX_SPIKE fires** — VIX above 30

That's it. Everything else (RSI overbought, MACD bearish cross, BB break,
even SPY breaking below 50DMA) is **NOISE** — backtests show selling on
these signals reduces returns more than it reduces risk.

## How to use the daily scanner

### Local (manual)

```bash
cd ~/Desktop/stock_80_20_leaps
source .venv/bin/activate
python sell_signals/daily_sell_check.py
```

Output shows the current verdict — **HOLD / WATCH / SELL** — plus every
metric with its sell threshold so you can see how close any rule is to
firing.

### With your open positions

Create `positions.json`:

```json
[
  {
    "description": "SPY 855C Dec 2028 — bought 2026-05-14",
    "strike": 855,
    "expiry": "2028-12-19",
    "contracts": 1,
    "cost": 7649
  }
]
```

Then:

```bash
python sell_signals/daily_sell_check.py --positions positions.json
```

The scanner will list your positions in the email and tell you whether
the current verdict applies to them.

### Re-run the backtest yourself

```bash
python sell_signals/sell_backtest.py --years 10
```

Output is saved to `sell_signals/sell_backtest_results.csv` (one row per
simulated trade × policy).

## When does the SELL email actually go out?

The daily sell scanner (`daily_sell_check.py`) emails when verdict is
**SELL** or **WATCH**. **HOLD** days don't trigger a notification (so
you don't get spammed during long bull runs).

Verdict logic:
- **🔴 SELL** → at least one of the 2 priority rules (S5 or S1) is firing
- **🟡 WATCH** → between 1 and 9 non-priority rules firing
- **🟢 HOLD** → 0 sell rules firing

## Files in this folder

| File | What it does |
|------|--------------|
| `sell_rules.py` | The 10 rule definitions + condition explanations |
| `sell_backtest.py` | Tests every rule on 145 historical LEAPS entries |
| `daily_sell_check.py` | Daily scanner that emails verdict + per-rule status |
| `sell_backtest_results.csv` | Generated: every simulated trade × policy |
| `README.md` | This file |

## A note on what NOT to use as sell signals

The backtest evidence is clear — these rules **hurt returns** more than
they help, despite "feeling" like good sell signals:

- ❌ **MACD bear cross alone** (S6) — too jumpy in choppy markets
- ❌ **RSI mean-reversion** (S7) — exits during normal pullbacks in bull
  markets
- ❌ **Bollinger lower breaks** (S9) — gets you out at the wrong moment
- ❌ **3% break below 50DMA** (S3) — too sensitive
- ❌ **Death cross** (S4) — fires AFTER the damage is done

These are common retail sell rules but the data shows they exit too
early. The recommended composite (S5 + S1) only fires on **extreme**
conditions where holding becomes statistically irrational.
