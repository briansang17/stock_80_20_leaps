"""Render an annotated equity / signals chart for the multi-lot STRICT variant."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
import pandas as pd

from strategy_backtest import load_data, add_features, signals_in_window, PROFILES
from strategy_backtest_multilot import run_backtest_multilot

CAPITAL = 20_000
PROFILE = "STRICT"
MAX_LOTS = 2

def main():
    df = load_data()
    eq, trades, _ = run_backtest_multilot(
        df, PROFILE, total_capital=CAPITAL, max_lots=MAX_LOTS,
    )
    feats = add_features(df)
    sigs = signals_in_window(feats, PROFILES[PROFILE]["cross_window"])

    spy_norm = feats["SPY"] / feats["SPY"].iloc[0] * CAPITAL

    fig, axes = plt.subplots(
        3, 1, figsize=(13, 9), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.4, 1.0]},
    )
    fig.suptitle(
        f"Strategy A — MULTI-LOT ({MAX_LOTS} concurrent)  •  ${CAPITAL:,} start  •  10-yr backtest\n"
        f"Stacks new lots on every 2-of-3 cross while gates pass • 14-day entry debounce",
        fontsize=12, y=0.995,
    )

    # ─── Top panel: equity vs SPY ─────────────────────────────────────────────
    ax = axes[0]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    spy_cagr = ((spy_norm.iloc[-1] / CAPITAL) ** (1 / years) - 1) * 100
    strat_cagr = ((eq["total"].iloc[-1] / CAPITAL) ** (1 / years) - 1) * 100

    ax.plot(spy_norm.index, spy_norm.values, color="#888", linewidth=1.4, linestyle="--",
            label=f"SPY buy-hold  CAGR {spy_cagr:+.1f}%")
    ax.plot(eq.index, eq["total"], color="#ff9933", linewidth=1.6,
            label=f"Strategy A x{MAX_LOTS}  CAGR {strat_cagr:+.1f}%")

    # Shade by number of open lots (intensity = number of lots)
    for n_lots in range(1, MAX_LOTS + 1):
        mask = eq["open_lots"] == n_lots
        ax.fill_between(eq.index, 0, eq["total"], where=mask,
                        alpha=0.08 + 0.10 * n_lots, color="#33cc66",
                        label=f"{n_lots} lot(s) open" if n_lots <= 2 else None)

    # Mark buys and sells
    if not trades.empty:
        for t in trades.itertuples():
            color = "#22cc55" if t.pct > 0 else "#dd4444"
            ax.scatter(t.entry_date, eq.loc[t.entry_date, "total"],
                       color="#33aa66", s=70, zorder=5, edgecolor="white", linewidth=0.8)
            ax.scatter(t.exit_date, eq.loc[t.exit_date, "total"],
                       color=color, s=70, zorder=5, edgecolor="white", linewidth=0.8,
                       marker="v")
            ax.annotate(f"{t.pct*100:+.0f}%",
                        xy=(t.exit_date, eq.loc[t.exit_date, "total"]),
                        xytext=(0, 8), textcoords="offset points",
                        fontsize=7, ha="center",
                        color="white",
                        bbox=dict(boxstyle="round,pad=0.2", fc=color, ec="none", alpha=0.85))

    ax.set_ylabel("Portfolio value ($)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    # ─── Middle panel: open lots over time ────────────────────────────────────
    ax2 = axes[1]
    ax2.fill_between(eq.index, 0, eq["open_lots"], step="post",
                     alpha=0.7, color="#22aa66")
    ax2.set_ylabel("# of open lots")
    ax2.set_ylim(-0.2, MAX_LOTS + 0.5)
    ax2.set_yticks(range(0, MAX_LOTS + 1))
    ax2.axhline(MAX_LOTS, color="#aa6633", linewidth=0.8, linestyle="--", alpha=0.6)
    ax2.grid(True, alpha=0.25)
    ax2.text(eq.index[5], MAX_LOTS + 0.1, f"max = {MAX_LOTS}",
             color="#aa6633", fontsize=8, va="bottom")

    # ─── Bottom panel: signal strength + gate pass ────────────────────────────
    ax3 = axes[2]
    gates_ok = (
        feats["spy_above_200"]
        & (feats["VIX"] < PROFILES[PROFILE]["vix_max_entry"])
        & (feats["RSI14"] < PROFILES[PROFILE]["rsi_max_entry"])
    )
    score = sigs["score"].fillna(0).astype(int)
    eligible = (score >= 2) & gates_ok

    colors = [
        "#33cc66" if (s >= 2 and g) else
        "#ffaa33" if s >= 1 else
        "#666666"
        for s, g in zip(score, gates_ok)
    ]
    ax3.bar(score.index, score, color=colors, width=1.0, alpha=0.85)
    ax3.set_ylabel("Signals 0-3")
    ax3.set_ylim(0, 3.5)
    ax3.set_yticks([0, 1, 2, 3])
    ax3.grid(True, alpha=0.2)
    legend = [
        Patch(facecolor="#33cc66", label="2+ signals & gates pass (eligible to BUY)"),
        Patch(facecolor="#ffaa33", label="1 signal (watching)"),
        Patch(facecolor="#666666", label="0 signals or gates fail"),
    ]
    ax3.legend(handles=legend, loc="upper left", fontsize=7, framealpha=0.85)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # Final formatting
    plt.tight_layout()
    out_path = f"results/multilot_strict_x{MAX_LOTS}.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out_path, dpi=130, bbox_inches="tight", facecolor="#1a1a1a",
                edgecolor="none")
    print(f"💾 Saved: {out_path}")

if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
