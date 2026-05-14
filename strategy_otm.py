"""
OTM 2-year LEAPS variant
========================

Tests the winning strategies (BB_SQUEEZE, P_DEEP_SQUEEZE, Q_TRIPLE_ALIGN, A_CURRENT)
with OUT-OF-THE-MONEY 2-year strikes instead of at-the-money.

For each OTM level (0%, 5%, 10%, 15%, 20%) and each strategy, we backtest
the rotation model (cash never idle, sells VOO to fund LEAPS) over 10 years.

Why test OTM?
  - Lower per-contract cost → fits smaller account sizes
  - Higher leverage to SPY moves → bigger % wins on right call
  - BUT: higher probability of expiring worthless if SPY doesn't rally enough

Strike rounding: SPY 2-yr LEAPS strikes are at $5 increments.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Callable
from dataclasses import dataclass
from tqdm import tqdm

from strategy_backtest import (
    add_features, signals_in_window, bs_call, load_data,
    RISK_FREE_RATE, LEAPS_YEARS,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
    COMMISSION_PER_CONTRACT,
)
from strategy_alternatives import (
    extend_features, rule_I_bb_squeeze, rule_A_current,
)
from strategy_high_conviction import (
    rule_P_deep_squeeze, rule_Q_triple_align,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG, FreshLot
from compare_rotation import rotation_portfolio, voo_only_portfolio

MIN_HOLD = 180
MAX_HOLD = 500
DEBOUNCE_DEFAULT = 14
MONTHLY_SAVINGS = 2_500
PER_LOT = 10_000


def round_to_strike(spy: float, otm_pct: float, increment: float = 5.0) -> float:
    """Round target strike to nearest $5 SPY strike increment."""
    target = spy * (1 + otm_pct)
    return round(target / increment) * increment


def run_strategy_otm(df, rule, per_lot, otm_pct, debounce_days=DEBOUNCE_DEFAULT,
                    start_date=None, end_date=None):
    """Same as run_strategy but with configurable OTM strike."""
    feats = extend_features(df)
    if start_date:
        feats = feats.loc[start_date:]
    if end_date:
        feats = feats.loc[:end_date]
    sigs = signals_in_window(feats, 1)

    open_lots, closed_lots, last_entry = [], [], None

    for date, row in feats.iterrows():
        spy = float(row["SPY"])
        if pd.isna(spy):
            continue
        sigma = float(row["IV1Y_cal"]) if pd.notna(row.get("IV1Y_cal", np.nan)) else float(row["VIX"]) / 100
        spread = float(row["spread"]) if pd.notna(row.get("spread", np.nan)) else 0.045
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
                sell_comm = lot["contracts"] * COMMISSION_PER_CONTRACT
                proceeds = max(0, mtm - sell_comm)
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

        debounce_ok = last_entry is None or (date - last_entry).days >= debounce_days
        try:
            eligible = bool(rule(row, sigs_row)) and debounce_ok
        except (KeyError, TypeError):
            eligible = False

        if eligible:
            strike = round_to_strike(spy, otm_pct)
            premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
            if premium > 0.05:  # too cheap = unrealistic, must be at least $0.05/share
                contracts = int(per_lot / (premium * 100))
                if contracts >= 1:
                    buy_comm = contracts * COMMISSION_PER_CONTRACT
                    cost = contracts * premium * 100 + buy_comm
                    open_lots.append({
                        "strike": strike, "contracts": contracts,
                        "entry_date": date, "entry_spy": spy,
                        "entry_vix": float(row["VIX"]),
                        "cost": cost,
                        "expiry": date + pd.Timedelta(days=int(LEAPS_YEARS * 365)),
                        "spy_shares": cost / spy,
                    })
                    last_entry = date

    # Force-close at end
    final_spy = float(feats["SPY"].iloc[-1])
    final_sigma = float(feats["IV1Y_cal"].iloc[-1]) if pd.notna(feats["IV1Y_cal"].iloc[-1]) else float(feats["VIX"].iloc[-1]) / 100
    for lot in open_lots:
        T_rem = max((lot["expiry"] - feats.index[-1]).days / 365.25, 1e-6)
        mtm = max(0, bs_call(final_spy, lot["strike"], T_rem, RISK_FREE_RATE, final_sigma) * 100 * lot["contracts"])
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


STRATEGIES = [
    ("BB_SQUEEZE",     rule_I_bb_squeeze),
    ("Q_TRIPLE_ALIGN", rule_Q_triple_align),
    ("P_DEEP_SQUEEZE", rule_P_deep_squeeze),
    ("A_CURRENT",      rule_A_current),
]

OTM_LEVELS = [0.00, 0.05, 0.10, 0.15, 0.20]   # 0%, 5%, 10%, 15%, 20% OTM
PERIOD = ("2016-05-13", "2026-05-13")


def evaluate(trades, feats_period, voo_pure_after):
    if trades.empty:
        return None
    result = rotation_portfolio(trades, feats_period, MONTHLY_SAVINGS)
    leaps_tax = sum(
        (t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
        for t in trades.itertuples() if t.proceeds > t.cost
    )
    voo_basis = result["deposited"] + result["leaps_proceeds"]
    voo_gain = result["voo_value"] - voo_basis
    voo_tax = max(0, voo_gain) * TAX_LONG
    profit = result["total"] - result["deposited"]
    after_tax = profit - leaps_tax - voo_tax

    wins = (trades["pct"] > 0).sum()
    losses = (trades["pct"] <= 0).sum()
    total_losers = trades[trades["pct"] <= -0.95]  # essentially total losses
    return {
        "trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) * 100,
        "total_losses": len(total_losers),
        "avg_win": trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0,
        "avg_loss": trades[trades["pct"] <= 0]["pct"].mean() * 100 if losses else 0,
        "worst": trades["pct"].min() * 100,
        "best": trades["pct"].max() * 100,
        "avg_cost": trades["cost"].mean(),
        "avg_held": trades["held_days"].mean(),
        "total_deployed": trades["cost"].sum(),
        "total_proceeds": trades["proceeds"].sum(),
        "after_tax": after_tax,
        "edge_aftertax": after_tax - voo_pure_after,
        "final": result["total"],
        "deposited": result["deposited"],
    }


def main():
    df = load_data()
    feats_full = extend_features(df)
    feats_period = feats_full.loc[PERIOD[0]:PERIOD[1]]

    voo_pure = voo_only_portfolio(feats_period, MONTHLY_SAVINGS)
    pure_pnl = voo_pure["total"] - voo_pure["deposited"]
    voo_pure_after = pure_pnl - max(0, pure_pnl) * TAX_LONG

    print("\n" + "═" * 130)
    print(f"  OTM 2-YEAR LEAPS  •  10-year backtest  •  Rotation model  "
          f"•  ${MONTHLY_SAVINGS:,}/mo savings")
    print(f"  Period: {PERIOD[0]} → {PERIOD[1]}    "
          f"Deposited: ${voo_pure['deposited']:,.0f}    "
          f"Pure VOO DCA after-tax profit: ${voo_pure_after:+,.0f}")
    print("═" * 130)

    all_results = {}
    combos = [(name, rule, otm) for name, rule in STRATEGIES for otm in OTM_LEVELS]
    print(f"\n  Running {len(combos)} strategy×OTM combos...")

    for name, rule, otm in tqdm(combos, ncols=80):
        trades = run_strategy_otm(df, rule, PER_LOT, otm,
                                  start_date=PERIOD[0], end_date=PERIOD[1])
        r = evaluate(trades, feats_period, voo_pure_after)
        all_results[(name, otm)] = r

    # ── Per-strategy table ───────────────────────────────────────────────────
    for name, _ in STRATEGIES:
        print(f"\n  ── {name} at varying moneyness ──")
        print(f"     {'Strike':<9}  {'Trades':>6}  {'Win%':>5}  {'Worst':>6}  "
              f"{'Best':>6}  {'AvgCost':>8}  {'After-tax':>12}  {'Edge vs VOO':>13}")
        print("     " + "─" * 95)
        for otm in OTM_LEVELS:
            r = all_results[(name, otm)]
            if r is None:
                print(f"     {otm*100:>+4.0f}%-OTM  {'(no fires)':>12}")
                continue
            verdict = "✅" if r["edge_aftertax"] > 0 else "❌"
            print(f"     {otm*100:>+4.0f}%-OTM  "
                  f"{r['trades']:>6}  "
                  f"{r['win_rate']:>4.0f}%  "
                  f"{r['worst']:>+5.0f}%  "
                  f"{r['best']:>+5.0f}%  "
                  f"${r['avg_cost']:>6,.0f}  "
                  f"${r['after_tax']:>+10,.0f}  "
                  f"${r['edge_aftertax']:>+11,.0f} {verdict}")

    # ── Best combo overall ───────────────────────────────────────────────────
    best = max(all_results.items(), key=lambda kv: kv[1]["edge_aftertax"] if kv[1] else -1e9)
    print(f"\n  🏆 BEST COMBO: {best[0][0]} at {best[0][1]*100:+.0f}% OTM  "
          f"→ after-tax edge ${best[1]['edge_aftertax']:+,.0f}")

    # ── Today's contract cost reference ──────────────────────────────────────
    latest = feats_full.iloc[-1]
    spy_now, vix_now = float(latest["SPY"]), float(latest["VIX"])
    iv_now = float(latest["IV1Y_cal"]) if pd.notna(latest["IV1Y_cal"]) else vix_now / 100
    spread_now = float(latest["spread"]) if pd.notna(latest["spread"]) else 0.045

    print(f"\n  ── TODAY'S COST PER CONTRACT (SPY ${spy_now:.0f}, VIX {vix_now:.1f}, "
          f"IV {iv_now*100:.1f}%) ──")
    print(f"     {'OTM':<8}  {'Strike':>7}  {'Premium':>9}  {'Cost / contract':>18}  "
          f"{'Delta':>6}")
    print("     " + "─" * 65)
    for otm in OTM_LEVELS:
        strike = round_to_strike(spy_now, otm)
        prem = bs_call(spy_now, strike, LEAPS_YEARS, RISK_FREE_RATE, iv_now) * (1 + spread_now / 2)
        cost = prem * 100 + COMMISSION_PER_CONTRACT
        # rough delta approximation: d1 in BS
        from math import log, sqrt
        from scipy.stats import norm
        d1 = (log(spy_now/strike) + (RISK_FREE_RATE + iv_now**2 / 2) * LEAPS_YEARS) / (iv_now * sqrt(LEAPS_YEARS))
        delta = norm.cdf(d1)
        print(f"     {otm*100:>+4.0f}%   ${strike:>5,.0f}    ${prem:>6.2f}    "
              f"${cost:>10,.0f}{'  per contract':>10}    {delta:>4.2f}")


if __name__ == "__main__":
    main()
