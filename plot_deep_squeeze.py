"""Visualize P_DEEP_SQUEEZE — the "once a year, very obvious" rule."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
import pandas as pd

from strategy_backtest import load_data
from strategy_alternatives import extend_features, run_strategy
from strategy_high_conviction import rule_P_deep_squeeze

PER_LOT = 10_000


def main():
    df = load_data()
    feats = extend_features(df)
    trades = run_strategy(df, rule_P_deep_squeeze, PER_LOT)
    print(f"P_DEEP_SQUEEZE fires found: {len(trades)}")
    if not trades.empty:
        for i, t in enumerate(trades.itertuples(), 1):
            print(f"  #{i}  {t.entry_date.date()} → {t.exit_date.date()}  "
                  f"{t.contracts}c  ${t.cost:>6,.0f} → ${t.proceeds:>6,.0f}  "
                  f"{t.pct*100:+5.1f}%  (held {t.held_days}d)  "
                  f"vs SPY same dates: {(t.spy_value_at_exit/t.cost-1)*100:+5.1f}%")

    fig, axes = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True,
        gridspec_kw={"height_ratios": [3.5, 1.2, 1.0]},
    )
    fig.suptitle(
        "P_DEEP_SQUEEZE — 'Once a Year, Very Obvious' Rule  •  100% Win Rate (7/7) over 10 years\n"
        "BB width < 10th %ile  ∧  SPY ≥ upper band  ∧  SPY > 200DMA  ∧  VIX < 18  ∧  RSI < 65",
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

    # Eligible days (rule conditions met today)
    eligible = (
        (feats["bb_width_pct"] < 0.10) &
        (feats["SPY"] >= feats["bb_upper"]) &
        feats["spy_above_200"] &
        (feats["VIX"] < 18) &
        (feats["RSI14"] < 65)
    )
    elig_days = feats[eligible]
    ax.scatter(elig_days.index, elig_days["SPY"], color="#88cc44",
               s=20, alpha=0.5, label=f"Eligible day ({len(elig_days)})",
               zorder=3, edgecolor="none")

    for i, t in enumerate(trades.itertuples(), 1):
        ax.scatter(t.entry_date, t.entry_spy, color="#22cc55", s=170,
                   marker="^", zorder=9, edgecolor="white", linewidth=1.3)
        color = "#22cc55" if t.pct > 0 else "#dd4444"
        ax.scatter(t.exit_date, t.exit_spy, color=color, s=170,
                   marker="v", zorder=9, edgecolor="white", linewidth=1.3)
        ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                color=color, linewidth=1.6, alpha=0.5)
        ax.annotate(f"#{i}\nBUY @ ${t.entry_spy:.0f}",
                    xy=(t.entry_date, t.entry_spy),
                    xytext=(0, -32), textcoords="offset points",
                    fontsize=7.0, ha="center", color="white",
                    bbox=dict(boxstyle="round,pad=0.25", fc="#226633", ec="white",
                              alpha=0.95, lw=0.6))
        ax.annotate(f"SELL +{t.pct*100:.0f}%",
                    xy=(t.exit_date, t.exit_spy),
                    xytext=(0, 22), textcoords="offset points",
                    fontsize=7.5, ha="center", color="white",
                    bbox=dict(boxstyle="round,pad=0.25", fc=color, ec="white",
                              alpha=0.95, lw=0.6))

    ax.set_ylabel("SPY ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=8.5, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Middle: BB width percentile + 10% threshold ──────────────────────────
    ax2 = axes[1]
    ax2.plot(feats.index, feats["bb_width_pct"] * 100, color="#aaaaff",
             linewidth=0.8, label="BB width percentile")
    ax2.fill_between(feats.index, 0, feats["bb_width_pct"] * 100,
                     where=feats["bb_width_pct"] < 0.10,
                     color="#22cc55", alpha=0.5,
                     label="Deep squeeze (<10%)")
    ax2.axhline(10, color="#22cc55", linewidth=0.8, linestyle="--", alpha=0.8)
    ax2.set_ylabel("BB width\npercentile")
    ax2.set_ylim(0, 100)
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper right", fontsize=8, framealpha=0.85)

    # ── Bottom: VIX with 18 threshold ────────────────────────────────────────
    ax3 = axes[2]
    ax3.plot(feats.index, feats["VIX"], color="#ff5555", linewidth=0.6)
    ax3.fill_between(feats.index, 0, feats["VIX"], where=feats["VIX"] < 18,
                     color="#22cc55", alpha=0.25)
    ax3.axhline(18, color="#22cc55", linewidth=0.8, linestyle="--",
                label="VIX 18 (buy threshold)", alpha=0.85)
    ax3.axhline(30, color="#dd4444", linewidth=0.8, linestyle="--",
                label="VIX 30 (sell trigger)", alpha=0.85)
    ax3.set_ylabel("VIX")
    ax3.set_ylim(0, 70)
    ax3.grid(True, alpha=0.2)
    ax3.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.tight_layout()
    out = "results/deep_squeeze_chart.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"\n💾 Saved: {out}")


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
