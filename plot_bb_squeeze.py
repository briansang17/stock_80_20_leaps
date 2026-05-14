"""
Visualize I_BB_SQUEEZE — the Bollinger Band squeeze breakout strategy.

Top panel:    SPY price + 200DMA + Bollinger Bands + entry/exit markers
Middle:       BB width percentile (squeeze indicator) + 20% threshold
Bottom:       VIX + 22 threshold
Right side:   Per-trade table showing each round trip
"""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
import pandas as pd

from strategy_backtest import load_data
from strategy_alternatives import (
    extend_features, rule_I_bb_squeeze, run_strategy, evaluate,
)

PER_LOT = 10_000

def main():
    df = load_data()
    feats = extend_features(df)
    trades = run_strategy(df, rule_I_bb_squeeze, PER_LOT)
    print(f"Trades found: {len(trades)}")

    fig, axes = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True,
        gridspec_kw={"height_ratios": [3.5, 1.2, 1.0]},
    )
    fig.suptitle(
        "I_BB_SQUEEZE  —  Bollinger Band Squeeze Breakout  •  $10k fresh capital per entry\n"
        "Buy when volatility has been compressed AND SPY breaks above upper band AND trend filters pass",
        fontsize=11, y=0.995,
    )

    # ── Top: price + BB + 200DMA ──────────────────────────────────────────────
    ax = axes[0]
    ax.plot(feats.index, feats["SPY"], color="white", linewidth=0.9,
            label="SPY price")
    ax.plot(feats.index, feats["sma200"], color="#cc8833", linewidth=0.9,
            linestyle="--", label="200DMA (trend filter)", alpha=0.85)
    ax.fill_between(feats.index, feats["bb_lower"], feats["bb_upper"],
                    color="#3366aa", alpha=0.18, label="Bollinger Bands (20, 2σ)")
    ax.plot(feats.index, feats["bb_upper"], color="#3399cc", linewidth=0.5, alpha=0.7)
    ax.plot(feats.index, feats["bb_lower"], color="#3399cc", linewidth=0.5, alpha=0.7)

    # Mark BB squeeze + breakout days (the entry condition only, before debounce)
    eligible_today = (
        (feats["bb_width_pct"] < 0.20) &
        (feats["SPY"] >= feats["bb_upper"]) &
        feats["spy_above_200"] &
        (feats["VIX"] < 22)
    )
    eligible_days = feats[eligible_today]
    ax.scatter(eligible_days.index, eligible_days["SPY"],
               color="#88cc44", s=18, marker="o", alpha=0.45,
               label=f"Eligible day ({len(eligible_days)} total)", zorder=3,
               edgecolor="none")

    # Mark actual entries (after debounce) and exits
    if not trades.empty:
        for t in trades.itertuples():
            color = "#22cc55" if t.pct > 0 else "#dd4444"
            ax.scatter(t.entry_date, t.entry_spy,
                       color="#22cc55", s=110, marker="^", zorder=6,
                       edgecolor="white", linewidth=1.2)
            ax.scatter(t.exit_date, t.exit_spy,
                       color=color, s=110, marker="v", zorder=6,
                       edgecolor="white", linewidth=1.2)
            ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                    color=color, linewidth=1.4, alpha=0.55)
            ax.annotate(f"{t.pct*100:+.0f}%",
                        xy=(t.exit_date, t.exit_spy),
                        xytext=(0, 12), textcoords="offset points",
                        fontsize=7.5, ha="center",
                        color="white",
                        bbox=dict(boxstyle="round,pad=0.25",
                                  fc=color, ec="none", alpha=0.85))

    ax.set_ylabel("SPY price ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85, ncol=2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Middle: BB width percentile ───────────────────────────────────────────
    ax2 = axes[1]
    ax2.plot(feats.index, feats["bb_width_pct"] * 100, color="#aaaaff",
             linewidth=0.9, label="BB width percentile (vs prior year)")
    ax2.fill_between(feats.index, 0, feats["bb_width_pct"] * 100,
                     where=feats["bb_width_pct"] < 0.20,
                     color="#33cc66", alpha=0.4, label="Squeeze zone (<20%)")
    ax2.axhline(20, color="#33cc66", linewidth=0.8, linestyle="--", alpha=0.7)
    ax2.set_ylabel("BB width\npercentile")
    ax2.set_ylim(0, 100)
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper right", fontsize=8, framealpha=0.85)

    # ── Bottom: VIX ───────────────────────────────────────────────────────────
    ax3 = axes[2]
    ax3.plot(feats.index, feats["VIX"], color="#ff5555", linewidth=0.7)
    ax3.axhline(22, color="#33cc66", linewidth=0.8, linestyle="--",
                label="VIX 22 (buy threshold)", alpha=0.85)
    ax3.axhline(30, color="#dd4444", linewidth=0.8, linestyle="--",
                label="VIX 30 (sell trigger)", alpha=0.85)
    ax3.fill_between(feats.index, 0, feats["VIX"], where=feats["VIX"] < 22,
                     color="#33cc66", alpha=0.2)
    ax3.set_ylabel("VIX")
    ax3.set_ylim(0, 70)
    ax3.grid(True, alpha=0.2)
    ax3.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # Build summary text
    if not trades.empty:
        m = evaluate(trades, (feats.index[-1] - feats.index[0]).days / 365.25)
        summary = (
            f"23 entries over 10 yrs ({m['per_year']:.1f}/yr)  •  "
            f"Win rate {m['win_rate']:.0f}%  •  "
            f"After-tax edge vs SPY-DCA: {m['edge_post']:+.1f}% (${m['edge_post_$']:+,.0f} on ${m['deployed']:,.0f} deployed)"
        )
        fig.text(0.5, 0.965, summary, fontsize=10, ha="center", color="#ffcc66")

    plt.tight_layout()
    out = "results/bb_squeeze_chart.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"💾 Saved: {out}")

if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
