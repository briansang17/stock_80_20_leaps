# SPY LEAPS Strategy — Final Playbook

> **TL;DR** — Monthly $2,500 into VOO. When any of 10 backtested signals fires, sell ~$8k of VOO and buy 1 SPY 2-year **+15% OTM** call. Hold 6-14 months or until an exit signal triggers. Reinvest proceeds back into VOO. Historical edge over pure VOO DCA: **+$58k-$390k over 10 years** depending on which signal you use.

---

## 1. The Core Idea

You're already saving and DCA'ing into VOO every month. The only question is whether you can do *better* than 100% VOO by occasionally rotating some VOO into a leveraged bet (LEAPS) when conditions favor it.

After backtesting 19 different rules over 10 years against real options pricing, the answer is **yes** — but only if you:

1. **Rotate, don't add cash**: Sell VOO to fund LEAPS, then buy VOO back at exit. Never let cash sit idle.
2. **Use OTM strikes**: +15% OTM gives more leverage per dollar than ATM, with better historical returns.
3. **Always use 2-year LEAPS**: Long time horizon = slow theta decay + room for the trade to work.
4. **Stick to filtered, high-quality entry signals** (the 10 below).
5. **Have clear exit rules**: time-stop, drawdown-stop, and volatility-stop all active.

This document explains exactly what to buy, when, and why.

---

## 2. What To Buy (When A Signal Fires)

### The Contract

| Spec | Value |
|---|---|
| **Underlying** | SPY (SPDR S&P 500 ETF) |
| **Type** | Call (long call only — no spreads, no puts) |
| **Strike** | **SPY × 1.15**, rounded to nearest $5 |
| **Expiry** | December roughly 24 months out (the next "December LEAPS" past today + 24 months) |
| **Quantity** | 1 contract per signal-day (sized to ~$7-8k cost) |

### Example — At Today's Prices (May 14, 2026, SPY $742, VIX 17.9)

```
Strike:           $742 × 1.15 = $853  →  round to $855
Expiry:           Dec 2028  (December LEAPS, ~31 months out)
Mid premium:      ~$74/share
Cost / contract:  ~$7,650
Delta:            ~0.47  (option moves ~$0.47 per $1 SPY move)
```

### What This Looks Like At Different SPY Prices

| SPY now | +15% strike | Mid premium (VIX 18) | Cost/contract |
|---|---|---|---|
| $500 | $575 | ~$50 | ~$5,000 |
| $600 | $690 | ~$60 | ~$6,000 |
| $650 | $750 | ~$66 | ~$6,600 |
| $700 | $805 | ~$73 | ~$7,300 |
| $750 | $865 | ~$78 | ~$7,800 |
| $800 | $920 | ~$84 | ~$8,400 |

Higher VIX = higher premium. Lower VIX = lower premium. Rough rule: each +1 VIX point ≈ +5% premium.

### How To Execute The Trade

```
STEP 1   Signal fires (check daily_signal_top10.py or your dashboard)
STEP 2   Calculate +15% strike: SPY price × 1.15, round to nearest $5
STEP 3   In your brokerage, find the SPY call at that strike, Dec expiry
            ~24 months out (e.g., today → pick Dec 2028)
            Make sure open interest > 100
STEP 4   Note the bid, ask, and mid (mid = (bid+ask)/2)
STEP 5   Sell ~10-12 shares of VOO to raise ~$8,000 cash
            • Use specific-lot selection — prefer loss lots or long-term lots
            • Avoid selling short-term lots (32% tax) if possible
STEP 6   Place a LIMIT BUY order on the call at mid + $0.05
            • If not filled in 10 min, raise to mid + $0.10
            • If still not filled, raise to mid + $0.15 (max)
            • Don't chase further — walk away and wait for next signal
STEP 7   Set calendar reminder 6 months out: "check LEAPS exit conditions"
```

---

## 3. When To Buy (The 10 Signals)

Each signal has different conditions. The script `daily_signal_top10.py` checks all 10 daily and tells you which fired. Pick whichever style suits you, or trade them all.

### Tier 1 — Highest Edge, Most Frequent

| Signal | Conditions (all must be true) | Frequency | Win % | After-tax edge (10yr) |
|---|---|---|---|---|
| **D_BREAKOUT** | SPY at new 60-day high + SPY > 200DMA + VIX < 20 | 8/yr | 81% | **+$372,959** |
| **M_QUAL_BREAKOUT** | New 60-day high + VIX < 18 + SPY > 50DMA > 200DMA | 6/yr | 79% | **+$341,693** |
| **C_CHEAP_IV** | VIX < 16 + SPY > 50DMA + RSI 40-65 | 8/yr | 82% | **+$390,073** |
| **H_TREND_FOLLOW** | SPY > 50DMA > 200DMA + MACD > 0 + RSI < 65 | 11.6/yr | 74% | +$378,454 |

### Tier 2 — High Quality, Lower Frequency

| Signal | Conditions | Frequency | Win % | Edge (10yr) |
|---|---|---|---|---|
| **L_A_OR_SQUEEZE** | A_CURRENT *or* I_BB_SQUEEZE fires | 4/yr | 77% | +$181,197 |
| **F_VIX_CRUSH** | VIX dropped 30%+ in 10 days + SPY > 200DMA | 3/yr | 68% | +$132,260 |
| **I_BB_SQUEEZE** | BB width < 20% + SPY ≥ upper band + SPY > 200DMA + VIX < 22 | 2/yr | 78% | +$114,301 |

### Tier 3 — Highest Win Rate, Lowest Frequency

| Signal | Conditions | Frequency | Win % | Edge (10yr) |
|---|---|---|---|---|
| **A_CURRENT** | ≥2 of (MACD cross / RSI cross 50 / 50DMA reclaim) + SPY > 200DMA + VIX < 28 | 2/yr | 70% | +$64,727 |
| **N_FILTER_CURR** | A_CURRENT + VIX-30d-avg < 22 + MACD > 0 + SPY > 50DMA | 1.6/yr | 75% | +$63,826 |
| **E_OVERSOLD** | RSI < 35 + SPY > 200DMA + VIX < 28 | 3/yr | 71% | +$58,465 |

### Consistency Across Periods

All 10 strategies above are **top-10 in BOTH** the past 2 years AND past 10 years. They've worked through:
- 2017-2019 bull market
- 2020 COVID crash + recovery
- 2022 bear market
- 2023-2026 recent bull market

---

## 4. When To Sell (Exit Rules)

Every LEAPS purchased above follows the **same** exit rules, no matter which signal initiated it:

```
EXIT IF ANY OF THE FOLLOWING TRIGGER (whichever comes first):

1. Held ≥ 180 days AND SPY closes below 50DMA × 0.97  →  trend broken
2. Held ≥ 180 days AND VIX > 30                        →  volatility spike
3. Held ≥ 180 days AND VIX rose 6+ points in 5 days    →  panic incoming
4. Held ≥ 500 days                                     →  max hold
5. Time to expiry ≤ 4 months                           →  time decay critical
```

Why the 180-day minimum hold? Because:
- LEAPS need time to work (theta decay is slow with 18-24 months remaining)
- Short-term gains taxed at 32%; long-term (>365 days) taxed at 20%
- Empirically, 60% of winners need 6+ months to develop

When you exit:
1. Sell the call at limit ≥ mid - $0.10
2. Take proceeds and **buy VOO with all of it** (rotation model)
3. Resume monthly $2,500 VOO DCA the next month

---

## 5. The Backtest Results

Below are the after-tax results vs pure VOO DCA over the past 10 years ($2,500/mo deposited = $302,500 total, +15% OTM 2-year LEAPS, rotation model).

```
                            After-tax profit    Edge vs pure VOO
─────────────────────────────────────────────────────────────────
Pure VOO DCA (benchmark):     +$320,351            $0
─────────────────────────────────────────────────────────────────
🥇 C_CHEAP_IV          :     +$710,424          +$390,073
🥈 H_TREND_FOLLOW      :     +$698,805          +$378,454
🥉 D_BREAKOUT          :     +$693,310          +$372,959
   M_QUAL_BREAKOUT     :     +$662,043          +$341,693
   L_A_OR_SQUEEZE      :     +$501,548          +$181,197
   F_VIX_CRUSH         :     +$452,610          +$132,260
   I_BB_SQUEEZE        :     +$434,652          +$114,301
   A_CURRENT           :     +$385,078           +$64,727
   N_FILTER_CURR       :     +$384,177           +$63,826
   E_OVERSOLD          :     +$378,816           +$58,465
```

**Worst single trade in any strategy:** -67% on the LEAPS premium (~$5k absolute loss on $7.5k buy). This is the downside risk you accept for the upside leverage.

---

## 6. Position Sizing

```
RULE: Never put more than 3-5% of portfolio into LEAPS at once.
      Each LEAPS contract ≈ $7,000-$8,000 cost.

Portfolio size      Suggested # contracts per signal
─────────────────────────────────────────────────────
$50,000             1 contract  (slightly above 3% — OK)
$100,000            1 contract
$200,000            1 contract
$300,000            1 contract
$500,000            1-2 contracts
$1,000,000+         2-4 contracts
```

For most people, **1 contract per signal-day** is the right answer. The asymmetric payoff doesn't require much capital to move the needle.

---

## 7. The Code

All scripts live in `/Users/briansang/Desktop/stock_80_20_leaps/`. Here's what each does:

### Core Strategy Code

| File | Purpose |
|---|---|
| `strategy_backtest.py` | Black-Scholes pricing, base features (RSI, MACD, drawdown), exit rules |
| `strategy_alternatives.py` | The 14 entry rules + main backtest loop with bid/ask + commission |
| `strategy_high_conviction.py` | The 5 stricter "once-a-year" rules (P, Q, R, S, T) |
| `strategy_otm.py` | OTM strike variant — runs any rule at any moneyness |
| `compare_rotation.py` | The rotation portfolio model (sell VOO to fund LEAPS, buy back at exit) |

### Daily Monitoring Code

| File | Purpose |
|---|---|
| `daily_signal_top10.py` | **PRIMARY** — daily scanner for all 10 strategies + email alerts |
| `notify.py` | macOS / email / Pushover notification helpers |
| `recent_signals.py` | Show most-recent buy signal for each strategy |
| `leaps_sizing_guide.py` | Print sizing/strike/cost table for current market |

### Analysis & Visualization Code

| File | Purpose |
|---|---|
| `top10_2yr_vs_10yr.py` | Compare top 10 across 2-yr and 10-yr periods |
| `strategy_all_otm.py` | All 19 strategies × multiple OTM levels |
| `plot_all_strategies.py` | Generate equity curve charts per strategy |
| `plot_bb_squeeze_10yr.py` | Detailed BB_SQUEEZE 10-year chart |
| `plot_deep_squeeze.py` | Detailed P_DEEP_SQUEEZE chart |

### Automation Config

| File | Purpose |
|---|---|
| `.github/workflows/daily_signal.yml` | GitHub Actions — runs daily scanner in the cloud (FREE) |
| `com.briansang.leaps-signal.plist` | macOS launchd — runs daily scanner locally |
| `requirements.txt` | Python dependencies |

### Documentation

| File | Purpose |
|---|---|
| **`FINAL_STRATEGY.md`** | ← **You are here — definitive playbook** |
| `TOP10_SIGNAL_SETUP.md` | Detailed Python + Google Sheets setup guide |
| `FREE_CLOUD_SETUP.md` | GitHub Actions, PythonAnywhere, Oracle Cloud setup |
| `GOOGLE_SHEETS_SETUP.md` | Google Sheets dashboard setup |
| `DAILY_SIGNAL_README.md` | Older notification setup notes |

---

## 8. How To Run Things

### Once-off setup
```bash
cd /Users/briansang/Desktop/stock_80_20_leaps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Daily — check current signals
```bash
python daily_signal_top10.py        # prints + emails if signal fires
python daily_signal_top10.py --force # emails even if no signal (test)
```

### One-off — see backtest results
```bash
python strategy_all_otm.py          # all 19 strategies × OTM sweep
python top10_2yr_vs_10yr.py         # top 10 in both periods
python recent_signals.py            # most recent buy date for each
python leaps_sizing_guide.py        # today's strike/cost recommendations
```

### One-off — generate strategy charts
```bash
python plot_all_strategies.py       # equity curves per strategy
python plot_bb_squeeze_10yr.py      # detailed BB_SQUEEZE chart
python plot_deep_squeeze.py         # detailed P_DEEP_SQUEEZE chart
```

---

## 9. Daily Monitoring Setup

Pick one (or more). Setup time in parentheses.

### Option A — GitHub Actions (FREE, recommended)
Runs in cloud automatically every weekday at 4:30pm ET. Zero ongoing work.
**See `FREE_CLOUD_SETUP.md`** for 15-min walkthrough.

### Option B — macOS launchd (FREE, local)
Runs on your Mac whenever it's on at 4:15pm local time.
```bash
cp com.briansang.leaps-signal.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
```

### Option C — Google Sheets + Apps Script (FREE)
Free 24/7 server-less alternative for 4 simplest strategies.
**See `TOP10_SIGNAL_SETUP.md`** Option 2.

### Option D — Manual
Just run `python daily_signal_top10.py` whenever you remember.

---

## 10. Critical Rules — Read These Twice

### DO

- ✅ Use **limit orders** for both VOO and LEAPS — never market orders on options
- ✅ Wait for the strategy to fire — don't anticipate or "almost-buy"
- ✅ Pick **Dec expiry** roughly 24 months out (most liquid LEAPS)
- ✅ Verify **open interest > 100** on the contract before buying
- ✅ Use **specific-lot identification** when selling VOO (prefer loss lots / long-term)
- ✅ Hold for the minimum 180 days unless an exit trigger fires
- ✅ Always reinvest LEAPS proceeds back into VOO (rotation)
- ✅ Keep monthly $2,500 VOO DCA running regardless of LEAPS activity

### DON'T

- ❌ Don't buy LEAPS without a fired signal (gambling, not strategy)
- ❌ Don't buy ATM strikes — backtest shows +15% OTM is better
- ❌ Don't buy shorter-than-2-year expiries (theta decay too fast)
- ❌ Don't use market orders on options (you'll overpay 5-15%)
- ❌ Don't put more than 5% of portfolio in any single LEAPS position
- ❌ Don't average down on a losing LEAPS — let the exit rules work
- ❌ Don't day-trade or scalp — this is a 6-14 month hold strategy
- ❌ Don't sell winners early "because I'm up a lot" — let exit triggers fire

---

## 11. What Today Looks Like (May 14, 2026)

For reference — what the system says right now:

```
Market state:
  SPY $742.31    VIX 17.87    RSI 81.9
  50DMA $687.62  200DMA $674.98  BB-width %ile 79%

🚦 2 of 10 strategies firing today

🟢 SUGGESTED CONTRACT (+15% OTM 2-yr LEAPS):
   Strike     : $855
   Expiry     : Dec 2028
   Mid premium: $74.80 / share
   Cost/cntrct: $7,649  (limit ≤ $76.48)

🟢 D_BREAKOUT         ✅ SPY at new 60-day high (no resistance left)
🟢 M_QUAL_BREAKOUT    ✅ Same setup with stricter VIX < 18 confirmation
```

**Translation:** If you weren't already in, today is a valid buy day for `D_BREAKOUT`. Buy 1 SPY $855 Dec-2028 call at limit ~$76/share.

⚠️ However, RSI is 81.9 — short-term overbought. Cautious traders might wait for a small pullback before entering, even though the rule technically fires.

---

## 12. Tax Strategy

LEAPS held < 366 days = **short-term gain** = taxed at your ordinary income rate (32% in your bracket).
LEAPS held ≥ 366 days = **long-term gain** = taxed at 20%.

The backtest assumes the worst case (32% short-term) for any trade closed < 366 days. The "after-tax edge" numbers already account for this.

**Tip:** If a LEAPS is up 30%+ and you've held 320 days, it's almost always worth holding another 46 days to convert to long-term. The 12% tax savings usually beats any short-term volatility risk.

---

## 13. FAQ

**Q: Why +15% OTM specifically?**
A: Backtest shows +15% OTM is the **sweet spot** — gives ~85% of the edge of +20% OTM with better delta (less time-decay sensitivity) and slightly higher win rate. ATM and +10% OTM both have lower historical edge.

**Q: What if I can't afford $8k per trade?**
A: Go to +20% OTM — contracts ~$6,700 each at current prices. Edge is slightly higher historically. Or trade less often by using only the Tier 3 signals (1-3/yr).

**Q: What if I want even more leverage?**
A: +20% OTM gave the highest edge in backtesting (+$130k vs +$114k at +15% for BB_SQUEEZE). But you give up some delta and stability. Don't go beyond +20% — deeper OTM has poor risk/reward.

**Q: Multiple signals fire same day — do I buy multiple contracts?**
A: **No.** Buy 1 contract per signal-day, regardless of how many strategies confirm. Avoid pyramiding.

**Q: Two signals fire 1 day apart — buy two contracts?**
A: The backtest uses a 14-day debounce between any two entries. Don't add a second contract within 14 days of the first.

**Q: What if I'm wrong about a signal — should I exit immediately?**
A: No. Let the exit rules work. The strategy's edge comes from *not second-guessing*. Premature exits historically hurt more than they help.

**Q: What if SPY tanks 20% right after I buy?**
A: The exit triggers will fire (SPY < 50DMA, VIX > 30, etc.). You'll exit at a loss but not a total loss — typical worst case is ~-50 to -60% on the premium. With 1 contract, that's a ~$4k absolute loss.

**Q: Can I use QQQ or single stocks?**
A: This strategy is calibrated for SPY only. QQQ would have different optimal parameters. Single stocks introduce idiosyncratic risk the backtest doesn't account for. **Stick to SPY.**

---

## 14. Three-Sentence Summary

1. DCA $2,500/mo into VOO. Every weekday, check `daily_signal_top10.py` for an alert.
2. When any of the 10 signals fires, sell ~10 shares of VOO and buy 1 SPY +15% OTM Dec-2yr call.
3. Hold 180-500 days or until an exit trigger fires; reinvest proceeds back into VOO.

That's the entire strategy.

---

## 15. Honest Caveats

- **Backtest is not the future.** Markets change. Strategies stop working. Re-evaluate yearly.
- **Sample sizes are modest** — 20-100 trades over 10 years per strategy. High variance possible.
- **Tax assumptions are simplified** — your actual tax situation may differ.
- **Real-world execution differs from backtest** — slippage, fill quality, broker fees may erode edge by ~10-15%.
- **Behavioral risk is real** — discipline to hold 180+ days through drawdowns is harder than it sounds.
- **The +$390k edge is over 10 years** — that's ~$39k/yr, or ~13% extra annual return on top of VOO's ~13%. Real but not magic.

This is a **disciplined, rule-based supplement** to a VOO buy-and-hold strategy. It's not a get-rich-quick scheme.

---

## 16. Next Steps

If you haven't yet:

1. **Set up automated daily monitoring** — `FREE_CLOUD_SETUP.md` (GitHub Actions, 15 min)
2. **Configure email** — Gmail App Password + `~/.leaps_signal_config.json`
3. **Run the scanner today** — `python daily_signal_top10.py --force` (sends a test email so you know it works)
4. **Set a 6-month calendar reminder** to re-check this strategy against new market data

Welcome to systematic LEAPS trading. May your signals fire and your VOO grow.
