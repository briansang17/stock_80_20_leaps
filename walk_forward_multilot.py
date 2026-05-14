"""
Walk-forward validation for the MULTI-LOT variant.
Train (2016-2020) vs Test (2021-2026) vs Full (2016-2026).

Tests whether stacking concurrent positions on "strong buy" days actually
improves robustness — or just amplifies the same overfit edge.
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import PROFILES
from strategy_backtest_multilot import run_backtest_multilot

TRAIN_START, TRAIN_END = "2016-05-02", "2020-12-31"
TEST_START,  TEST_END  = "2021-01-01", "2026-05-13"
FULL_START,  FULL_END  = "2016-05-02", "2026-05-13"

TAX_SHORT, TAX_LONG = 0.32, 0.20

def apply_taxes(trades_df, start_cap):
    if trades_df.empty:
        return start_cap, 0.0
    tax = 0.0
    pnl = 0.0
    for t in trades_df.itertuples():
        gain = t.proceeds - t.cost
        pnl += gain
        if gain > 0:
            tax += gain * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
    return start_cap + pnl - tax, tax

def spy_after_tax(df, start, end, cap):
    p = df["SPY"].loc[start:end]
    gross = (p.iloc[-1] / p.iloc[0]) * cap
    return gross - max(0, gross - cap) * TAX_LONG

def cagr(final, cap, days):
    years = days / 365.25
    return ((final / cap) ** (1 / years) - 1) * 100 if final > 0 else -100

def run_period(df, profile, start, end, capital, max_lots):
    eq, trades, dbg = run_backtest_multilot(
        df, profile, total_capital=capital, max_lots=max_lots,
        start_date=start, end_date=end,
    )
    days = (eq.index[-1] - eq.index[0]).days
    pre = eq["total"].iloc[-1]
    pre_cagr = cagr(pre, capital, days)
    after, tax = apply_taxes(trades, capital)
    after_cagr = cagr(after, capital, days)
    spy = spy_after_tax(df, start, end, capital)
    spy_cagr = cagr(spy, capital, days)
    peak = eq["total"].cummax()
    maxdd = (eq["total"] / peak - 1).min() * 100
    n = len(trades)
    short_term = (trades["held_days"] < 366).sum() if n else 0
    return {
        "profile": profile, "max_lots": max_lots, "capital": capital,
        "start": start, "end": end, "days": days, "trades": n, "short_term": short_term,
        "wins": (trades["pct"] > 0).sum() if n else 0,
        "final_pre": pre, "cagr_pre": pre_cagr,
        "final_post": after, "cagr_post": after_cagr,
        "tax_paid": tax, "max_dd": maxdd,
        "spy_post": spy, "spy_cagr_post": spy_cagr,
        "edge_post": after_cagr - spy_cagr,
        "avg_concurrent": eq["open_lots"].mean(),
        "max_concurrent": eq["open_lots"].max(),
        "pct_invested": (eq["open_lots"] > 0).mean() * 100,
        "skipped_full": dbg["skipped_full_lots"],
    }

def print_table(rows):
    print(f"\n  {'Period':12s}  {'Profile':11s}  {'Lots':>4}  "
          f"{'Final pre':>10s}  {'CAGR':>6s}  {'Final post':>10s}  {'CAGRpost':>8s}  "
          f"{'T':>3}  {'W':>3}  {'DD':>7s}  {'SPYpost':>9}  {'SPYcagr':>7}  {'Edge':>6s}  "
          f"{'Avg':>5s}  {'Max':>3s}  {'%Inv':>5s}")
    print("  " + "─" * 145)

def print_row(r, period_label):
    print(f"  {period_label:12s}  {r['profile']:11s}  {r['max_lots']:>4}  "
          f"${r['final_pre']:>9,.0f}  {r['cagr_pre']:>+5.1f}%  "
          f"${r['final_post']:>9,.0f}  {r['cagr_post']:>+7.1f}%  "
          f"{r['trades']:>3}  {r['wins']:>3}  {r['max_dd']:>+6.1f}%  "
          f"${r['spy_post']:>8,.0f}  {r['spy_cagr_post']:>+5.1f}%  "
          f"{r['edge_post']:>+5.1f}%  "
          f"{r['avg_concurrent']:>4.2f}  {r['max_concurrent']:>3}  {r['pct_invested']:>4.0f}%")

def main():
    from strategy_backtest import load_data
    df = load_data()

    print("\n" + "═" * 145)
    print("  WALK-FORWARD VALIDATION  •  MULTI-LOT VARIANT  •  $20k capital, up to N concurrent positions")
    print("  Same signals as single-lot Strategy A.  Each lot uses ~$5k.  14-day entry debounce.")
    print("═" * 145)

    all_rows = []
    for profile in ("STRICT", "BALANCED", "AGGRESSIVE"):
        for max_lots in (1, 2, 4):
            print_table([])  # header
            train = run_period(df, profile, TRAIN_START, TRAIN_END, 20_000, max_lots)
            test  = run_period(df, profile, TEST_START,  TEST_END,  20_000, max_lots)
            full  = run_period(df, profile, FULL_START,  FULL_END,  20_000, max_lots)
            for r, lab in [(train, "Train 16-20"), (test, "Test  21-26"), (full, "Full  16-26")]:
                print_row(r, lab)
                all_rows.append({**r, "period": lab})

            edge_gap = train["edge_post"] - test["edge_post"]
            verdict = (
                "✅ ROBUST"   if abs(edge_gap) < 5
                else "⚠️ WEAKER" if test["edge_post"] > 0
                else "❌ OVERFIT"
            )
            print(f"    Train edge {train['edge_post']:+.1f}pp  •  "
                  f"Test edge {test['edge_post']:+.1f}pp  •  "
                  f"Gap {edge_gap:+.1f}pp  •  {verdict}\n")

    pd.DataFrame(all_rows).to_csv("results/walk_forward_multilot.csv", index=False)
    print(f"\n  💾 Saved: results/walk_forward_multilot.csv\n")

    print("  Key questions:")
    print("    1. Does multi-lot improve the TEST period vs single-lot?")
    print("    2. Does the edge gap narrow (less overfit)?")
    print("    3. Or does it just deploy more capital into the same bad trades?")

if __name__ == "__main__":
    main()
