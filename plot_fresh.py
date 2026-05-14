"""Per-trade comparison: $10k in LEAPS vs $10k in SPY shares on the same date."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
import pandas as pd

from strategy_backtest import load_data
from strategy_fresh_capital import run_fresh, TAX_SHORT, TAX_LONG

PROFILE  = "STRICT"
PER_LOT  = 10_000

def main():
    df = load_data()
    eq, trades = run_fresh(df, PROFILE, PER_LOT)
    if trades.empty:
        print("no trades — nothing to plot")
        return

    fig, axes = plt.subplots(
        2, 1, figsize=(13, 8.5), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0]},
    )
    fig.suptitle(
        f"Strategy A — FRESH CAPITAL  •  {PROFILE}  •  ${PER_LOT:,}/entry\n"
        f"Per-trade head-to-head: LEAPS round-trip vs SPY shares deployed on same date",
        fontsize=11, y=0.995,
    )

    # ─── Top: per-trade $ PnL bars ────────────────────────────────────────────
    ax = axes[0]
    trades = trades.sort_values("entry_date").reset_index(drop=True)
    x = trades["entry_date"]
    bar_width_days = 60

    leaps_pnl = trades["proceeds"] - trades["cost"]
    spy_pnl = trades["spy_value_at_exit"] - trades["cost"]
    x_num = mdates.date2num(x)

    ax.bar(x_num - bar_width_days * 0.35, leaps_pnl, width=bar_width_days * 0.6,
           color=["#22cc55" if v > 0 else "#dd4444" for v in leaps_pnl],
           edgecolor="white", linewidth=0.4, label="LEAPS $PnL")
    ax.bar(x_num + bar_width_days * 0.35, spy_pnl, width=bar_width_days * 0.6,
           color=["#3399cc" if v > 0 else "#996633" for v in spy_pnl],
           edgecolor="white", linewidth=0.4, label="SPY shares $PnL", alpha=0.85)

    ax.axhline(0, color="white", linewidth=0.6, alpha=0.4)
    ax.set_ylabel(f"$ Profit per trade  (${PER_LOT:,} deployed)")
    ax.grid(True, alpha=0.2, axis="y")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:+,.0f}"))
    ax.xaxis_date()

    # Annotate per-trade edge (LEAPS - SPY) above each pair
    for xn, lpnl, spnl in zip(x_num, leaps_pnl, spy_pnl):
        diff = lpnl - spnl
        top = max(lpnl, spnl)
        ax.text(xn, top + max(abs(top) * 0.06, 200), f"{diff:+,.0f}",
                ha="center", fontsize=6.5, color="#cccc66")

    # ─── Bottom: cumulative outperformance over time ──────────────────────────
    ax2 = axes[1]
    cum_leaps_pnl = leaps_pnl.cumsum()
    cum_spy_pnl = spy_pnl.cumsum()
    cum_edge = cum_leaps_pnl - cum_spy_pnl
    # Use trade exit dates for the "realized" curve (more honest)
    exits = trades["exit_date"]
    ax2.plot(exits, cum_leaps_pnl, color="#22cc55", linewidth=1.4,
             marker="o", markersize=3.5, label="Cumulative LEAPS $PnL")
    ax2.plot(exits, cum_spy_pnl, color="#3399cc", linewidth=1.4,
             marker="o", markersize=3.5, label="Cumulative SPY-DCA $PnL")
    ax2.fill_between(exits, cum_spy_pnl, cum_leaps_pnl,
                     where=cum_leaps_pnl >= cum_spy_pnl,
                     alpha=0.18, color="#22cc55", label="LEAPS edge zone")
    ax2.fill_between(exits, cum_spy_pnl, cum_leaps_pnl,
                     where=cum_leaps_pnl < cum_spy_pnl,
                     alpha=0.18, color="#dd4444", label="SPY edge zone")

    deployed = trades["cost"].sum()
    final_edge = cum_edge.iloc[-1]
    final_pct = final_edge / deployed * 100

    # After-tax numbers
    leaps_tax = sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                    for t in trades.itertuples() if t.proceeds > t.cost)
    spy_pnl_total = (trades["spy_value_at_exit"] - trades["cost"]).sum()
    spy_tax = max(0, spy_pnl_total) * TAX_LONG
    leaps_after = trades["proceeds"].sum() - leaps_tax
    spy_after = trades["spy_value_at_exit"].sum() - spy_tax
    edge_after = leaps_after - spy_after
    edge_after_pct = edge_after / deployed * 100

    ax2.set_ylabel("Cumulative $ profit (pre-tax)")
    ax2.set_xlabel(
        f"Trade exit date  •  ${deployed:,.0f} total deployed  •  "
        f"Pre-tax edge ${final_edge:+,.0f} ({final_pct:+.1f}%)  •  "
        f"After-tax edge ${edge_after:+,.0f} ({edge_after_pct:+.1f}%)"
    )
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:+,.0f}"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.tight_layout()
    out = f"results/fresh_{PROFILE.lower()}_${PER_LOT}_pertrade.png"
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"💾 Saved: {out}")

if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
