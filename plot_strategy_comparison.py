"""Comparison chart: all 14 strategies, train vs test after-tax edge."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

def main():
    df = pd.read_csv("results/strategy_comparison.csv")

    test_df = df[df["period"].str.startswith("TEST")].set_index("strategy")
    train_df = df[df["period"].str.startswith("TRAIN")].set_index("strategy")
    test_df = test_df.sort_values("edge_post", ascending=False)

    strategies = test_df.index.tolist()
    test_edge = test_df["edge_post"].values
    train_edge = train_df.loc[strategies, "edge_post"].values
    test_trades = test_df["trades"].values
    test_winrate = test_df["win_rate"].values
    test_per_year = test_df["per_year"].values

    fig, axes = plt.subplots(2, 1, figsize=(13, 9.5),
                              gridspec_kw={"height_ratios": [1, 1]})
    fig.suptitle(
        "Strategy Comparison — 14 different entry rules tested\n"
        "After-tax edge vs SPY-DCA on same dates  •  $10k fresh capital per entry  •  same exit rules for all",
        fontsize=11, y=0.995,
    )

    # ── Top: Edge bar chart ───────────────────────────────────────────────────
    ax = axes[0]
    y = np.arange(len(strategies))
    width = 0.4
    bars_test = ax.barh(y - width/2, test_edge, width,
                         color=["#22cc55" if v > 0 else "#dd4444" for v in test_edge],
                         label="TEST 2021-2026 (held-out)", edgecolor="white", linewidth=0.4)
    bars_train = ax.barh(y + width/2, train_edge, width,
                          color=["#3399cc" if v > 0 else "#996633" for v in train_edge],
                          label="TRAIN 2016-2020", edgecolor="white", linewidth=0.4, alpha=0.85)
    ax.set_yticks(y)
    ax.set_yticklabels(strategies, fontsize=8)
    ax.invert_yaxis()
    ax.axvline(0, color="white", linewidth=0.6)
    ax.set_xlabel("After-tax edge vs SPY-DCA  (% of capital deployed)")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.2, axis="x")

    # Annotate edges
    for i, (te, tr) in enumerate(zip(test_edge, train_edge)):
        ax.text(te + (0.5 if te >= 0 else -0.5), i - width/2, f"{te:+.1f}%",
                va="center", ha="left" if te >= 0 else "right", fontsize=7.5,
                color="white")
        ax.text(tr + (0.5 if tr >= 0 else -0.5), i + width/2, f"{tr:+.1f}%",
                va="center", ha="left" if tr >= 0 else "right", fontsize=7.5,
                color="#ccccff", alpha=0.85)

    # ── Bottom: trade count and win rate scatter ──────────────────────────────
    ax2 = axes[1]
    bubble = ax2.scatter(test_per_year, test_winrate, s=test_trades * 14,
                          c=test_edge, cmap="RdYlGn", vmin=-15, vmax=25,
                          edgecolors="white", linewidth=0.8, alpha=0.85)
    for i, label in enumerate(strategies):
        ax2.annotate(label, (test_per_year[i], test_winrate[i]),
                     xytext=(6, 4), textcoords="offset points",
                     fontsize=7.5, color="white", alpha=0.9)
    cbar = plt.colorbar(bubble, ax=ax2, label="Test-period after-tax edge %")
    ax2.axvspan(1.5, 2.5, alpha=0.15, color="#33aaff",
                label="User's ~2/yr target zone")
    ax2.axhline(60, color="#888", linewidth=0.6, linestyle="--", alpha=0.6)
    ax2.set_xlabel("Trades per year (TEST period)")
    ax2.set_ylabel("Win rate (%, TEST period)")
    ax2.set_xlim(-0.5, 13)
    ax2.set_ylim(-5, 105)
    ax2.legend(loc="lower right", fontsize=9)
    ax2.grid(True, alpha=0.2)
    ax2.text(0.5, 102, "Bubble size = # of trades in test  •  Color = after-tax edge",
             fontsize=8, color="#aaaaaa", va="top")

    plt.tight_layout()
    os.makedirs("results", exist_ok=True)
    out = "results/strategy_comparison.png"
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"💾 Saved: {out}")

if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
