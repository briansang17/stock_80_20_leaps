# `roll_signals/` — when to roll your SPY LEAPS

Mirror of `sell_signals/` but for **rollovers**: closing an existing LEAPS
contract and immediately opening a fresh, further-dated / re-struck one.

A rollover is **not** a sell:

| Action | Capital flow | Why |
|---|---|---|
| Sell  | LEAPS → cash  | Exit the bet entirely (trend broke, vol spiked, etc.) |
| Roll  | Old LEAPS → new LEAPS | Keep the bet on, but lock gain / reset delta / extend duration |

The 10 candidate rules look at **both** market state **and** the position
(DTE, delta, P&L) because rollovers are position-aware.

---

## File map

```
roll_signals/
├── roll_rules.py          # 10 candidate rollover rules + Black-Scholes helpers
├── roll_backtest.py       # vs NEVER_ROLL baseline + CSV of results
├── daily_roll_check.py    # daily scanner — takes positions.json, prints verdicts
├── positions.example.json # template for the positions.json file
└── README.md              # this file
```

---

## The 10 candidate rules

| Key | One-liner |
|---|---|
| **R1_PROFIT_50**     | Up ≥ +50%  → lock gain, reset delta |
| **R2_PROFIT_100**    | Up ≥ +100% → take the double and redeploy |
| **R3_CAL_365**       | DTE < 365  → theta starts to bite below 1 year |
| **R4_CAL_180**       | DTE < 180  → theta hot zone, don't get stuck |
| **R5_DELTA_HIGH**    | Δ > 0.85  → deep ITM, paying mostly intrinsic |
| **R6_DELTA_LOW**     | Δ < 0.30  → deep OTM, paying for hope |
| **R7_PROFIT_AND_CAL**| +30% **and** DTE < 365 (capture gain + extend) |
| **R8_VIX_CHEAP**     | VIX < 16 **and** +20% (replace old leg with cheap new one) |
| **R9_PROFIT_OR_CAL** | +50% **or** DTE < 365 (whichever first) |
| **R10_QUARTERLY**    | Roll every 180d regardless (calendar ladder) |

`RECOMMENDED_KEYS_DEFAULT` in `roll_rules.py` is what the daily scanner
flags as 🔥 PRIORITY (start = `R7_PROFIT_AND_CAL`, `R3_CAL_365`).  Run
the back-test and re-pick if you want.

---

## Daily scanner — when to roll *your* open lots

```bash
cd /Users/briansang/Desktop/stock_80_20_leaps

# 1) copy the template and edit it to match your open lots
cp roll_signals/positions.example.json roll_signals/positions.json
# (edit roll_signals/positions.json — see header of daily_roll_check.py
#  for the JSON schema)

# 2) run the scanner
python roll_signals/daily_roll_check.py --positions roll_signals/positions.json

# alternate flags
python roll_signals/daily_roll_check.py --positions roll_signals/positions.json --force
python roll_signals/daily_roll_check.py --positions roll_signals/positions.json --quiet
```

Per-position output:

```
  🔄 Lot opened on Apr 8 2025  →  VERDICT: ROLL
     One of the high-priority roll rules is firing.
     Lot:  $540 strike  •  opened 2025-04-08  •  expires 2027-04-08
     Today: mark $112.50/sh  •  P&L +49.2%  •  Δ 0.78  •  354d to expiry
     🔥 Priority firing: R7_PROFIT_AND_CAL
     Rules firing:
       🔄 R3_CAL_365     1 year left: 2-year LEAPS theta starts to bite below 365 DTE
       🔄 R7_PROFIT_AND_CAL Up +30% AND <1y left: capture gain + extend duration
```

---

## Back-test — pick the rule that beats `NEVER_ROLL`

```bash
# Full 10-year sweep (slow — ~1 minute):
python roll_signals/roll_backtest.py

# Faster 5-year sweep:
python roll_signals/roll_backtest.py --years 5

# Test only specific rules:
python roll_signals/roll_backtest.py --rules R1_PROFIT_50,R7_PROFIT_AND_CAL

# Bigger / smaller per-entry capital:
python roll_signals/roll_backtest.py --per-lot 10000
```

The back-test:
1. Finds every BUY signal day (debounced 14d, same as production).
2. For each rule, walks the trade forward day by day.  If the rule fires:
   close the current LEAPS at the bid, open a new +15% OTM 2-yr LEAPS
   at the ask using the realised $$, keep going.  Sell-side exit rules
   (SPY below 50DMA, VIX > 35, etc.) **always** override.
3. Prints one row per rule (winrate, # rolls, total NAV, Δ vs baseline)
   and saves `roll_signals/roll_backtest_results.csv`.

Update `roll_signals/roll_rules.py::RECOMMENDED_KEYS_DEFAULT` to point at
the top-performing rules from the table — the daily scanner will then
flag them as PRIORITY.

---

## Wire it into the daily email

`final_leaps/daily_signal_top10.py` already combines BUY + SELL into one
email.  To also include rollover verdicts:

1. Drop a `positions.json` next to the daily scanner (or set
   `ROLL_POSITIONS_PATH` env var to its location).
2. The BUY scanner will call `roll_signals.daily_roll_check.build_position_report(...)`
   for each lot and append `format_email_section(...)` to the email if any
   lot is `ROLL` or `WATCH`.

(That integration is gated on whether `positions.json` exists — if it
doesn't, the email is BUY+SELL only, same as today.)

---

## Sanity checks

* **No rollovers fire in flat noise** — rules are conservative by design.
* **R3_CAL_365 fires roughly once per year per lot** (annual roll cadence).
* **R1_PROFIT_50 is rare** — only on the strongest legs.
* **The back-test never spends more than `--per-lot` per original entry** —
  rolls re-use the realised $$ of the leg they replace.  So a 50%-up roll
  funds the new leg with the *gain plus initial capital*, not extra cash.
