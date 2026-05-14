"""
Detailed 2-year view of BB_SQUEEZE: every buy/sell + cumulative P&L vs VOO.

Shows three comparisons over the last 2 years (May 2024 – May 2026):
  1. BB_SQUEEZE LEAPS trades (actual strategy)
  2. SPY-DCA equivalent (same $ deployed on same dates as LEAPS)
  3. VOO buy-and-hold (lump sum at start of period, same $ as total deployed)

Top panel:    SPY price + BB + entry/exit markers + return labels
Bottom panel: cumulative profit lines (LEAPS vs SPY-DCA vs VOO buy-hold)
Side text:    per-trade table
"""

from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np

from strategy_backtest import load_data
from strategy_alternatives import (
    extend_features, rule_I_bb_squeeze, run_strategy,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG

PER_LOT = 10_000
START = "2024-05-13"
END   = "2026-05-13"


def main():
    df = load_data()
    feats_full = extend_features(df)

    # Run BB_SQUEEZE over full 10yr to keep features sane, then filter trades to the 2yr window
    trades_all = run_strategy(df, rule_I_bb_squeeze, PER_LOT)
    trades = trades_all[
        (trades_all["entry_date"] >= START) &
        (trades_all["entry_date"] <= END)
    ].copy().reset_index(drop=True)

    feats = feats_full.loc[START:END].copy()

    print(f"Trades in window {START} → {END}: {len(trades)}")
    if not trades.empty:
        for t in trades.itertuples():
            print(f"  {t.entry_date.date()} → {t.exit_date.date()}  "
                  f"{t.contracts}c  ${t.cost:>7,.0f} → ${t.proceeds:>7,.0f}  "
                  f"{t.pct*100:+5.1f}%   (vs SPY same dates: {(t.spy_value_at_exit/t.cost-1)*100:+5.1f}%)")

    # ── Build daily LEAPS portfolio P&L and SPY-DCA P&L within the window ────
    # For each day in window, sum: MTM of open lots + closed proceeds
    from strategy_backtest import bs_call, RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT
    from strategy_backtest import EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP

    daily_rows = []
    for date in feats.index:
        spy = float(feats.loc[date, "SPY"])
        sigma = float(feats.loc[date, "IV1Y_cal"]) if pd.notna(feats.loc[date, "IV1Y_cal"]) else float(feats.loc[date, "VIX"]) / 100
        spread = float(feats.loc[date, "spread"]) if pd.notna(feats.loc[date, "spread"]) else 0.04

        leaps_value = 0.0
        spy_value = 0.0
        cum_cost = 0.0

        for t in trades.itertuples():
            if t.entry_date <= date:
                cum_cost += t.cost
                spy_value += t.spy_shares * spy

                if t.exit_date <= date:
                    leaps_value += t.proceeds
                else:
                    T_rem = max(((t.entry_date + pd.Timedelta(days=int(LEAPS_YEARS*365))) - date).days / 365.25, 1e-6)
                    strike = round(t.entry_spy)
                    mark = bs_call(spy, strike, T_rem, RISK_FREE_RATE, sigma) * (1 - spread/2)
                    leaps_value += mark * 100 * t.contracts

        daily_rows.append({
            "date": date,
            "leaps_value": leaps_value,
            "spy_dca_value": spy_value,
            "cum_cost": cum_cost,
        })
    daily = pd.DataFrame(daily_rows).set_index("date")
    daily["leaps_pnl"] = daily["leaps_value"] - daily["cum_cost"]
    daily["spy_dca_pnl"] = daily["spy_dca_value"] - daily["cum_cost"]

    # VOO buy-and-hold: deploy TOTAL final cum_cost at START, hold
    total_deployed = trades["cost"].sum() if not trades.empty else PER_LOT
    spy_start = float(feats["SPY"].iloc[0])
    daily["voo_value"] = feats["SPY"] / spy_start * total_deployed
    daily["voo_pnl"] = daily["voo_value"] - total_deployed

    # ── Build figure ──────────────────────────────────────────────────────────
    fig, axes = plt.subplots(
        2, 1, figsize=(14, 9.5), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.6]},
    )

    period_pct_spy = (feats["SPY"].iloc[-1] / feats["SPY"].iloc[0] - 1) * 100
    fig.suptitle(
        f"BB_SQUEEZE — Past 2 Years (May 2024 → May 2026)  •  ${PER_LOT:,} per entry\n"
        f"SPY rose {period_pct_spy:+.1f}% over period  •  {len(trades)} LEAPS trades fired",
        fontsize=11, y=0.997,
    )

    # ── Top: SPY price + BB + entries/exits ──────────────────────────────────
    ax = axes[0]
    ax.plot(feats.index, feats["SPY"], color="white", linewidth=1.2, label="SPY")
    ax.plot(feats.index, feats["sma200"], color="#cc8833", linewidth=0.9,
            linestyle="--", label="200DMA", alpha=0.8)
    ax.fill_between(feats.index, feats["bb_lower"], feats["bb_upper"],
                    color="#3366aa", alpha=0.18, label="BB (20, 2σ)")
    ax.plot(feats.index, feats["bb_upper"], color="#3399cc", linewidth=0.5, alpha=0.6)
    ax.plot(feats.index, feats["bb_lower"], color="#3399cc", linewidth=0.5, alpha=0.6)

    if not trades.empty:
        for i, t in enumerate(trades.itertuples(), 1):
            color_buy = "#22cc55"
            color_sell = "#22cc55" if t.pct > 0 else "#dd4444"
            ax.scatter(t.entry_date, t.entry_spy, color=color_buy, s=160, marker="^",
                       zorder=8, edgecolor="white", linewidth=1.4)
            ax.scatter(t.exit_date, t.exit_spy, color=color_sell, s=160, marker="v",
                       zorder=8, edgecolor="white", linewidth=1.4)
            ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                    color=color_sell, linewidth=1.6, alpha=0.55)
            # Buy annotation
            ax.annotate(f"BUY #{i}\n{t.contracts}c @ ${t.entry_spy:.0f}",
                        xy=(t.entry_date, t.entry_spy),
                        xytext=(0, -36), textcoords="offset points",
                        fontsize=7.2, ha="center", color="white",
                        bbox=dict(boxstyle="round,pad=0.25",
                                  fc="#226633", ec="white", alpha=0.95, lw=0.6))
            # Sell annotation (above the line)
            if t.exit_reason == "(still open)":
                exit_label = f"OPEN\n{t.pct*100:+.0f}% MTM\n{t.exit_reason}"
            else:
                exit_label = f"SELL\n{t.pct*100:+.0f}%\n{t.exit_reason}"
            ax.annotate(exit_label,
                        xy=(t.exit_date, t.exit_spy),
                        xytext=(0, 26), textcoords="offset points",
                        fontsize=7.2, ha="center", color="white",
                        bbox=dict(boxstyle="round,pad=0.25",
                                  fc=color_sell, ec="white", alpha=0.95, lw=0.6))

    ax.set_ylabel("SPY price ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Bottom: cumulative profit comparison ─────────────────────────────────
    ax2 = axes[1]
    ax2.plot(daily.index, daily["leaps_pnl"], color="#22cc55", linewidth=2.0,
             label=f"LEAPS strategy P&L")
    ax2.plot(daily.index, daily["spy_dca_pnl"], color="#3399cc", linewidth=1.6,
             label=f"SPY-DCA (same $ on same dates)")
    ax2.plot(daily.index, daily["voo_pnl"], color="#ffcc66", linewidth=1.6,
             linestyle="--", label=f"VOO buy-and-hold (${total_deployed:,.0f} at start)")

    ax2.fill_between(daily.index, daily["voo_pnl"], daily["leaps_pnl"],
                     where=daily["leaps_pnl"] >= daily["voo_pnl"],
                     alpha=0.15, color="#22cc55", label="LEAPS beats VOO")
    ax2.fill_between(daily.index, daily["voo_pnl"], daily["leaps_pnl"],
                     where=daily["leaps_pnl"] < daily["voo_pnl"],
                     alpha=0.15, color="#dd4444", label="VOO beats LEAPS")

    # Mark each entry with vertical line
    if not trades.empty:
        for t in trades.itertuples():
            ax2.axvline(t.entry_date, color="#22cc55", alpha=0.18, linewidth=0.7)

    final_leaps = daily["leaps_pnl"].iloc[-1]
    final_spy = daily["spy_dca_pnl"].iloc[-1]
    final_voo = daily["voo_pnl"].iloc[-1]

    ax2.set_ylabel("Cumulative profit ($, pre-tax)")
    ax2.set_xlabel(
        f"Final P&L  →  LEAPS: ${final_leaps:+,.0f}   SPY-DCA: ${final_spy:+,.0f}   "
        f"VOO buy-hold (lump): ${final_voo:+,.0f}"
    )
    ax2.axhline(0, color="white", linewidth=0.6, alpha=0.4)
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.85, ncol=2)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:+,.0f}"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=20, ha="right")

    plt.tight_layout()
    out = "results/bb_squeeze_2yr.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"\n💾 Saved: {out}")

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "═" * 80)
    print(f"  PAST 2 YEARS  •  BB_SQUEEZE vs VOO  •  May 2024 → May 2026")
    print("═" * 80)
    if not trades.empty:
        n = len(trades)
        wins = (trades["pct"] > 0).sum()
        print(f"  Trades fired              : {n}  ({wins} wins, {n - wins} losers)")
        print(f"  Total capital deployed    : ${total_deployed:>10,.0f}")
        print(f"  LEAPS strategy P&L        : ${final_leaps:>+10,.0f}   "
              f"({final_leaps / total_deployed * 100:+.1f}% on deployed capital)")
        print(f"  SPY-DCA same dates P&L    : ${final_spy:>+10,.0f}   "
              f"({final_spy / total_deployed * 100:+.1f}%)")
        print(f"  VOO buy-hold (lump sum)   : ${final_voo:>+10,.0f}   "
              f"({final_voo / total_deployed * 100:+.1f}%)")
        print(f"  ─" * 30)
        print(f"  Edge over SPY-DCA         : ${final_leaps - final_spy:>+10,.0f}  "
              f"({(final_leaps - final_spy) / total_deployed * 100:+.1f}pp)")
        print(f"  Edge over VOO buy-hold    : ${final_leaps - final_voo:>+10,.0f}  "
              f"({(final_leaps - final_voo) / total_deployed * 100:+.1f}pp)")

        # After-tax
        leaps_tax = sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                        for t in trades.itertuples() if t.proceeds > t.cost)
        spy_pnl_tot = (trades["spy_value_at_exit"] - trades["cost"]).sum()
        spy_tax = max(0, spy_pnl_tot) * TAX_LONG
        voo_tax = max(0, final_voo) * TAX_LONG    # held >365d at end
        print(f"  ─" * 30)
        print(f"  After-tax  LEAPS P&L      : ${final_leaps - leaps_tax:>+10,.0f}")
        print(f"  After-tax  SPY-DCA P&L    : ${final_spy - spy_tax:>+10,.0f}")
        print(f"  After-tax  VOO buy-hold   : ${final_voo - voo_tax:>+10,.0f}")
    print("═" * 80)


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
