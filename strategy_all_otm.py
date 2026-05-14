"""
ALL strategies tested at multiple OTM levels (2-year LEAPS)
============================================================

Tests every entry rule from strategy_alternatives.py + strategy_high_conviction.py
against the rotation model with monthly $2,500 VOO DCA, over the past 10 years.

Each strategy is tested at +0%, +10%, +15% OTM strikes (always 2-yr expiry).

The point is to find strategies that:
  • Fire MORE often (several signals per year)
  • Still keep a decent win rate
  • Compound the small wins via reinvestment into VOO

Output: ranked tables and a master comparison chart.
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd
from tqdm import tqdm

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from strategy_backtest import load_data
from strategy_alternatives import (
    extend_features,
    rule_A_current, rule_B_fear_revert, rule_C_cheap_iv, rule_D_breakout,
    rule_E_oversold_uptrend, rule_F_vix_crush, rule_G_golden_cross,
    rule_H_trend_follow, rule_I_bb_squeeze, rule_J_deep_dd,
    rule_K_strict_cheap, rule_L_squeeze_or_current,
    rule_M_quality_breakout, rule_N_filter_current,
)
from strategy_high_conviction import (
    rule_P_deep_squeeze, rule_Q_triple_align, rule_R_fear_recovery,
    rule_S_perfect_setup, rule_T_annual_elite,
)
from strategy_otm import run_strategy_otm
from strategy_fresh_capital import TAX_SHORT, TAX_LONG
from compare_rotation import rotation_portfolio, voo_only_portfolio

PER_LOT = 10_000
MONTHLY_SAVINGS = 2_500
START, END = "2016-05-13", "2026-05-13"

ALL_STRATEGIES = [
    # (label, rule, description)
    ("A_CURRENT",       rule_A_current,         "2-of-3 momentum + filters"),
    ("B_FEAR_REVERT",   rule_B_fear_revert,     "VIX spike receding + uptrend"),
    ("C_CHEAP_IV",      rule_C_cheap_iv,        "Low VIX (<16) + trend intact"),
    ("D_BREAKOUT",      rule_D_breakout,        "New 60-day high + low VIX"),
    ("E_OVERSOLD",      rule_E_oversold_uptrend,"RSI<35 in uptrend"),
    ("F_VIX_CRUSH",     rule_F_vix_crush,       "VIX dropped 30%+ in 10d"),
    ("G_GOLDEN_CROSS",  rule_G_golden_cross,    "SPY crosses 200DMA"),
    ("H_TREND_FOLLOW",  rule_H_trend_follow,    "SPY>50DMA>200DMA + MACD>0"),
    ("I_BB_SQUEEZE",    rule_I_bb_squeeze,      "BB width<20% + breakout"),
    ("J_DEEP_DD",       rule_J_deep_dd,         "DD -10 to -20 + recovery"),
    ("K_STRICT_CHEAP",  rule_K_strict_cheap,    "A AND C combined"),
    ("L_A_OR_SQUEEZE",  rule_L_squeeze_or_current, "A OR BB_SQUEEZE"),
    ("M_QUAL_BREAKOUT", rule_M_quality_breakout, "60d high + low VIX + uptrend"),
    ("N_FILTER_CURR",   rule_N_filter_current,  "A + extra VIX/momentum filters"),
    ("P_DEEP_SQUEEZE",  rule_P_deep_squeeze,    "BB<10% + VIX<18 + uptrend"),
    ("Q_TRIPLE_ALIGN",  rule_Q_triple_align,    "BB_SQUEEZE + CHEAP_IV + 60d high"),
    ("R_FEAR_RECOVERY", rule_R_fear_recovery,   "DD -10 to -25 + recovery"),
    ("S_PERFECT_SETUP", rule_S_perfect_setup,   "BB<15 + VIX<16 + RSI 40-65"),
    ("T_ANNUAL_ELITE",  rule_T_annual_elite,    "BB<15 + 60d high + VIX<17"),
]

OTM_LEVELS = [0.00, 0.10, 0.15]   # ATM, +10%, +15% OTM


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

    n = len(trades)
    wins = (trades["pct"] > 0).sum()
    years = (feats_period.index[-1] - feats_period.index[0]).days / 365.25
    return {
        "trades": n,
        "per_year": n / years,
        "win_rate": wins / n * 100,
        "avg_win": trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0,
        "avg_loss": trades[trades["pct"] <= 0]["pct"].mean() * 100 if (n - wins) else 0,
        "worst": trades["pct"].min() * 100,
        "best": trades["pct"].max() * 100,
        "avg_held": trades["held_days"].mean(),
        "avg_cost": trades["cost"].mean(),
        "after_tax": after_tax,
        "edge_aftertax": after_tax - voo_pure_after,
        "edge_per_year": (after_tax - voo_pure_after) / years,
        "final": result["total"],
        "deposited": result["deposited"],
        "trades_df": trades,
    }


def main():
    df = load_data()
    feats_full = extend_features(df)
    feats_period = feats_full.loc[START:END]
    years = (feats_period.index[-1] - feats_period.index[0]).days / 365.25

    voo_pure = voo_only_portfolio(feats_period, MONTHLY_SAVINGS)
    pure_pnl = voo_pure["total"] - voo_pure["deposited"]
    voo_pure_after = pure_pnl - max(0, pure_pnl) * TAX_LONG

    print("\n" + "═" * 130)
    print(f"  ALL STRATEGIES × OTM SWEEP  •  2-year LEAPS  •  Rotation model  "
          f"•  ${MONTHLY_SAVINGS:,}/mo")
    print(f"  Period: {START} → {END}    "
          f"Deposited: ${voo_pure['deposited']:,.0f}    "
          f"Pure VOO DCA after-tax: ${voo_pure_after:+,.0f}")
    print("═" * 130)

    results = {}
    combos = [(name, rule, otm) for (name, rule, _) in ALL_STRATEGIES for otm in OTM_LEVELS]
    print(f"\n  Running {len(combos)} strategy×OTM combos...\n")

    for name, rule, otm in tqdm(combos, ncols=80):
        trades = run_strategy_otm(df, rule, PER_LOT, otm,
                                  start_date=START, end_date=END)
        r = evaluate(trades, feats_period, voo_pure_after)
        results[(name, otm)] = r

    # ─── Build summary table for each OTM level ──────────────────────────────
    for otm in OTM_LEVELS:
        print(f"\n  ── ALL STRATEGIES AT {otm*100:+.0f}% OTM (2-yr LEAPS) ──")
        print(f"     {'Strategy':<17}  {'Trades/yr':>9}  {'Win%':>5}  "
              f"{'AvgWin':>7}  {'AvgLoss':>7}  {'Worst':>6}  {'AvgHeld':>7}  "
              f"{'AvgCost':>8}  {'After-tax':>12}  {'Edge vs VOO':>13}  {'$/yr':>8}")
        print("     " + "─" * 127)

        rows = []
        for name, _, _ in ALL_STRATEGIES:
            r = results[(name, otm)]
            if r is None:
                rows.append((name, None))
            else:
                rows.append((name, r))
        rows.sort(key=lambda x: -(x[1]["edge_aftertax"] if x[1] else -1e9))

        for name, r in rows:
            if r is None:
                print(f"     {name:<17}  {'(no fires)':>9}")
                continue
            verdict = "✅" if r["edge_aftertax"] > 0 else "❌"
            print(f"     {name:<17}  "
                  f"{r['per_year']:>4.1f}/yr   "
                  f"{r['win_rate']:>4.0f}%  "
                  f"{r['avg_win']:>+6.0f}%  "
                  f"{r['avg_loss']:>+6.0f}%  "
                  f"{r['worst']:>+5.0f}%  "
                  f"{r['avg_held']:>5.0f}d  "
                  f"${r['avg_cost']:>6,.0f}  "
                  f"${r['after_tax']:>+10,.0f}  "
                  f"${r['edge_aftertax']:>+11,.0f}  "
                  f"${r['edge_per_year']:>+6,.0f} {verdict}")

    # ─── Top 5 by edge ───────────────────────────────────────────────────────
    print("\n" + "═" * 130)
    print("  🏆 TOP 5 OVERALL (across all strategies × OTM levels)")
    print("═" * 130)
    all_combos = [
        (name, otm, r) for (name, otm), r in results.items()
        if r is not None
    ]
    all_combos.sort(key=lambda x: -x[2]["edge_aftertax"])

    print(f"\n  {'Rank':>4}  {'Strategy + Strike':<27}  {'Trades/yr':>9}  {'Win%':>5}  "
          f"{'Worst':>6}  {'After-tax':>12}  {'Edge vs VOO':>13}")
    print("  " + "─" * 95)
    for rank, (name, otm, r) in enumerate(all_combos[:10], 1):
        marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        print(f"  {marker} {rank}  {name + f' @ {otm*100:+.0f}% OTM':<27}  "
              f"{r['per_year']:>4.1f}/yr   "
              f"{r['win_rate']:>4.0f}%  "
              f"{r['worst']:>+5.0f}%  "
              f"${r['after_tax']:>+10,.0f}  "
              f"${r['edge_aftertax']:>+11,.0f}")

    # ─── High-frequency leaders (>=3 trades/yr) ──────────────────────────────
    print(f"\n  ── HIGH-FREQUENCY LEADERS (≥3 trades/yr) at +15% OTM ──")
    hifreq = sorted(
        [(name, r) for name, _, _ in ALL_STRATEGIES
         if results[(name, 0.15)] is not None and results[(name, 0.15)]["per_year"] >= 3],
        key=lambda x: -x[1]["edge_aftertax"]
    )
    for name, r in hifreq[:10]:
        print(f"     {name:<17}  {r['per_year']:>4.1f}/yr  "
              f"{r['win_rate']:>4.0f}% win  ${r['edge_aftertax']:>+10,.0f} edge")

    # ─── Chart: edge_per_year vs frequency, at +15% OTM ─────────────────────
    out = Path("results/strategies/all_strategies_otm15.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(13, 8))
    for name, _, _ in ALL_STRATEGIES:
        r = results[(name, 0.15)]
        if r is None:
            continue
        color = "#22cc55" if r["edge_aftertax"] > 0 else "#dd4444"
        size = 80 + abs(r["edge_aftertax"]) / 200
        ax.scatter(r["per_year"], r["edge_aftertax"], s=size, c=color,
                   alpha=0.7, edgecolor="white", linewidth=1.0)
        ax.annotate(name, xy=(r["per_year"], r["edge_aftertax"]),
                    xytext=(6, 6), textcoords="offset points",
                    fontsize=8, color="white")
    ax.axhline(0, color="#888888", linewidth=0.6, linestyle="--", alpha=0.6)
    ax.set_xlabel("Trades per year (frequency)")
    ax.set_ylabel("After-tax edge over VOO DCA ($)")
    ax.set_title("All Strategies @ +15% OTM 2-yr LEAPS  •  10-year backtest, rotation model",
                 fontsize=11)
    ax.grid(True, alpha=0.2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
    plt.tight_layout()
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    plt.close(fig)
    print(f"\n  💾 Scatter plot saved: {out}")


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
