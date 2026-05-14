"""Visualize BB_SQUEEZE (the "all fires" version) over 10 years."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from strategy_backtest import load_data
from strategy_alternatives import extend_features, run_strategy, rule_I_bb_squeeze

PER_LOT = 10_000


def main():
    df = load_data()
    feats = extend_features(df)
    trades = run_strategy(df, rule_I_bb_squeeze, PER_LOT)
    print(f"\nBB_SQUEEZE fires found: {len(trades)}\n")

    if not trades.empty:
        print(f"  {'#':>3}  {'Entry':>10}  {'Exit':>10}  {'Cost':>8}  {'Proceeds':>8}  "
              f"{'LEAPS %':>8}  {'SPY %':>7}  {'Held':>5}  {'Exit reason':<22}")
        print("  " + "─" * 100)
        for i, t in enumerate(trades.itertuples(), 1):
            spy_pct = (t.spy_value_at_exit / t.cost - 1) * 100
            verdict = "✅" if t.pct > 0 else "❌"
            print(f"  {i:>3}  {t.entry_date.date()}  {t.exit_date.date()}  "
                  f"${t.cost:>6,.0f}  ${t.proceeds:>6,.0f}  "
                  f"{t.pct*100:>+6.1f}%  {spy_pct:>+5.1f}%  "
                  f"{t.held_days:>4}d  {t.exit_reason:<20}  {verdict}")

        wins = trades[trades["pct"] > 0]
        losses = trades[trades["pct"] <= 0]
        print(f"\n  Summary:")
        print(f"    Total fires      : {len(trades)} (over {(feats.index[-1]-feats.index[0]).days/365.25:.1f} years)")
        print(f"    Win rate         : {len(wins)}/{len(trades)} = {len(wins)/len(trades)*100:.0f}%")
        print(f"    Avg win          : {wins['pct'].mean()*100:+.1f}%")
        if len(losses):
            print(f"    Avg loss         : {losses['pct'].mean()*100:+.1f}%")
            print(f"    Worst loss       : {losses['pct'].min()*100:+.1f}%")
        print(f"    Avg hold period  : {trades['held_days'].mean():.0f} days")

    fig, axes = plt.subplots(
        3, 1, figsize=(15, 11), sharex=True,
        gridspec_kw={"height_ratios": [3.5, 1.2, 1.0]},
    )
    fig.suptitle(
        f"BB_SQUEEZE (all fires)  •  10-year chart  •  {len(trades)} trades total\n"
        "BB width < 20th %ile  ∧  SPY ≥ upper band  ∧  SPY > 200DMA  ∧  VIX < 22",
        fontsize=11, y=0.997,
    )

    # ── Top: SPY with BB and trades ──────────────────────────────────────────
    ax = axes[0]
    ax.plot(feats.index, feats["SPY"], color="white", linewidth=0.9, label="SPY")
    ax.plot(feats.index, feats["sma200"], color="#cc8833", linewidth=0.9,
            linestyle="--", label="200DMA", alpha=0.75)
    ax.fill_between(feats.index, feats["bb_lower"], feats["bb_upper"],
                    color="#3366aa", alpha=0.15, label="Bollinger Bands")
    ax.plot(feats.index, feats["bb_upper"], color="#3399cc", linewidth=0.4, alpha=0.6)
    ax.plot(feats.index, feats["bb_lower"], color="#3399cc", linewidth=0.4, alpha=0.6)

    for i, t in enumerate(trades.itertuples(), 1):
        ax.scatter(t.entry_date, t.entry_spy, color="#22cc55", s=130,
                   marker="^", zorder=9, edgecolor="white", linewidth=1.0)
        color = "#22cc55" if t.pct > 0 else "#dd4444"
        ax.scatter(t.exit_date, t.exit_spy, color=color, s=130,
                   marker="v", zorder=9, edgecolor="white", linewidth=1.0)
        ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                color=color, linewidth=1.2, alpha=0.45)
        ax.annotate(f"#{i} {t.pct*100:+.0f}%",
                    xy=(t.entry_date, t.entry_spy),
                    xytext=(0, -22), textcoords="offset points",
                    fontsize=6.5, ha="center", color="white",
                    bbox=dict(boxstyle="round,pad=0.2", fc=color, ec="white",
                              alpha=0.9, lw=0.5))

    ax.set_ylabel("SPY ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Middle: BB width percentile + 20% threshold ──────────────────────────
    ax2 = axes[1]
    ax2.plot(feats.index, feats["bb_width_pct"] * 100, color="#aaaaff",
             linewidth=0.7, label="BB width percentile")
    ax2.fill_between(feats.index, 0, feats["bb_width_pct"] * 100,
                     where=feats["bb_width_pct"] < 0.20,
                     color="#22cc55", alpha=0.4,
                     label="Squeeze zone (<20%)")
    ax2.axhline(20, color="#22cc55", linewidth=0.8, linestyle="--", alpha=0.8)
    ax2.set_ylabel("BB width\npercentile")
    ax2.set_ylim(0, 100)
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper right", fontsize=8, framealpha=0.85)

    # ── Bottom: VIX with 22 threshold ────────────────────────────────────────
    ax3 = axes[2]
    ax3.plot(feats.index, feats["VIX"], color="#ff5555", linewidth=0.6)
    ax3.fill_between(feats.index, 0, feats["VIX"], where=feats["VIX"] < 22,
                     color="#22cc55", alpha=0.2)
    ax3.axhline(22, color="#22cc55", linewidth=0.8, linestyle="--",
                label="VIX 22 (buy threshold)", alpha=0.85)
    ax3.axhline(30, color="#dd4444", linewidth=0.8, linestyle="--",
                label="VIX 30 (sell trigger)", alpha=0.85)
    ax3.set_ylabel("VIX")
    ax3.set_ylim(0, 70)
    ax3.grid(True, alpha=0.2)
    ax3.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.tight_layout()
    out = "results/bb_squeeze_10yr.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"\n  💾 Saved: {out}")


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
