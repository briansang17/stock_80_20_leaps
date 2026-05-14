"""
Fair 2-year comparison: Strategy A vs BB_SQUEEZE vs realistic VOO-DCA.

The user can't deploy a lump sum because they're earning the cash gradually,
so VOO buy-hold lump sum isn't a realistic alternative. Instead we compare to:
  • VOO DCA every $10k (matches their actual cash-flow constraint)
  • SPY-DCA on same dates as each LEAPS entry (the cleanest apples-to-apples)
"""

from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np

from strategy_backtest import (
    load_data, bs_call, RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
)
from strategy_alternatives import (
    extend_features, rule_I_bb_squeeze, rule_A_current, run_strategy,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG

PER_LOT = 10_000
START = "2024-05-13"
END   = "2026-05-13"
MONTHLY_DCA_DOLLARS = 2500   # $2,500/month into VOO = $60k over 2 years ≈ same total budget


def get_strategy_trades(df, rule, label):
    trades_all = run_strategy(df, rule, PER_LOT)
    trades = trades_all[
        (trades_all["entry_date"] >= START) &
        (trades_all["entry_date"] <= END)
    ].copy().reset_index(drop=True)
    print(f"\n{label}: {len(trades)} trades in window")
    if not trades.empty:
        for t in trades.itertuples():
            print(f"  {t.entry_date.date()} → {t.exit_date.date()}  "
                  f"{t.contracts}c  ${t.cost:>7,.0f} → ${t.proceeds:>7,.0f}  "
                  f"{t.pct*100:+5.1f}%   (vs SPY same dates: {(t.spy_value_at_exit/t.cost-1)*100:+5.1f}%)")
    return trades


def daily_leaps_value(trades, feats, date):
    """Compute MTM value of all LEAPS positions on a given date."""
    spy = float(feats.loc[date, "SPY"])
    sigma = float(feats.loc[date, "IV1Y_cal"]) if pd.notna(feats.loc[date, "IV1Y_cal"]) else float(feats.loc[date, "VIX"]) / 100
    spread = float(feats.loc[date, "spread"]) if pd.notna(feats.loc[date, "spread"]) else 0.04

    leaps_value = 0.0
    cost_so_far = 0.0
    spy_dca_value = 0.0

    for t in trades.itertuples():
        if t.entry_date > date:
            continue
        cost_so_far += t.cost
        spy_dca_value += t.spy_shares * spy

        if t.exit_date <= date:
            leaps_value += t.proceeds
        else:
            T_rem = max(
                ((t.entry_date + pd.Timedelta(days=int(LEAPS_YEARS * 365))) - date).days / 365.25,
                1e-6,
            )
            strike = round(t.entry_spy)
            mark = bs_call(spy, strike, T_rem, RISK_FREE_RATE, sigma) * (1 - spread / 2)
            leaps_value += mark * 100 * t.contracts

    return leaps_value, cost_so_far, spy_dca_value


def monthly_dca_voo(feats, monthly_dollars=MONTHLY_DCA_DOLLARS):
    """Deploy fixed $X into VOO on the first trading day of each month."""
    shares = 0.0
    deployed = 0.0
    deployments = []
    last_month = None
    for date in feats.index:
        m = (date.year, date.month)
        if last_month != m:
            spy = float(feats.loc[date, "SPY"])
            shares += monthly_dollars / spy
            deployed += monthly_dollars
            deployments.append((date, spy, monthly_dollars))
            last_month = m
    return shares, deployed, deployments


def build_daily_pnl(trades, feats, label):
    rows = []
    for date in feats.index:
        spy = float(feats.loc[date, "SPY"])
        leaps_val, cost, spy_dca_val = daily_leaps_value(trades, feats, date)
        rows.append({
            "date": date,
            f"{label}_pnl":     leaps_val - cost,
            f"{label}_cost":    cost,
            f"{label}_spy_dca_pnl": spy_dca_val - cost,
        })
    return pd.DataFrame(rows).set_index("date")


def main():
    df = load_data()
    feats_full = extend_features(df)
    feats = feats_full.loc[START:END].copy()

    # ─── Strategy A trades ────────────────────────────────────────────────────
    trades_a = get_strategy_trades(df, rule_A_current, "Strategy A (momentum cross)")
    daily_a = build_daily_pnl(trades_a, feats, "A")

    # ─── BB_SQUEEZE trades ────────────────────────────────────────────────────
    trades_bb = get_strategy_trades(df, rule_I_bb_squeeze, "BB_SQUEEZE")
    daily_bb = build_daily_pnl(trades_bb, feats, "BB")

    # ─── Monthly VOO DCA benchmark ────────────────────────────────────────────
    shares, total_deployed_voo, deployments = monthly_dca_voo(feats)
    print(f"\nMonthly VOO DCA: {len(deployments)} purchases × ${MONTHLY_DCA_DOLLARS:,} = ${total_deployed_voo:,}")

    daily = daily_a.join(daily_bb)
    daily["voo_value"] = 0.0
    daily["voo_cost"] = 0.0
    cum_shares = 0.0
    cum_cost = 0.0
    last_month = None
    for date in feats.index:
        m = (date.year, date.month)
        if last_month != m:
            spy = float(feats.loc[date, "SPY"])
            cum_shares += MONTHLY_DCA_DOLLARS / spy
            cum_cost += MONTHLY_DCA_DOLLARS
            last_month = m
        daily.loc[date, "voo_value"] = cum_shares * float(feats.loc[date, "SPY"])
        daily.loc[date, "voo_cost"]  = cum_cost
    daily["voo_pnl"] = daily["voo_value"] - daily["voo_cost"]

    # ─── Build figure ─────────────────────────────────────────────────────────
    fig, axes = plt.subplots(
        2, 1, figsize=(14, 10), sharex=True,
        gridspec_kw={"height_ratios": [1.6, 2.0]},
    )

    fig.suptitle(
        "Past 2 Years (May 2024 → May 2026)  —  Realistic Cash-Flow Comparison\n"
        f"Strategy A  vs  BB_SQUEEZE  vs  VOO monthly DCA (${MONTHLY_DCA_DOLLARS:,}/mo, no lump-sum required)",
        fontsize=11, y=0.997,
    )

    # ── Top: SPY price + entries ─────────────────────────────────────────────
    ax = axes[0]
    ax.plot(feats.index, feats["SPY"], color="white", linewidth=1.0, label="SPY")
    ax.plot(feats.index, feats["sma200"], color="#cc8833", linewidth=0.9,
            linestyle="--", label="200DMA", alpha=0.75)

    # Strategy A entries
    if not trades_a.empty:
        for t in trades_a.itertuples():
            ax.scatter(t.entry_date, t.entry_spy,
                       color="#3399cc", s=140, marker="^",
                       zorder=8, edgecolor="white", linewidth=1.0)
            color = "#3399cc" if t.pct > 0 else "#dd4444"
            ax.scatter(t.exit_date, t.exit_spy,
                       color=color, s=140, marker="v",
                       zorder=8, edgecolor="white", linewidth=1.0)
            ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                    color=color, linewidth=1.2, alpha=0.4)
            ax.annotate(f"A: {t.pct*100:+.0f}%",
                        xy=(t.exit_date, t.exit_spy),
                        xytext=(0, -22), textcoords="offset points",
                        fontsize=7.0, ha="center", color="white",
                        bbox=dict(boxstyle="round,pad=0.2", fc="#225588", ec="none", alpha=0.85))

    # BB_SQUEEZE entries
    if not trades_bb.empty:
        for t in trades_bb.itertuples():
            ax.scatter(t.entry_date, t.entry_spy,
                       color="#22cc55", s=140, marker="^",
                       zorder=9, edgecolor="white", linewidth=1.0)
            color = "#22cc55" if t.pct > 0 else "#dd4444"
            ax.scatter(t.exit_date, t.exit_spy,
                       color=color, s=140, marker="v",
                       zorder=9, edgecolor="white", linewidth=1.0)
            ax.plot([t.entry_date, t.exit_date], [t.entry_spy, t.exit_spy],
                    color=color, linewidth=1.2, alpha=0.4)
            ax.annotate(f"BB: {t.pct*100:+.0f}%",
                        xy=(t.exit_date, t.exit_spy),
                        xytext=(0, 16), textcoords="offset points",
                        fontsize=7.0, ha="center", color="white",
                        bbox=dict(boxstyle="round,pad=0.2", fc=color, ec="none", alpha=0.85))

    # Monthly VOO DCA markers
    for d, p, _ in deployments:
        ax.axvline(d, color="#ffcc66", alpha=0.15, linewidth=0.6)

    ax.set_ylabel("SPY price ($)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    # ── Bottom: cumulative P&L per strategy ──────────────────────────────────
    ax2 = axes[1]
    ax2.plot(daily.index, daily["A_pnl"], color="#3399cc", linewidth=1.8,
             label=f"Strategy A (LEAPS) — {len(trades_a)} trades")
    ax2.plot(daily.index, daily["BB_pnl"], color="#22cc55", linewidth=1.8,
             label=f"BB_SQUEEZE (LEAPS) — {len(trades_bb)} trades")
    ax2.plot(daily.index, daily["voo_pnl"], color="#ffcc66", linewidth=1.8,
             linestyle="--",
             label=f"VOO monthly DCA — ${MONTHLY_DCA_DOLLARS:,}/mo ({len(deployments)} buys)")

    ax2.axhline(0, color="white", linewidth=0.6, alpha=0.4)
    ax2.grid(True, alpha=0.2)
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.85)
    ax2.set_ylabel("Cumulative profit ($, pre-tax)")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:+,.0f}"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=20, ha="right")

    final_a   = daily["A_pnl"].iloc[-1]
    final_bb  = daily["BB_pnl"].iloc[-1]
    final_voo = daily["voo_pnl"].iloc[-1]
    cost_a    = daily["A_cost"].iloc[-1]
    cost_bb   = daily["BB_cost"].iloc[-1]
    cost_voo  = daily["voo_cost"].iloc[-1]

    ax2.set_xlabel(
        f"Final P&L  →  A: ${final_a:+,.0f} on ${cost_a:,.0f} | "
        f"BB_SQUEEZE: ${final_bb:+,.0f} on ${cost_bb:,.0f} | "
        f"VOO DCA: ${final_voo:+,.0f} on ${cost_voo:,.0f}"
    )

    plt.tight_layout()
    out = "results/compare_2yr_fair.png"
    os.makedirs("results", exist_ok=True)
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor="#1a1a1a", edgecolor="none")
    print(f"\n💾 Saved: {out}")

    # ── Print full comparison table ──────────────────────────────────────────
    print("\n" + "═" * 96)
    print(f"  PAST 2 YEARS  •  REALISTIC CASH-FLOW COMPARISON  •  May 2024 → May 2026")
    print("═" * 96)
    print(f"\n  {'Strategy':30s}  {'Deployed':>10}  {'P&L':>10}  {'Return':>8}  "
          f"{'After-tax P&L':>14}  {'After-tax %':>11}")
    print("  " + "─" * 94)

    def show(label, deployed, pnl, trades=None):
        if trades is not None and not trades.empty:
            tax = sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                      for t in trades.itertuples() if t.proceeds > t.cost)
        else:
            tax = max(0, pnl) * TAX_LONG
        after_tax = pnl - tax
        ret_pct = pnl / deployed * 100 if deployed else 0
        after_pct = after_tax / deployed * 100 if deployed else 0
        print(f"  {label:30s}  ${deployed:>9,.0f}  ${pnl:>+9,.0f}  {ret_pct:>+7.1f}%  "
              f"${after_tax:>+13,.0f}  {after_pct:>+10.1f}%")

    show("Strategy A (LEAPS)",   cost_a,   final_a,   trades_a)
    show("BB_SQUEEZE (LEAPS)",   cost_bb,  final_bb,  trades_bb)
    show("VOO monthly DCA",      cost_voo, final_voo)

    # also show SPY-DCA same dates (the head-to-head for each strategy)
    spy_dca_a  = daily["A_spy_dca_pnl"].iloc[-1]
    spy_dca_bb = daily["BB_spy_dca_pnl"].iloc[-1]
    print("  " + "─" * 94)
    show("SPY-DCA on A's dates",  cost_a,  spy_dca_a)
    show("SPY-DCA on BB's dates", cost_bb, spy_dca_bb)
    print("═" * 96)

    print(f"\n  HEAD-TO-HEAD (pre-tax):")
    print(f"    Strategy A   vs SPY on same dates  : ${final_a - spy_dca_a:>+10,.0f}  "
          f"({(final_a - spy_dca_a)/cost_a*100:+.1f}pp)")
    print(f"    BB_SQUEEZE   vs SPY on same dates  : ${final_bb - spy_dca_bb:>+10,.0f}  "
          f"({(final_bb - spy_dca_bb)/cost_bb*100:+.1f}pp)")
    print(f"\n  VS VOO MONTHLY-DCA (pre-tax):")
    print(f"    Strategy A    vs VOO monthly DCA    : ${final_a - final_voo:>+10,.0f}  ")
    print(f"    BB_SQUEEZE    vs VOO monthly DCA    : ${final_bb - final_voo:>+10,.0f}  ")


if __name__ == "__main__":
    plt.style.use("dark_background")
    main()
