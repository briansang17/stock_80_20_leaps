"""
Most-recent buy signal for each of the top 10 strategies (+15% OTM 2-yr LEAPS).

For each strategy, finds the most recent date it fired and shows:
  - Date, SPY/VIX context
  - The trade that resulted (or is still open)
  - What the trade returned (or current MTM if open)
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import load_data
from strategy_alternatives import (
    extend_features,
    rule_A_current, rule_C_cheap_iv, rule_D_breakout, rule_E_oversold_uptrend,
    rule_F_vix_crush, rule_H_trend_follow, rule_I_bb_squeeze,
    rule_L_squeeze_or_current, rule_M_quality_breakout, rule_N_filter_current,
)
from strategy_otm import run_strategy_otm, round_to_strike

PER_LOT = 10_000
OTM_PCT = 0.15
START, END = "2016-05-13", "2026-05-13"

TOP_10 = [
    ("C_CHEAP_IV",      rule_C_cheap_iv,        "Low VIX (<16) + uptrend + RSI 40-65"),
    ("H_TREND_FOLLOW",  rule_H_trend_follow,    "SPY>50DMA>200DMA + MACD>0 + RSI<65"),
    ("D_BREAKOUT",      rule_D_breakout,        "60-day high + SPY>200DMA + VIX<20"),
    ("M_QUAL_BREAKOUT", rule_M_quality_breakout,"60d high + VIX<18 + SPY>50DMA>200DMA"),
    ("L_A_OR_SQUEEZE",  rule_L_squeeze_or_current,"A_CURRENT OR BB_SQUEEZE fires"),
    ("F_VIX_CRUSH",     rule_F_vix_crush,       "VIX crushed 30%+ in 10d + SPY>200DMA"),
    ("I_BB_SQUEEZE",    rule_I_bb_squeeze,      "BB<20% + breakout + SPY>200DMA + VIX<22"),
    ("A_CURRENT",       rule_A_current,         "2-of-3 momentum + SPY>200DMA + VIX<28"),
    ("N_FILTER_CURR",   rule_N_filter_current,  "A_CURRENT + extra VIX/momentum filters"),
    ("E_OVERSOLD",      rule_E_oversold_uptrend,"RSI<35 + SPY>200DMA + VIX<28"),
]


def main():
    df = load_data()
    feats = extend_features(df)
    today = feats.index[-1]
    spy_today = float(feats["SPY"].iloc[-1])
    vix_today = float(feats["VIX"].iloc[-1])

    print("\n" + "═" * 130)
    print(f"  MOST RECENT BUY SIGNAL — Top 10 Strategies (+15% OTM, 2-yr LEAPS)")
    print(f"  Reference: today is {today.date()}, SPY ${spy_today:,.0f}, VIX {vix_today:.1f}")
    print("═" * 130)

    summary_rows = []
    for label, rule, desc in TOP_10:
        trades = run_strategy_otm(df, rule, PER_LOT, OTM_PCT,
                                  start_date=START, end_date=END)
        if trades.empty:
            print(f"\n  ── {label}: NO FIRES in 10-year window ──")
            continue

        last = trades.iloc[-1]
        days_ago = (today - last["entry_date"]).days
        is_open = last["exit_reason"] == "(still open)"

        # Find days since last fire
        last_entry = last["entry_date"]
        entry_spy = last["entry_spy"]
        entry_vix = last["entry_vix"]
        exit_date = last["exit_date"]
        exit_spy = last["exit_spy"]
        contracts = last["contracts"]
        cost = last["cost"]
        proceeds = last["proceeds"]
        pct = last["pct"] * 100
        held = last["held_days"]
        reason = last["exit_reason"]
        strike = round_to_strike(entry_spy, OTM_PCT)

        # Equivalent SPY-DCA on same money
        spy_equivalent = last["spy_value_at_exit"]
        spy_pct = (spy_equivalent / cost - 1) * 100

        print(f"\n  ── {label} ──  ({desc})")
        print(f"     Total fires (10y)    : {len(trades)} ({len(trades)/10:.1f}/yr)")
        print(f"     MOST RECENT BUY      : {last_entry.date()}  ({days_ago} days ago)")
        print(f"        SPY at buy        : ${entry_spy:>7,.2f}")
        print(f"        VIX at buy        : {entry_vix:>5.1f}")
        print(f"        Strike (+15% OTM) : ${strike:>5,.0f}")
        print(f"        Contracts         : {contracts}")
        print(f"        Cost              : ${cost:>7,.0f}")

        if is_open:
            print(f"     STATUS: 🟢 STILL OPEN")
            print(f"        Current MTM    : ${proceeds:>7,.0f}  ({pct:+.1f}% paper)")
        else:
            verdict = "✅ WIN" if pct > 0 else "❌ LOSS"
            print(f"     STATUS: {verdict}  CLOSED on {exit_date.date()}")
            print(f"        Reason         : {reason}")
            print(f"        Held           : {held} days")
            print(f"        Proceeds       : ${proceeds:>7,.0f}  ({pct:+.1f}%)")
            print(f"        SPY-equivalent : ${spy_equivalent:>7,.0f}  ({spy_pct:+.1f}%)")
            print(f"        LEAPS edge     : {pct - spy_pct:+.1f} percentage points")

        # Did this strategy already exit AND fire again before today's date?
        # Find any signal AFTER last_entry that wasn't captured by debounce
        try:
            sigs_after = []
            for date_ in feats.loc[last_entry + pd.Timedelta(days=15):today].index:
                row = feats.loc[date_]
                try:
                    if bool(rule(row, row)):
                        sigs_after.append(date_)
                except (KeyError, TypeError):
                    pass
            if sigs_after:
                next_sig = sigs_after[0]
                ago = (today - next_sig).days
                print(f"     ⚡ NEXT ELIGIBLE day after exit: {next_sig.date()} ({ago} days ago)")
        except Exception:
            pass

        summary_rows.append({
            "label": label,
            "last_buy": last_entry,
            "days_ago": days_ago,
            "is_open": is_open,
            "pct": pct,
            "entry_spy": entry_spy,
            "entry_vix": entry_vix,
            "strike": strike,
        })

    # ── Summary table ────────────────────────────────────────────────────────
    print("\n" + "═" * 130)
    print(f"  SUMMARY — Most recent buy signal across the top 10 strategies")
    print("═" * 130)
    print(f"\n  {'Strategy':<17}  {'Last buy date':<14}  {'Days ago':>8}  "
          f"{'SPY then':>9}  {'VIX then':>9}  {'+15% Strike':>11}  {'Result':>10}")
    print("  " + "─" * 95)
    for r in sorted(summary_rows, key=lambda x: -x["days_ago"]):
        if r["is_open"]:
            result = f"OPEN {r['pct']:+.1f}%"
        else:
            result = f"{'WIN' if r['pct'] > 0 else 'LOSS'} {r['pct']:+.1f}%"
        print(f"  {r['label']:<17}  {str(r['last_buy'].date()):<14}  "
              f"{r['days_ago']:>5}d   "
              f"${r['entry_spy']:>7,.0f}   "
              f"{r['entry_vix']:>6.1f}    "
              f"${r['strike']:>7,.0f}    "
              f"{result:>10}")

    # ── Which strategies fired MOST recently? ───────────────────────────────
    recent = sorted(summary_rows, key=lambda x: x["days_ago"])[:5]
    print(f"\n  📅 5 MOST RECENTLY FIRED:")
    for r in recent:
        status = "🟢 STILL OPEN" if r["is_open"] else f"closed {r['pct']:+.1f}%"
        print(f"     {r['label']:<17}  {str(r['last_buy'].date())}  "
              f"({r['days_ago']}d ago, {status})")


if __name__ == "__main__":
    main()
