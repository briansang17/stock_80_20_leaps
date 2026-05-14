"""
Walk-Forward Validation
=======================

Splits the dataset into TRAIN (2016-2020) and TEST (2021-2026) periods
and runs the same strategy rules on both — separately.

If the strategy is real, both periods should show similar edge.
If only the TRAIN period works, the rules were overfit.

Also applies realistic tax drag (short-term vs long-term cap gains)
and SPY benchmark comparison for each period.

Usage:
    python walk_forward.py
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from strategy_backtest import (
    PROFILES, load_data, run_backtest, START_CAPITAL,
)

# ── Period split ──────────────────────────────────────────────────────────────
TRAIN_START = "2016-05-02"
TRAIN_END   = "2020-12-31"
TEST_START  = "2021-01-01"
TEST_END    = "2026-05-13"

# ── Tax assumptions (US federal, no state) ────────────────────────────────────
TAX_SHORT_TERM = 0.32   # ordinary income (~24-32% bracket)
TAX_LONG_TERM  = 0.20   # long-term capital gains (15-20%)

def apply_taxes(trades: pd.DataFrame, start_cap: float) -> tuple[float, float]:
    """Compute after-tax final value given the trade log."""
    if trades.empty:
        return start_cap, 0.0
    total_tax = 0.0
    cash = start_cap
    for t in trades.itertuples():
        gain = t.proceeds - t.cost
        if gain > 0:
            tax_rate = TAX_LONG_TERM if t.held_days >= 366 else TAX_SHORT_TERM
            tax = gain * tax_rate
            total_tax += tax
        cash += t.proceeds - t.cost
    cash_after_tax = start_cap + (cash - start_cap) - total_tax
    return cash_after_tax, total_tax

def spy_buy_hold(df: pd.DataFrame, start: str, end: str) -> float:
    """SPY buy-and-hold value for a sub-period with long-term cap gains tax."""
    p = df.loc[start:end, "SPY"]
    gross = (p.iloc[-1] / p.iloc[0]) * START_CAPITAL
    gain = gross - START_CAPITAL
    tax = max(0, gain) * TAX_LONG_TERM
    return gross, gross - tax

def metrics(eq: pd.DataFrame, trades: pd.DataFrame):
    final = eq["total"].iloc[-1]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = ((final / START_CAPITAL) ** (1 / years) - 1) * 100 if final > 0 else -100
    peak = eq["total"].cummax()
    maxdd = (eq["total"] / peak - 1).min() * 100
    n = len(trades); w = (trades["pct"] > 0).sum() if n else 0
    avg_loss = trades[trades["pct"] <= 0]["pct"].mean() * 100 if (n - w) else 0
    avg_win  = trades[trades["pct"] > 0]["pct"].mean()  * 100 if w else 0
    short_term = (trades["held_days"] < 366).sum() if n else 0
    return {
        "final": final, "cagr": cagr, "maxdd": maxdd, "trades": n,
        "wins": w, "avg_win": avg_win, "avg_loss": avg_loss,
        "years": years, "short_term": short_term,
    }

def run_period(df, profile, start, end, label):
    eq, trades, _ = run_backtest(df, profile, whole_contracts=True,
                                 start_date=start, end_date=end)
    m = metrics(eq, trades)
    after_tax, total_tax = apply_taxes(trades, START_CAPITAL)
    spy_gross, spy_after_tax = spy_buy_hold(df, start, end)
    after_tax_cagr = ((after_tax / START_CAPITAL) ** (1 / m["years"]) - 1) * 100 if after_tax > 0 else -100
    spy_after_tax_cagr = ((spy_after_tax / START_CAPITAL) ** (1 / m["years"]) - 1) * 100
    return {
        **m, "label": label, "profile": profile,
        "after_tax_final": after_tax, "after_tax_cagr": after_tax_cagr,
        "total_tax": total_tax,
        "spy_gross": spy_gross, "spy_after_tax": spy_after_tax,
        "spy_after_tax_cagr": spy_after_tax_cagr,
        "edge_vs_spy_pretax":  m["cagr"] - ((spy_gross/START_CAPITAL) ** (1/m["years"]) - 1) * 100,
        "edge_vs_spy_aftertax": after_tax_cagr - spy_after_tax_cagr,
    }

def print_row(r):
    print(f"  {r['label']:18s}  {r['profile']:11s}  "
          f"${r['final']:>9,.0f}  {r['cagr']:>+6.1f}%  "
          f"${r['after_tax_final']:>9,.0f}  {r['after_tax_cagr']:>+6.1f}%  "
          f"{r['trades']:>3}({r['short_term']}s)  "
          f"{r['wins']}/{r['trades']:<2}  "
          f"{r['maxdd']:>+6.1f}%  "
          f"${r['spy_after_tax']:>9,.0f}  {r['spy_after_tax_cagr']:>+5.1f}%  "
          f"{r['edge_vs_spy_aftertax']:>+5.1f}%")

def main():
    df = load_data()

    print("\n" + "═" * 130)
    print("  WALK-FORWARD VALIDATION  •  TRAIN (2016-2020) vs TEST (2021-2026)")
    print("  Includes commissions ($0.65/contract each side) and tax drag")
    print("═" * 130)
    print(f"  {'Period':18s}  {'Profile':11s}  {'Final pre':>10s}  {'CAGR':>7s}  "
          f"{'Final post':>10s}  {'CAGRpost':>8s}  {'T(st)':>5}  {'W/L':>5}  "
          f"{'DD':>7s}  {'SPY post':>10s}  {'SPYcagr':>7}  {'Edge':>6s}")
    print("  " + "─" * 130)

    rows = []
    for profile in ("STRICT", "BALANCED", "AGGRESSIVE"):
        train = run_period(df, profile, TRAIN_START, TRAIN_END, "Train 16-20")
        test  = run_period(df, profile, TEST_START,  TEST_END,  "Test  21-26")
        full  = run_period(df, profile, "2016-05-02", "2026-05-13", "Full  16-26")
        rows.extend([train, test, full])
        print_row(train); print_row(test); print_row(full)
        # check for overfit gap
        edge_gap = train["edge_vs_spy_aftertax"] - test["edge_vs_spy_aftertax"]
        verdict = (
            "✅ ROBUST   - test edge close to train edge" if abs(edge_gap) < 5
            else "⚠️ MODERATE - test edge weaker but still positive" if test["edge_vs_spy_aftertax"] > 0
            else "❌ OVERFIT  - test edge collapses or goes negative"
        )
        print(f"    Train edge: {train['edge_vs_spy_aftertax']:+.1f}pp  •  "
              f"Test edge: {test['edge_vs_spy_aftertax']:+.1f}pp  •  "
              f"Gap: {edge_gap:+.1f}pp  •  {verdict}")
        print()

    pd.DataFrame(rows).to_csv("results/walk_forward.csv", index=False)
    print(f"\n  💾 Saved: results/walk_forward.csv")

    print(f"\n  Assumptions used:")
    print(f"    • Commissions: ${0.65} per contract per side")
    print(f"    • Tax on short-term gains (<365d): {TAX_SHORT_TERM*100:.0f}% (ordinary income)")
    print(f"    • Tax on long-term gains  (≥365d): {TAX_LONG_TERM*100:.0f}% (LTCG)")
    print(f"    • SPY benchmark taxed at LTCG (single sale at end)")
    print(f"    • State taxes NOT included (add ~5-10% if applicable)")

if __name__ == "__main__":
    main()
