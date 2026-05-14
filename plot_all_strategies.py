"""
Plot all winning LEAPS strategies vs VOO DCA
=============================================

Generates a chart for each strategy that beat pure VOO DCA after taxes
(10-year backtest, rotation model).  Output goes to results/strategies/.

For each strategy:
  - SPY price chart with entry/exit markers
  - Cumulative portfolio value vs VOO-only DCA (equity curves)

Plus a final master comparison chart with all strategies overlaid.
"""

from __future__ import annotations
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from tqdm import tqdm

from strategy_backtest import load_data
from strategy_alternatives import (
    extend_features, run_strategy,
    rule_I_bb_squeeze, rule_A_current,
)
from strategy_high_conviction import (
    rule_P_deep_squeeze, rule_Q_triple_align, rule_S_perfect_setup,
    rule_T_annual_elite, rule_R_fear_recovery,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG
from compare_rotation import rotation_portfolio, voo_only_portfolio

PER_LOT = 10_000
MONTHLY_SAVINGS = 2_500
OUTDIR = Path("results/strategies")

START = "2016-05-13"
END   = "2026-05-13"

STRATEGIES = [
    ("01_BB_SQUEEZE",      rule_I_bb_squeeze,    "BB_SQUEEZE  (all fires)",
     "BB width < 20%  ∧  SPY ≥ upper band  ∧  SPY > 200DMA  ∧  VIX < 22"),
    ("02_A_CURRENT",       rule_A_current,       "A_CURRENT  (2-of-3 momentum)",
     "MACD cross  ∨  RSI cross 50  ∨  50DMA reclaim  ∧  SPY > 200DMA  ∧  VIX < 28"),
    ("03_Q_TRIPLE_ALIGN",  rule_Q_triple_align,  "Q_TRIPLE_ALIGN",
     "BB_SQUEEZE  ∧  CHEAP_IV (VIX<16)  ∧  60-day high all fire same day"),
    ("04_T_ANNUAL_ELITE",  rule_T_annual_elite,  "T_ANNUAL_ELITE",
     "BB width <15%  ∧  60-day high  ∧  VIX <17  ∧  VIX30d <19  ∧  golden cross"),
    ("05_P_DEEP_SQUEEZE",  rule_P_deep_squeeze,  "P_DEEP_SQUEEZE",
     "BB width <10%  ∧  SPY ≥ upper band  ∧  SPY > 200DMA  ∧  VIX < 18  ∧  RSI < 65"),
    ("06_S_PERFECT_SETUP", rule_S_perfect_setup, "S_PERFECT_SETUP",
     "BB width <15%  ∧  golden cross  ∧  VIX <16  ∧  VIX30d <18  ∧  RSI 40-65"),
    ("07_R_FEAR_RECOVERY", rule_R_fear_recovery, "R_FEAR_RECOVERY",
     "Drawdown -10% to -25%  ∧  SPY > 50DMA & 200DMA  ∧  VIX < 25  ∧  VIX falling"),
]


def build_equity_curve(trades: pd.DataFrame, feats: pd.DataFrame, monthly: float):
    """Day-by-day cumulative value: VOO holdings + open LEAPS mark-to-market."""
    if "first_of_month" not in feats.columns:
        feats = feats.copy()
        feats["month"] = feats.index.to_period("M")
        feats["first_of_month"] = feats["month"] != feats["month"].shift(1)

    dates = feats.index
    voo_shares = 0.0
    cash = 0.0
    deposited = 0.0
    open_lots = []  # list of (trade_idx, exit_date)
    curve = []

    trades_sorted = trades.sort_values("entry_date").reset_index(drop=True)
    entry_lookup: dict = {}
    exit_lookup: dict = {}
    for i, t in trades_sorted.iterrows():
        entry_lookup.setdefault(pd.Timestamp(t.entry_date).normalize(), []).append(i)
        exit_lookup.setdefault(pd.Timestamp(t.exit_date).normalize(), []).append(i)

    spy = feats["SPY"]
    for date in dates:
        # Monthly deposit at start of month
        if feats.at[date, "first_of_month"]:
            cash += monthly
            deposited += monthly
            # Immediately buy VOO with cash
            voo_shares += cash / spy.at[date]
            cash = 0.0

        # Did any trades enter today? — sell VOO to fund each one
        if date.normalize() in entry_lookup:
            for idx in entry_lookup[date.normalize()]:
                t = trades_sorted.loc[idx]
                sell_voo_value = t["cost"]
                if voo_shares * spy.at[date] >= sell_voo_value:
                    voo_shares -= sell_voo_value / spy.at[date]
                else:
                    voo_shares = 0.0
                open_lots.append(idx)

        # Did any trades exit today? — buy back VOO with proceeds for each
        if date.normalize() in exit_lookup:
            for idx in exit_lookup[date.normalize()]:
                t = trades_sorted.loc[idx]
                voo_shares += t["proceeds"] / spy.at[date]
                if idx in open_lots:
                    open_lots.remove(idx)

        # Mark-to-market: VOO + open LEAPS (linear approximation)
        voo_value = voo_shares * spy.at[date]
        leaps_mtm = 0.0
        for idx in open_lots:
            t = trades_sorted.loc[idx]
            held = (date - pd.Timestamp(t["entry_date"])).days
            total = (pd.Timestamp(t["exit_date"]) - pd.Timestamp(t["entry_date"])).days
            if total > 0:
                progress = min(1.0, max(0.0, held / total))
            else:
                progress = 1.0
            leaps_mtm += t["cost"] + (t["proceeds"] - t["cost"]) * progress

        curve.append({
            "date": date,
            "total": voo_value + leaps_mtm,
            "voo": voo_value,
            "leaps": leaps_mtm,
            "deposited": deposited,
        })

    return pd.DataFrame(curve).set_index("date")


def voo_dca_curve(feats: pd.DataFrame, monthly: float):
    if "first_of_month" not in feats.columns:
        feats = feats.copy()
        feats["month"] = feats.index.to_period("M")
        feats["first_of_month"] = feats["month"] != feats["month"].shift(1)
    voo_shares, deposited, curve = 0.0, 0.0, []
    for date in feats.index:
        if feats.at[date, "first_of_month"]:
            voo_shares += monthly / feats.at[date, "SPY"]
            deposited += monthly
        curve.append({
            "date": date,
            "total": voo_shares * feats.at[date, "SPY"],
            "deposited": deposited,
        })
    return pd.DataFrame(curve).set_index("date")


def plot_strategy(key, rule, title, description, feats, voo_curve_full, df):
    feats_period = feats.loc[START:END]
    voo_curve = voo_curve_full.loc[START:END]

    trades = run_strategy(df, rule, PER_LOT)
    trades = trades[
        (trades["entry_date"] >= START) & (trades["entry_date"] <= END)
    ].copy().reset_index(drop=True)
    if trades.empty:
        return None

    curve = build_equity_curve(trades, feats_period, MONTHLY_SAVINGS)

    # Tax calc
    leaps_tax = sum(
        (t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
        for t in trades.itertuples() if t.proceeds > t.cost
    )
    final_total = curve["total"].iloc[-1]
    final_voo   = voo_curve["total"].iloc[-1]
    deposited   = curve["deposited"].iloc[-1]
    voo_gain    = final_voo - deposited
    voo_tax_only = max(0, voo_gain) * TAX_LONG
    # Need to allocate basis split for rotation model
    leaps_proceeds = sum(t.proceeds for t in trades.itertuples())
    leaps_cost = sum(t.cost for t in trades.itertuples())
    rotation_voo_basis = deposited + (leaps_proceeds - 0)  # proceeds added back
    rotation_voo_final = final_total - 0  # all in VOO if no open lots
    after_tax_strategy = final_total - deposited - leaps_tax - voo_tax_only
    after_tax_voo      = voo_gain - voo_tax_only

    edge_pretax  = final_total - final_voo
    edge_aftertax = after_tax_strategy - after_tax_voo

    wins = (trades["pct"] > 0).sum()
    win_rate = wins / len(trades) * 100
    avg_win = trades[trades["pct"] > 0]["pct"].mean() * 100
    avg_loss = trades[trades["pct"] <= 0]["pct"].mean() * 100 if (len(trades)-wins) else 0
    worst = trades["pct"].min() * 100

    # ── Build figure ─────────────────────────────────────────────────────────
    fig, axes = plt.subplots(
        2, 1, figsize=(15, 10), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.6]},
    )
    fig.suptitle(
        f"{title}  •  10-year rotation model vs pure VOO DCA\n"
        f"{description}",
        fontsize=11, y=0.995,
    )

    # ── Top: SPY chart with entries/exits ────────────────────────────────────
    ax = axes[0]
    ax.plot(feats_period.index, feats_period["SPY"], color="white", linewidth=0.85, label="SPY")
    ax.plot(feats_period.index, feats_period["sma200"], color="#cc8833", linewidth=0.7,
            linestyle="--", label="200DMA", alpha=0.7)

    for i, t in enumerate(trades.itertuples(), 1):
        ax.scatter(t.entry_date, t.entry_spy, color="#22cc55", s=85,
                   marker="^", zorder=9, edgecolor="white", linewidth=0.7)
        color = "#22cc55" if t.pct > 0 else "#dd4444"
        ax.scatter(t.exit_date, t.exit_spy, color=color, s=85,
                   marker="v", zorder=9, edgecolor="white", linewidth=0.7)
        ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                color=color, linewidth=1.0, alpha=0.5)

    ax.set_ylabel("SPY ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    stat_text = (
        f"Trades: {len(trades)} (~{len(trades)/10:.1f}/yr)   "
        f"Win rate: {win_rate:.0f}%   Avg win: {avg_win:+.0f}%   "
        f"Avg loss: {avg_loss:+.0f}%   Worst: {worst:+.0f}%"
    )
    ax.text(0.5, 0.02, stat_text, transform=ax.transAxes,
            fontsize=9, ha="center", color="white",
            bbox=dict(boxstyle="round,pad=0.4", fc="#222244", ec="white", alpha=0.85, lw=0.6))

    # ── Bottom: equity curves ────────────────────────────────────────────────
    ax2 = axes[1]
    ax2.plot(curve.index, curve["total"], color="#22cc55", linewidth=1.7,
             label=f"{title} (rotation)  ${final_total:,.0f}")
    ax2.plot(voo_curve.index, voo_curve["total"], color="#3399ff", linewidth=1.4,
             label=f"Pure VOO DCA  ${final_voo:,.0f}", alpha=0.85)
    ax2.plot(curve.index, curve["deposited"], color="#888888", linewidth=0.9,
             linestyle=":", label=f"Deposited  ${deposited:,.0f}", alpha=0.8)

    # Shade the edge area
    ax2.fill_between(curve.index, voo_curve["total"], curve["total"],
                     where=(curve["total"] >= voo_curve["total"]),
                     color="#22cc55", alpha=0.12,
                     label="LEAPS edge over VOO")
    ax2.fill_between(curve.index, voo_curve["total"], curve["total"],
                     where=(curve["total"] < voo_curve["total"]),
                     color="#dd4444", alpha=0.12, label="VOO winning")

    ax2.set_ylabel("Portfolio value ($)")
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    edge_text = (
        f"FINAL EDGE vs Pure VOO DCA  •  "
        f"Pre-tax: ${edge_pretax:+,.0f}   "
        f"After-tax: ${edge_aftertax:+,.0f}   "
        f"(strategy ${after_tax_strategy:+,.0f} vs VOO ${after_tax_voo:+,.0f})"
    )
    ax2.text(0.5, 0.02, edge_text, transform=ax2.transAxes,
             fontsize=9, ha="center", color="white",
             bbox=dict(boxstyle="round,pad=0.4", fc="#224422" if edge_aftertax > 0 else "#442222",
                       ec="white", alpha=0.9, lw=0.6))

    plt.tight_layout()
    out_path = OUTDIR / f"{key}.png"
    plt.savefig(out_path, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    plt.close(fig)

    return {
        "key": key, "title": title, "trades": len(trades),
        "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss, "worst": worst,
        "final": final_total, "voo_final": final_voo, "deposited": deposited,
        "edge_pretax": edge_pretax, "edge_aftertax": edge_aftertax,
        "after_tax_strategy": after_tax_strategy, "after_tax_voo": after_tax_voo,
        "curve": curve, "trades_df": trades,
    }


def plot_master_comparison(results, voo_curve, outdir):
    """One chart with all strategies' equity curves overlaid."""
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.suptitle("All Winning LEAPS Strategies vs Pure VOO DCA (10-year rotation model)",
                 fontsize=12, y=0.985)

    colors = ["#22cc55", "#ff8833", "#aa66ff", "#ffcc33",
              "#33ccff", "#ff6699", "#88dd44"]

    ax.plot(voo_curve.index, voo_curve["total"],
            color="#3399ff", linewidth=2.0, alpha=0.9,
            label=f"Pure VOO DCA  ${voo_curve['total'].iloc[-1]:,.0f}", zorder=5)

    for r, color in zip(results, colors):
        ax.plot(r["curve"].index, r["curve"]["total"],
                color=color, linewidth=1.3, alpha=0.85,
                label=f"{r['title']}  ${r['final']:,.0f}  "
                      f"(edge ${r['edge_aftertax']:+,.0f} after tax)")

    ax.plot(voo_curve.index, voo_curve["total"] * 0 + voo_curve["deposited"],
            color="#888888", linewidth=1.0, linestyle=":", alpha=0.7,
            label=f"Deposited  ${voo_curve['deposited'].iloc[-1]:,.0f}")

    ax.set_ylabel("Portfolio value")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.tight_layout()
    out = outdir / "00_master_comparison.png"
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    plt.close(fig)
    print(f"  💾 Saved master comparison: {out}")


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    df = load_data()
    feats = extend_features(df)
    feats["month"] = feats.index.to_period("M")
    feats["first_of_month"] = feats["month"] != feats["month"].shift(1)
    voo_curve_full = voo_dca_curve(feats, MONTHLY_SAVINGS)

    results = []
    print(f"\n  Generating {len(STRATEGIES)} strategy charts to {OUTDIR}/ ...")
    for key, rule, title, desc in tqdm(STRATEGIES, ncols=80):
        r = plot_strategy(key, rule, title, desc, feats, voo_curve_full, df)
        if r is not None:
            results.append(r)

    # Sort by after-tax edge for the summary
    results.sort(key=lambda r: -r["edge_aftertax"])

    voo_curve_p = voo_curve_full.loc[START:END]
    plot_master_comparison(results, voo_curve_p, OUTDIR)

    # ── Summary table ────────────────────────────────────────────────────────
    print("\n" + "═" * 130)
    print(f"  RESULTS  •  10-year rotation model (rotation_portfolio + monthly $2,500 VOO DCA)")
    print(f"  Period: {START} → {END}    Deposited: ${voo_curve_p['deposited'].iloc[-1]:,.0f}")
    print(f"  Pure VOO DCA final: ${voo_curve_p['total'].iloc[-1]:,.0f}  "
          f"(after-tax: ${(voo_curve_p['total'].iloc[-1] - voo_curve_p['deposited'].iloc[-1]) * (1 - TAX_LONG):+,.0f} profit)")
    print("═" * 130)
    print(f"\n  {'Rank':>4}  {'Strategy':<26}  {'Trades':>6}  {'Win%':>5}  "
          f"{'Final':>11}  {'Edge (pre)':>12}  {'Edge (after-tax)':>16}")
    print("  " + "─" * 110)
    for rank, r in enumerate(results, 1):
        marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        print(f"  {marker} {rank}  {r['title']:<26}  "
              f"{r['trades']:>6}  {r['win_rate']:>4.0f}%  "
              f"${r['final']:>9,.0f}  "
              f"${r['edge_pretax']:>+10,.0f}  "
              f"${r['edge_aftertax']:>+14,.0f}")

    print("\n  All charts saved to:", OUTDIR)
    print(f"    • 00_master_comparison.png       — all strategies on one plot")
    for r in results:
        print(f"    • {r['key']}.png   — {r['title']}")


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
