"""
Alternative Entry Rules — Head-to-Head Comparison
==================================================

Tests many different "buy" criteria against the same exit rules and
SPY-DCA benchmark. Each rule uses fresh $10k per entry (matches user's
actual deployment style).

All strategies share these EXIT rules (for fair comparison):
  - Min hold 180 days, max hold 500 days
  - Exit if SPY closes < 50DMA × 0.97 (after min hold)
  - Exit if VIX > 30
  - Exit if VIX rose 6+ points in 5 days
  - Exit if option has < 4 months to expiry
  - 14-day debounce between any two entries

Strategies tested:
  A_CURRENT       : 2-of-3 momentum + gates (the recommended rule)
  B_FEAR_REVERT   : VIX spike + receding fear + uptrend
  C_CHEAP_IV      : Low VIX (cheap calls) + trend intact
  D_BREAKOUT      : New 60-day high
  E_OVERSOLD      : RSI < 35 in established uptrend
  F_VIX_CRUSH     : VIX collapsed 30% in 10 days
  G_GOLDEN_CROSS  : SPY crosses above 200DMA
  H_TREND_FOLLOW  : SPY > 50DMA > 200DMA, MACD > 0, low RSI
  I_BB_SQUEEZE    : Bollinger Band squeeze followed by breakout
  J_DEEP_DD       : SPY drawdown -10% to -20% + recovery starting

Usage:
    python strategy_alternatives.py
    python strategy_alternatives.py --per-lot 10000
"""

from __future__ import annotations
import argparse, os
import pandas as pd
import numpy as np
from typing import Callable
from dataclasses import dataclass

from strategy_backtest import (
    PROFILES, add_features, signals_in_window, bs_call, load_data,
    RISK_FREE_RATE, LEAPS_YEARS,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
    COMMISSION_PER_CONTRACT,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG, FreshLot

PER_LOT_DEFAULT = 10_000
DEBOUNCE_DEFAULT = 14
MIN_HOLD = 180
MAX_HOLD = 500


def extend_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add features required by alternative strategies."""
    df = add_features(df)
    # 60-day high (breakout detection)
    df["high60"] = df["SPY"].rolling(60).max()
    df["is_new_high60"] = df["SPY"] >= df["high60"]
    # Bollinger bands (20, 2σ)
    df["bb_mid"] = df["SPY"].rolling(20).mean()
    df["bb_std"] = df["SPY"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_width_pct"] = df["bb_width"].rolling(252).rank(pct=True)  # 0-1 percentile vs prior year
    # VIX 10-day max
    df["vix_max10"] = df["VIX"].rolling(10).max()
    df["vix_crush"] = (df["vix_max10"] - df["VIX"]) / df["vix_max10"]   # share of vol crushed
    # 200DMA cross today
    df["spy200_reclaim"] = (df["SPY"] >= df["sma200"]) & (df["SPY"].shift(1) < df["sma200"].shift(1))
    # VIX falling
    df["vix_falling"] = df["vix_slope5"] < -2
    # VIX 30-day rolling mean (for the N_FILTER rule)
    df["vix_30d_mean"] = df["VIX"].rolling(30).mean()
    return df


# ─── ENTRY RULES ─────────────────────────────────────────────────────────────
# Each rule: (df_row, prev_score) -> bool

def rule_A_current(row, sigs_row) -> bool:
    """The recommended STRICT rule: 2-of-3 momentum + gates."""
    return (
        sigs_row["score"] >= 2 and
        bool(row["spy_above_200"]) and
        row["VIX"] < 28 and
        row["RSI14"] < 65
    )

def rule_B_fear_revert(row, sigs_row) -> bool:
    """VIX spike followed by receding fear, in established uptrend."""
    return (
        row["VIX"] > 22 and
        bool(row["vix_falling"]) and        # VIX dropping
        row["drawdown"] > -15 and           # not in full crash
        row["drawdown"] < -3 and            # but there was some pullback
        bool(row["spy_above_200"])
    )

def rule_C_cheap_iv(row, sigs_row) -> bool:
    """Cheap options (low VIX) + clear uptrend."""
    return (
        row["VIX"] < 16 and
        bool(row["spy_above_50"]) and
        bool(row["spy_above_200"]) and
        40 <= row["RSI14"] <= 65
    )

def rule_D_breakout(row, sigs_row) -> bool:
    """New 60-day high — momentum continuation."""
    return (
        bool(row["is_new_high60"]) and
        row["VIX"] < 25 and
        row["RSI14"] < 75 and
        bool(row["spy_above_200"])
    )

def rule_E_oversold_uptrend(row, sigs_row) -> bool:
    """Oversold pullback inside a long-term uptrend."""
    return (
        bool(row["spy_above_200"]) and
        row["RSI14"] < 38 and
        row["VIX"] > 20 and
        row["VIX"] < 35
    )

def rule_F_vix_crush(row, sigs_row) -> bool:
    """VIX collapsed sharply — fear is receding."""
    return (
        row["vix_crush"] >= 0.30 and        # VIX dropped at least 30% off 10-day high
        row["VIX"] < 22 and
        bool(row["spy_above_200"])
    )

def rule_G_golden_cross(row, sigs_row) -> bool:
    """SPY crosses above 200DMA — regime change to bullish."""
    return (
        bool(row["spy200_reclaim"]) and
        row["VIX"] < 28
    )

def rule_H_trend_follow(row, sigs_row) -> bool:
    """Classic trend filter: 50>200, MACD>0, RSI mid-range."""
    return (
        bool(row["spy_above_50"]) and
        bool(row["spy_above_200"]) and
        row["sma50"] > row["sma200"] and
        row["macd"] > 0 and
        40 <= row["RSI14"] <= 65 and
        row["VIX"] < 25
    )

def rule_I_bb_squeeze(row, sigs_row) -> bool:
    """Bollinger band squeeze followed by upper breakout."""
    return (
        row["bb_width_pct"] < 0.20 and       # vol compressed (bottom 20%)
        row["SPY"] >= row["bb_upper"] and    # breaking upper band
        bool(row["spy_above_200"]) and
        row["VIX"] < 22
    )

def rule_J_deep_dd(row, sigs_row) -> bool:
    """Deep drawdown -8% to -20%, recovery starting (above 50DMA), uptrend intact."""
    return (
        -20 <= row["drawdown"] <= -8 and
        bool(row["spy_above_50"]) and
        row["VIX"] < 28
    )

# Combination rules — intersection of best individual rules
def rule_K_strict_cheap(row, sigs_row) -> bool:
    """A (momentum cross) AND C (cheap IV) — strictest, fewest fires."""
    return rule_A_current(row, sigs_row) and rule_C_cheap_iv(row, sigs_row)

def rule_L_squeeze_or_current(row, sigs_row) -> bool:
    """A (momentum cross) OR I (BB squeeze) — fires when either triggers."""
    return rule_A_current(row, sigs_row) or rule_I_bb_squeeze(row, sigs_row)

def rule_M_quality_breakout(row, sigs_row) -> bool:
    """D (60-day high) AND VIX < 18 — only breakouts when IV is cheap."""
    return (
        bool(row["is_new_high60"]) and
        row["VIX"] < 18 and
        row["RSI14"] < 70 and
        bool(row["spy_above_200"])
    )

def rule_N_filter_current(row, sigs_row) -> bool:
    """A (current) with extra anti-top filter: VIX must have been calm for 30 days."""
    return (
        rule_A_current(row, sigs_row) and
        row["VIX"] < 22 and
        row.get("vix_30d_mean", row["VIX"]) < 20
    )


STRATEGIES = {
    "A_CURRENT":      rule_A_current,
    "B_FEAR_REVERT":  rule_B_fear_revert,
    "C_CHEAP_IV":     rule_C_cheap_iv,
    "D_BREAKOUT":     rule_D_breakout,
    "E_OVERSOLD":     rule_E_oversold_uptrend,
    "F_VIX_CRUSH":    rule_F_vix_crush,
    "G_GOLDEN_CROSS": rule_G_golden_cross,
    "H_TREND_FOLLOW": rule_H_trend_follow,
    "I_BB_SQUEEZE":   rule_I_bb_squeeze,
    "J_DEEP_DD":      rule_J_deep_dd,
    "K_STRICT_CHEAP": rule_K_strict_cheap,
    "L_A_OR_SQUEEZE": rule_L_squeeze_or_current,
    "M_QUAL_BREAKOUT":rule_M_quality_breakout,
    "N_FILTER_CURR":  rule_N_filter_current,
}


def run_strategy(df: pd.DataFrame, rule: Callable, per_lot: float,
                 debounce_days: int = DEBOUNCE_DEFAULT,
                 start_date: str | None = None, end_date: str | None = None):
    feats = extend_features(df)
    if start_date is not None:
        feats = feats.loc[start_date:]
    if end_date is not None:
        feats = feats.loc[:end_date]
    sigs = signals_in_window(feats, 1)  # 1-day cross window for rule_A

    open_lots = []
    closed_lots: list[FreshLot] = []
    last_entry = None

    for date, row in feats.iterrows():
        spy = float(row["SPY"])
        if pd.isna(spy):
            continue
        sigma = float(row["IV1Y_cal"]) if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"]) else float(row["VIX"]) / 100
        spread = float(row["spread"]) if "spread" in row and pd.notna(row["spread"]) else 0.04
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})

        # Update / exit open lots
        still_open = []
        for lot in open_lots:
            T_rem = max((lot["expiry"] - date).days / 365.25, 1e-6)
            mark_bid = bs_call(spy, lot["strike"], T_rem, RISK_FREE_RATE, sigma) * (1 - spread / 2)
            mtm = mark_bid * 100 * lot["contracts"]
            held = (date - lot["entry_date"]).days
            sell, reason = False, ""
            if T_rem <= EXIT_NEAR_EXP:
                sell, reason = True, "Near expiry"
            elif held >= MAX_HOLD:
                sell, reason = True, "Max hold"
            elif held >= MIN_HOLD:
                if spy < row["sma50"] * EXIT_DD_50DMA:
                    sell, reason = True, "SPY broke 50DMA"
                elif row["VIX"] > EXIT_VIX_HIGH:
                    sell, reason = True, f"VIX>{EXIT_VIX_HIGH}"
                elif row["vix_slope5"] > EXIT_VIX_SLOPE:
                    sell, reason = True, f"VIX +{EXIT_VIX_SLOPE}/5d"

            if sell:
                sell_commission = lot["contracts"] * COMMISSION_PER_CONTRACT
                proceeds = mtm - sell_commission
                closed_lots.append(FreshLot(
                    entry_date=lot["entry_date"], exit_date=date,
                    entry_spy=lot["entry_spy"], exit_spy=spy,
                    entry_vix=lot["entry_vix"],
                    contracts=lot["contracts"], cost=lot["cost"], proceeds=proceeds,
                    pct=(proceeds - lot["cost"]) / lot["cost"],
                    held_days=held, exit_reason=reason,
                    spy_shares=lot["spy_shares"],
                    spy_value_at_exit=lot["spy_shares"] * spy,
                ))
            else:
                still_open.append(lot)
        open_lots = still_open

        # Entry check
        debounce_ok = last_entry is None or (date - last_entry).days >= debounce_days
        try:
            eligible = bool(rule(row, sigs_row)) and debounce_ok
        except (KeyError, TypeError):
            eligible = False

        if eligible:
            strike = round(spy)
            premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
            if premium > 0:
                contracts = int(per_lot / (premium * 100))
                if contracts >= 1:
                    buy_commission = contracts * COMMISSION_PER_CONTRACT
                    cost = contracts * premium * 100 + buy_commission
                    spy_shares_eq = cost / spy
                    open_lots.append({
                        "strike": strike, "contracts": contracts,
                        "entry_date": date, "entry_spy": spy,
                        "entry_vix": float(row["VIX"]),
                        "cost": cost,
                        "expiry": date + pd.Timedelta(days=int(LEAPS_YEARS * 365)),
                        "spy_shares": spy_shares_eq,
                    })
                    last_entry = date

    # Force-close any remaining open lots at end (MTM)
    final_spy = float(feats["SPY"].iloc[-1])
    final_sigma = float(feats["IV1Y_cal"].iloc[-1]) if pd.notna(feats["IV1Y_cal"].iloc[-1]) else float(feats["VIX"].iloc[-1]) / 100
    for lot in open_lots:
        T_rem = max((lot["expiry"] - feats.index[-1]).days / 365.25, 1e-6)
        mtm = bs_call(final_spy, lot["strike"], T_rem, RISK_FREE_RATE, final_sigma) * 100 * lot["contracts"]
        held = (feats.index[-1] - lot["entry_date"]).days
        closed_lots.append(FreshLot(
            entry_date=lot["entry_date"], exit_date=feats.index[-1],
            entry_spy=lot["entry_spy"], exit_spy=final_spy,
            entry_vix=lot["entry_vix"],
            contracts=lot["contracts"], cost=lot["cost"], proceeds=mtm,
            pct=(mtm - lot["cost"]) / lot["cost"],
            held_days=held, exit_reason="(still open)",
            spy_shares=lot["spy_shares"],
            spy_value_at_exit=lot["spy_shares"] * final_spy,
        ))

    return pd.DataFrame([l.__dict__ for l in closed_lots]) if closed_lots else pd.DataFrame()


def evaluate(trades: pd.DataFrame, years: float):
    if trades.empty:
        return None
    deployed = trades["cost"].sum()
    leaps_val = trades["proceeds"].sum()
    spy_val = trades["spy_value_at_exit"].sum()
    leaps_tax = sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                    for t in trades.itertuples() if t.proceeds > t.cost)
    spy_pnl = spy_val - deployed
    spy_tax = max(0, spy_pnl) * TAX_LONG
    leaps_after = leaps_val - leaps_tax
    spy_after = spy_val - spy_tax
    n = len(trades)
    wins = (trades["pct"] > 0).sum()
    return {
        "trades": n, "per_year": n / years, "wins": wins,
        "win_rate": wins / n * 100,
        "deployed": deployed,
        "leaps_pre": leaps_val, "leaps_post": leaps_after,
        "spy_pre": spy_val,    "spy_post":  spy_after,
        "leaps_ret_pre":  (leaps_val/deployed - 1) * 100,
        "leaps_ret_post": (leaps_after/deployed - 1) * 100,
        "spy_ret_pre":    (spy_val/deployed - 1) * 100,
        "spy_ret_post":   (spy_after/deployed - 1) * 100,
        "edge_pre":  (leaps_val - spy_val) / deployed * 100,
        "edge_post": (leaps_after - spy_after) / deployed * 100,
        "edge_pre_$":  leaps_val - spy_val,
        "edge_post_$": leaps_after - spy_after,
        "avg_pct":  trades["pct"].mean() * 100,
        "avg_win":  trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0,
        "avg_loss": trades[trades["pct"] <= 0]["pct"].mean() * 100 if n - wins else 0,
        "worst":    trades["pct"].min() * 100,
        "best":     trades["pct"].max() * 100,
    }


def print_header(title):
    print(f"\n  {'Strategy':18s}  {'Trades':>14}  {'Win%':>5}  "
          f"{'LEAPS post':>11}  {'SPY post':>9}  "
          f"{'EDGE%':>6}  {'EDGE $':>10}  {'AvgW':>6}  {'AvgL':>6}  {'Worst':>6}")
    print("  " + "─" * 122)

def print_row(name, m):
    if m is None:
        print(f"  {name:18s}  (no trades)")
        return
    print(f"  {name:18s}  {m['trades']:>4} ({m['per_year']:>3.1f}/yr)  {m['win_rate']:>4.0f}%  "
          f"{m['leaps_ret_post']:>+9.1f}%  {m['spy_ret_post']:>+7.1f}%  "
          f"{m['edge_post']:>+5.1f}%  ${m['edge_post_$']:>+8,.0f}  "
          f"{m['avg_win']:>+5.1f}%  {m['avg_loss']:>+5.1f}%  {m['worst']:>+5.1f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-lot", type=float, default=PER_LOT_DEFAULT)
    parser.add_argument("--data", default="data_cache/term_structure.csv")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    df = load_data(args.data)
    feats = extend_features(df)
    years_full = (feats.index[-1] - feats.index[0]).days / 365.25
    years_train = (pd.Timestamp("2020-12-31") - pd.Timestamp("2016-05-02")).days / 365.25
    years_test  = (pd.Timestamp("2026-05-13") - pd.Timestamp("2021-01-01")).days / 365.25

    all_results = []

    for label, (start, end, yrs) in {
        "FULL  (2016-2026)": (None, None, years_full),
        "TRAIN (2016-2020)": ("2016-05-02", "2020-12-31", years_train),
        "TEST  (2021-2026)": ("2021-01-01", "2026-05-13", years_test),
    }.items():
        print(f"\n{'═' * 124}")
        print(f"  PERIOD: {label}   •   ${args.per_lot:,.0f} fresh capital per entry   •   apples-to-apples vs SPY-DCA")
        print(f"{'═' * 124}")
        print_header(label)
        for name, rule in STRATEGIES.items():
            trades = run_strategy(df, rule, args.per_lot, start_date=start, end_date=end)
            m = evaluate(trades, yrs)
            print_row(name, m)
            if m:
                all_results.append({"period": label, "strategy": name, **m})

    pd.DataFrame(all_results).to_csv(f"{args.out}/strategy_comparison.csv", index=False)
    print(f"\n  💾 Saved: {args.out}/strategy_comparison.csv")

    # ──── Find winners ────────────────────────────────────────────────────────
    print(f"\n{'═' * 124}")
    print(f"  WINNERS (sorted by TEST-period after-tax edge — most important for forward-looking use)")
    print(f"{'═' * 124}")
    test_results = [r for r in all_results if r["period"].startswith("TEST")]
    test_results.sort(key=lambda r: -r["edge_post"])
    print_header("Test 2021-2026")
    for r in test_results:
        print_row(r["strategy"], r)

    print(f"\n  Interpretation:")
    print(f"    • Pre-tax  edge: option leverage advantage on the same dates")
    print(f"    • Post-tax edge: net after 32% short-term + 20% LTCG SPY benchmark")
    print(f"    • A strategy is REAL if post-tax edge in TEST period > 0  AND  > 1pp (vs noise)")

if __name__ == "__main__":
    main()
