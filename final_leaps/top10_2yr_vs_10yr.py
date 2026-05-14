"""
Top 10 strategies — past 2 years vs past 10 years (+15% OTM 2-yr LEAPS)

Side-by-side comparison to see which strategies are consistent winners
across both timeframes, and which only worked in one period.
"""

from __future__ import annotations
import pandas as pd
from tqdm import tqdm

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
OTM_PCT = 0.15

PERIODS = {
    "PAST 2 YEARS  (2024-05 → 2026-05)":  ("2024-05-13", "2026-05-13"),
    "PAST 10 YEARS (2016-05 → 2026-05)":  ("2016-05-13", "2026-05-13"),
}

STRATEGIES = [
    ("A_CURRENT",       rule_A_current),
    ("B_FEAR_REVERT",   rule_B_fear_revert),
    ("C_CHEAP_IV",      rule_C_cheap_iv),
    ("D_BREAKOUT",      rule_D_breakout),
    ("E_OVERSOLD",      rule_E_oversold_uptrend),
    ("F_VIX_CRUSH",     rule_F_vix_crush),
    ("G_GOLDEN_CROSS",  rule_G_golden_cross),
    ("H_TREND_FOLLOW",  rule_H_trend_follow),
    ("I_BB_SQUEEZE",    rule_I_bb_squeeze),
    ("J_DEEP_DD",       rule_J_deep_dd),
    ("K_STRICT_CHEAP",  rule_K_strict_cheap),
    ("L_A_OR_SQUEEZE",  rule_L_squeeze_or_current),
    ("M_QUAL_BREAKOUT", rule_M_quality_breakout),
    ("N_FILTER_CURR",   rule_N_filter_current),
    ("P_DEEP_SQUEEZE",  rule_P_deep_squeeze),
    ("Q_TRIPLE_ALIGN",  rule_Q_triple_align),
    ("R_FEAR_RECOVERY", rule_R_fear_recovery),
    ("S_PERFECT_SETUP", rule_S_perfect_setup),
    ("T_ANNUAL_ELITE",  rule_T_annual_elite),
]


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
    years = (feats_period.index[-1] - feats_period.index[0]).days / 365.25

    n = len(trades)
    wins = (trades["pct"] > 0).sum()
    return {
        "trades": n,
        "per_year": n / years,
        "win_rate": wins / n * 100,
        "avg_win": trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0,
        "avg_loss": trades[trades["pct"] <= 0]["pct"].mean() * 100 if (n - wins) else 0,
        "worst": trades["pct"].min() * 100,
        "after_tax": after_tax,
        "edge_aftertax": after_tax - voo_pure_after,
        "edge_per_year": (after_tax - voo_pure_after) / years,
        "deposited": result["deposited"],
        "final": result["total"],
    }


def main():
    df = load_data()
    feats_full = extend_features(df)

    all_results = {}

    for period_label, (start, end) in PERIODS.items():
        feats_period = feats_full.loc[start:end]
        voo_pure = voo_only_portfolio(feats_period, MONTHLY_SAVINGS)
        pure_pnl = voo_pure["total"] - voo_pure["deposited"]
        voo_pure_after = pure_pnl - max(0, pure_pnl) * TAX_LONG
        all_results[period_label] = {
            "voo_pure": voo_pure,
            "voo_pure_after": voo_pure_after,
            "results": {},
        }

        for name, rule in tqdm(STRATEGIES, desc=period_label[:25], ncols=80):
            trades = run_strategy_otm(df, rule, PER_LOT, OTM_PCT,
                                      start_date=start, end_date=end)
            r = evaluate(trades, feats_period, voo_pure_after)
            all_results[period_label]["results"][name] = r

    # ── Print summary tables ─────────────────────────────────────────────────
    print("\n" + "═" * 130)
    print(f"  TOP 10 STRATEGIES — +15% OTM 2-yr LEAPS, ROTATION MODEL ($2,500/mo VOO DCA)")
    print(f"  Strategies ranked by after-tax edge over pure VOO DCA")
    print("═" * 130)

    for period_label, data in all_results.items():
        results = data["results"]
        voo_pure = data["voo_pure"]
        voo_pure_after = data["voo_pure_after"]

        valid = [(name, r) for name, r in results.items() if r is not None]
        valid.sort(key=lambda x: -x[1]["edge_aftertax"])
        top10 = valid[:10]

        print(f"\n  ── {period_label} ──")
        print(f"     Pure VOO DCA: deposited ${voo_pure['deposited']:,.0f}, "
              f"final ${voo_pure['total']:,.0f}, "
              f"after-tax profit ${voo_pure_after:+,.0f}")
        print(f"\n     {'Rank':>4}  {'Strategy':<17}  {'Trades/yr':>9}  {'Win%':>5}  "
              f"{'AvgWin':>7}  {'Worst':>6}  {'After-tax':>12}  {'Edge vs VOO':>12}  {'$/yr':>8}")
        print("     " + "─" * 110)
        for rank, (name, r) in enumerate(top10, 1):
            marker = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
            print(f"     {marker} {rank}  {name:<17}  "
                  f"{r['per_year']:>4.1f}/yr   "
                  f"{r['win_rate']:>4.0f}%  "
                  f"{r['avg_win']:>+6.0f}%  "
                  f"{r['worst']:>+5.0f}%  "
                  f"${r['after_tax']:>+10,.0f}  "
                  f"${r['edge_aftertax']:>+10,.0f}  "
                  f"${r['edge_per_year']:>+6,.0f}")

    # ── Side-by-side rank comparison ────────────────────────────────────────
    print("\n" + "═" * 130)
    print("  CONSISTENCY CHECK — How rank changes from 10-yr to 2-yr")
    print("═" * 130)

    p2 = "PAST 2 YEARS  (2024-05 → 2026-05)"
    p10 = "PAST 10 YEARS (2016-05 → 2026-05)"
    r2  = all_results[p2]["results"]
    r10 = all_results[p10]["results"]

    valid10 = sorted([(n, r) for n, r in r10.items() if r], key=lambda x: -x[1]["edge_aftertax"])
    rank10 = {name: i+1 for i, (name, _) in enumerate(valid10)}
    valid2  = sorted([(n, r) for n, r in r2.items() if r], key=lambda x: -x[1]["edge_aftertax"])
    rank2   = {name: i+1 for i, (name, _) in enumerate(valid2)}

    print(f"\n  {'Strategy':<17}  "
          f"{'2yr rank':>8}  {'2yr edge':>11}  {'2yr/yr':>7}   "
          f"{'10yr rank':>9}  {'10yr edge':>11}  {'10yr/yr':>8}   {'Consistent':<10}")
    print("  " + "─" * 105)

    rows = []
    for name in [n for n, _ in valid10]:
        r2_r = r2.get(name)
        r10_r = r10.get(name)
        if r10_r is None:
            continue
        rk2 = rank2.get(name, "—")
        rk10 = rank10.get(name, "—")
        edge2 = r2_r["edge_aftertax"] if r2_r else 0
        edge10 = r10_r["edge_aftertax"]
        per2 = r2_r["per_year"] if r2_r else 0
        per10 = r10_r["per_year"]
        # Consistent if both top-10 and both positive edge
        top_both = isinstance(rk2, int) and rk2 <= 10 and isinstance(rk10, int) and rk10 <= 10
        positive_both = edge2 > 0 and edge10 > 0
        if top_both and positive_both:
            consistency = "⭐ TOP 10 BOTH"
        elif positive_both:
            consistency = "✅ both pos."
        elif edge10 > 0:
            consistency = "⚠️ 10yr only"
        elif edge2 > 0:
            consistency = "⚠️ 2yr only"
        else:
            consistency = "❌ both neg."

        rows.append((name, rk2, edge2, per2, rk10, edge10, per10, consistency))

    for name, rk2, edge2, per2, rk10, edge10, per10, consist in rows:
        print(f"  {name:<17}  "
              f"{'#'+str(rk2) if isinstance(rk2, int) else '—':>8}  "
              f"${edge2:>+9,.0f}  {per2:>4.1f}/yr   "
              f"{'#'+str(rk10) if isinstance(rk10, int) else '—':>9}  "
              f"${edge10:>+9,.0f}  {per10:>4.1f}/yr    "
              f"{consist}")

    # ── BEST: strategies that beat VOO in BOTH periods ──────────────────────
    print("\n  🏆 STRATEGIES THAT WON IN BOTH PERIODS:")
    winners_both = [r for r in rows if "TOP 10 BOTH" in r[-1] or "both pos." in r[-1]]
    winners_both.sort(key=lambda x: -(x[2] + x[5] / 5))  # weight 10yr 5x more
    for name, rk2, edge2, per2, rk10, edge10, per10, _ in winners_both[:10]:
        print(f"     {name:<17}  "
              f"2yr: ${edge2:>+8,.0f} (#{rk2}) | "
              f"10yr: ${edge10:>+8,.0f} (#{rk10})")


if __name__ == "__main__":
    main()
