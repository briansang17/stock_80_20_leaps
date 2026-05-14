"""
Sell-signal backtest — find the BEST exit rules for SPY LEAPS.

Method
------
1. Collect every historical LEAPS entry from the top-10 BUY strategies
   (de-duplicated by entry date — multiple strategies firing same day = 1 lot).
2. For each entry, simulate what would have happened if we used:
     • The BASELINE exit rules (3 current safety rules + near-expiry / max-hold)
     • Each of the 10 candidate SELL rules in isolation (with safety nets only)
     • HOLD_TO_MAX (never sell unless forced by near-expiry / max-hold)
3. Score each by:
     • Mean P&L %
     • Median P&L %
     • Win rate (% of trades > 0%)
     • % of trades that avoided a >20% loss
     • Average days held
4. Rank and print results.

Usage:
    python sell_signals/sell_backtest.py
    python sell_signals/sell_backtest.py --years 10
"""

from __future__ import annotations
import argparse, sys, os
from pathlib import Path
import pandas as pd
import numpy as np

# Make project root importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from strategy_backtest import (
    load_data, signals_in_window, bs_call,
    RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
)
from strategy_alternatives import (
    extend_features, MIN_HOLD, MAX_HOLD,
    rule_A_current, rule_C_cheap_iv, rule_D_breakout,
    rule_E_oversold_uptrend, rule_F_vix_crush,
    rule_H_trend_follow, rule_I_bb_squeeze,
    rule_L_squeeze_or_current, rule_M_quality_breakout, rule_N_filter_current,
)
from sell_signals.sell_rules import explain_sell, SELL_RULES

PER_LOT = 10_000
ENTRY_DEBOUNCE_DAYS = 14


# ─── Build the universe of historical LEAPS lots ─────────────────────────────

BUY_RULES = [
    ("D_BREAKOUT",      rule_D_breakout),
    ("M_QUAL_BREAKOUT", rule_M_quality_breakout),
    ("F_VIX_CRUSH",     rule_F_vix_crush),
    ("C_CHEAP_IV",      rule_C_cheap_iv),
    ("H_TREND_FOLLOW",  rule_H_trend_follow),
    ("L_A_OR_SQUEEZE",  rule_L_squeeze_or_current),
    ("I_BB_SQUEEZE",    rule_I_bb_squeeze),
    ("A_CURRENT",       rule_A_current),
    ("N_FILTER_CURR",   rule_N_filter_current),
    ("E_OVERSOLD",      rule_E_oversold_uptrend),
]


def collect_entries(feats: pd.DataFrame, sigs: pd.DataFrame) -> list[dict]:
    """One entry per buy-side fire-day (deduped across the 10 rules).

    Applies a 14-day debounce per the buy-side production logic.
    Returns list of {date, spy, vix, sigma, spread, strike, premium, contracts, cost, expiry}.
    """
    entries = []
    last_entry_date = None

    for date, row in feats.iterrows():
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired = False
        for _, rule in BUY_RULES:
            try:
                if bool(rule(row, sigs_row)):
                    fired = True
                    break
            except (KeyError, TypeError):
                pass
        if not fired:
            continue
        if last_entry_date is not None and (date - last_entry_date).days < ENTRY_DEBOUNCE_DAYS:
            continue

        spy = float(row["SPY"])
        sigma = float(row.get("IV1Y_cal", row["VIX"] / 100))
        spread = float(row.get("spread", 0.04))
        if pd.isna(spy) or pd.isna(sigma):
            continue
        strike = round(spy)
        premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
        if premium <= 0:
            continue
        contracts = int(PER_LOT / (premium * 100))
        if contracts < 1:
            continue
        cost = contracts * premium * 100 + contracts * COMMISSION_PER_CONTRACT
        expiry = date + pd.Timedelta(days=int(LEAPS_YEARS * 365.25))

        entries.append({
            "date": date,
            "spy": spy, "vix": float(row["VIX"]),
            "strike": strike, "contracts": contracts,
            "cost": cost, "expiry": expiry,
        })
        last_entry_date = date

    return entries


# ─── Simulate a single exit policy on a list of entries ─────────────────────

def baseline_should_exit(row, held, T_rem):
    """The current production exit logic (3 rules + near-exp/max-hold)."""
    if T_rem <= EXIT_NEAR_EXP:
        return True, "Near expiry"
    if held >= MAX_HOLD:
        return True, "Max hold"
    if held >= MIN_HOLD:
        spy = float(row["SPY"]); vix = float(row["VIX"])
        if spy < row["sma50"] * EXIT_DD_50DMA:
            return True, "SPY broke 50DMA"
        if vix > EXIT_VIX_HIGH:
            return True, "VIX>30"
        if row["vix_slope5"] > EXIT_VIX_SLOPE:
            return True, "VIX +6/5d"
    return False, ""


def hold_only_should_exit(row, held, T_rem):
    """Safety nets only — used as the floor for individual sell-rule tests."""
    if T_rem <= EXIT_NEAR_EXP:
        return True, "Near expiry"
    if held >= MAX_HOLD:
        return True, "Max hold"
    return False, ""


def simulate_with_policy(entries: list[dict], feats: pd.DataFrame,
                         exit_policy, label: str) -> pd.DataFrame:
    """Simulate each entry under `exit_policy(row, held, T_rem) -> (sell, reason)`.

    For the 10 sell-rule policies, we also pass the today_idx so explain_sell
    can look backwards in history.
    """
    rows = []
    feats_index = feats.index
    for e in entries:
        entry_date = e["date"]
        try:
            start_loc = feats_index.get_loc(entry_date) + 1  # the day AFTER entry
        except KeyError:
            continue

        for day_loc in range(start_loc, len(feats_index)):
            day = feats_index[day_loc]
            row = feats.iloc[day_loc]
            held = (day - entry_date).days
            T_rem = max((e["expiry"] - day).days / 365.25, 1e-6)

            sell, reason = exit_policy(row, held, T_rem, day_loc, e)
            if sell:
                spy = float(row["SPY"])
                sigma = float(row.get("IV1Y_cal", row["VIX"] / 100))
                spread = float(row.get("spread", 0.04))
                mark_bid = bs_call(spy, e["strike"], T_rem, RISK_FREE_RATE, sigma) \
                           * (1 - spread / 2)
                proceeds = mark_bid * 100 * e["contracts"] - e["contracts"] * COMMISSION_PER_CONTRACT
                pnl_pct = (proceeds - e["cost"]) / e["cost"] * 100
                rows.append({
                    "entry_date": entry_date, "exit_date": day,
                    "held": held, "exit_reason": reason,
                    "entry_spy": e["spy"], "exit_spy": spy,
                    "pnl_pct": pnl_pct,
                })
                break

    df = pd.DataFrame(rows)
    df["policy"] = label
    return df


# ─── Policy factories ────────────────────────────────────────────────────────

def make_baseline_policy():
    def policy(row, held, T_rem, today_idx, _entry):
        return baseline_should_exit(row, held, T_rem)
    return policy


def make_hold_only_policy():
    def policy(row, held, T_rem, today_idx, _entry):
        return hold_only_should_exit(row, held, T_rem)
    return policy


def make_sell_rule_policy(rule_key: str, feats: pd.DataFrame, min_hold: int = MIN_HOLD):
    """Sell when `rule_key` fires (after MIN_HOLD); otherwise hold to safety net."""
    def policy(row, held, T_rem, today_idx, _entry):
        # Safety nets always first
        nh, nr = hold_only_should_exit(row, held, T_rem)
        if nh:
            return nh, nr
        if held >= min_hold:
            fired, _conds = explain_sell(rule_key, row, feats, today_idx)
            if fired:
                return True, rule_key
        return False, ""
    return policy


# ─── Scoring ────────────────────────────────────────────────────────────────

def score_policy(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(n=0)
    return dict(
        n=len(df),
        mean=df["pnl_pct"].mean(),
        median=df["pnl_pct"].median(),
        win_rate=(df["pnl_pct"] > 0).mean() * 100,
        big_loss_pct=(df["pnl_pct"] < -20).mean() * 100,
        big_win_pct=(df["pnl_pct"] > 50).mean() * 100,
        avg_held=df["held"].mean(),
        worst=df["pnl_pct"].min(),
        best=df["pnl_pct"].max(),
    )


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=10.0,
                    help="Backtest window (years from latest data point)")
    args = ap.parse_args()

    print("\n" + "═" * 100)
    print("  SELL-SIGNAL BACKTEST  •  ranking 10 candidate exit rules")
    print("═" * 100)

    df = load_data()
    feats = extend_features(df)
    sigs = signals_in_window(feats, 1)
    if args.years is not None:
        cutoff = feats.index[-1] - pd.Timedelta(days=int(365 * args.years))
        feats = feats.loc[cutoff:]
        sigs = sigs.loc[cutoff:] if not sigs.empty else sigs

    print(f"  Period          : {feats.index[0].date()} → {feats.index[-1].date()}")
    print(f"  Trading days    : {len(feats)}")

    entries = collect_entries(feats, sigs)
    print(f"  LEAPS entries   : {len(entries)} (deduped, 14-day debounce, all 10 buy rules)")
    print(f"  Capital per lot : ${PER_LOT:,}\n")

    # Run baseline + hold-only + each sell rule
    results = {}
    print("  Running policies...")

    results["BASELINE_current"] = simulate_with_policy(
        entries, feats,
        lambda r, h, t, i, e: baseline_should_exit(r, h, t),
        "BASELINE_current")
    print(f"    BASELINE_current        — {len(results['BASELINE_current'])} trades")

    results["HOLD_ONLY"] = simulate_with_policy(
        entries, feats,
        lambda r, h, t, i, e: hold_only_should_exit(r, h, t),
        "HOLD_ONLY")
    print(f"    HOLD_ONLY               — {len(results['HOLD_ONLY'])} trades")

    for key, _ in SELL_RULES:
        policy = make_sell_rule_policy(key, feats)
        results[key] = simulate_with_policy(entries, feats, policy, key)
        print(f"    {key:<24}— {len(results[key])} trades")

    # ── Combo policies: union of multiple top sell rules ───────────────────
    def make_combo_policy(keys: list[str], min_hold: int = MIN_HOLD):
        def policy(row, held, T_rem, today_idx, _entry):
            nh, nr = hold_only_should_exit(row, held, T_rem)
            if nh:
                return nh, nr
            if held >= min_hold:
                for k in keys:
                    fired, _ = explain_sell(k, row, feats, today_idx)
                    if fired:
                        return True, k
            return False, ""
        return policy

    combo_specs = [
        ("COMBO_top2_low+regime",   ["S5_NEW_60D_LOW", "S10_VIX_REGIME"]),
        ("COMBO_top3_lowVixPanic",  ["S5_NEW_60D_LOW", "S10_VIX_REGIME", "S2_VIX_PANIC"]),
        ("COMBO_top4_+spike",       ["S5_NEW_60D_LOW", "S10_VIX_REGIME", "S2_VIX_PANIC", "S1_VIX_SPIKE"]),
        ("COMBO_only_extreme",      ["S5_NEW_60D_LOW", "S1_VIX_SPIKE"]),
    ]
    for name, keys in combo_specs:
        results[name] = simulate_with_policy(entries, feats, make_combo_policy(keys), name)
        print(f"    {name:<24}— {len(results[name])} trades")

    # Score and rank
    print("\n" + "═" * 100)
    print("  RESULTS (ranked by mean P&L)")
    print("═" * 100)
    table = []
    for label, dfp in results.items():
        s = score_policy(dfp)
        s["policy"] = label
        table.append(s)
    rank = sorted(table, key=lambda x: -x.get("mean", -1e9))

    hdr = f"{'Policy':<24}  {'n':>4}  {'Mean%':>7}  {'Median%':>8}  {'Win%':>5}  {'BigLoss%':>8}  {'BigWin%':>7}  {'AvgDays':>7}  {'Worst%':>7}  {'Best%':>6}"
    print(hdr)
    print("─" * len(hdr))
    for s in rank:
        if s.get("n", 0) == 0:
            continue
        print(f"{s['policy']:<24}  {s['n']:>4}  "
              f"{s['mean']:>+7.1f}  {s['median']:>+8.1f}  "
              f"{s['win_rate']:>5.1f}  {s['big_loss_pct']:>8.1f}  "
              f"{s['big_win_pct']:>7.1f}  {s['avg_held']:>7.0f}  "
              f"{s['worst']:>+7.1f}  {s['best']:>+6.1f}")

    # Differential vs HOLD_ONLY
    print("\n" + "═" * 100)
    print("  IMPROVEMENT vs HOLD_ONLY  (mean P&L delta — positive = sell rule HELPED)")
    print("═" * 100)
    ho = score_policy(results["HOLD_ONLY"])
    print(f"  HOLD_ONLY baseline: mean {ho['mean']:+.1f}%, win {ho['win_rate']:.0f}%, "
          f"avg held {ho['avg_held']:.0f}d\n")
    diffs = []
    for label, dfp in results.items():
        if label == "HOLD_ONLY":
            continue
        s = score_policy(dfp)
        if s.get("n", 0) == 0:
            continue
        diffs.append({
            "policy": label,
            "delta_mean": s["mean"] - ho["mean"],
            "delta_win":  s["win_rate"] - ho["win_rate"],
            "delta_bigloss": s["big_loss_pct"] - ho["big_loss_pct"],
            "delta_held": s["avg_held"] - ho["avg_held"],
        })
    diffs.sort(key=lambda x: -x["delta_mean"])
    print(f"  {'Policy':<24}  {'Δ mean%':>9}  {'Δ win%':>8}  {'Δ bigloss%':>11}  {'Δ days':>7}")
    print("  " + "─" * 70)
    for d in diffs:
        print(f"  {d['policy']:<24}  {d['delta_mean']:>+9.1f}  "
              f"{d['delta_win']:>+8.1f}  {d['delta_bigloss']:>+11.1f}  "
              f"{d['delta_held']:>+7.0f}")

    # Save
    out_dir = PROJECT_ROOT / "sell_signals"
    all_df = pd.concat(results.values(), ignore_index=True)
    all_df.to_csv(out_dir / "sell_backtest_results.csv", index=False)
    print(f"\n  📝 Saved {len(all_df)} trade records → sell_signals/sell_backtest_results.csv")


if __name__ == "__main__":
    main()
