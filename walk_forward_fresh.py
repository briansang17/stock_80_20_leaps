"""
Walk-forward validation for the FRESH-CAPITAL variant.
Train (2016-2020) vs Test (2021-2026) vs Full (2016-2026).
Apples-to-apples vs SPY-DCA (same $ on same entry dates).
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import PROFILES, load_data
from strategy_fresh_capital import run_fresh, TAX_SHORT, TAX_LONG

TRAIN_START, TRAIN_END = "2016-05-02", "2020-12-31"
TEST_START,  TEST_END  = "2021-01-01", "2026-05-13"
FULL_START,  FULL_END  = "2016-05-02", "2026-05-13"

def metrics(trades, eq):
    if trades.empty:
        return None
    deployed = trades["cost"].sum()
    leaps_val = trades["proceeds"].sum()
    spy_val = trades["spy_value_at_exit"].sum()
    leaps_tax = sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                    for t in trades.itertuples() if t.proceeds > t.cost)
    spy_pnl = spy_val - deployed
    spy_tax = max(0, spy_pnl) * TAX_LONG
    leaps_post = leaps_val - leaps_tax
    spy_post = spy_val - spy_tax
    n = len(trades)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return {
        "trades": n, "years": years, "per_year": n / years,
        "wins": (trades["pct"] > 0).sum(),
        "deployed": deployed,
        "leaps_pre": leaps_val, "leaps_post": leaps_post,
        "spy_pre": spy_val, "spy_post": spy_post,
        "leaps_return_pre": (leaps_val / deployed - 1) * 100,
        "leaps_return_post": (leaps_post / deployed - 1) * 100,
        "spy_return_pre": (spy_val / deployed - 1) * 100,
        "spy_return_post": (spy_post / deployed - 1) * 100,
        "edge_pre": (leaps_val - spy_val) / deployed * 100,
        "edge_post": (leaps_post - spy_post) / deployed * 100,
    }

def print_row(label, profile, per_lot, m):
    if m is None:
        print(f"  {label:14s}  {profile:11s}  ${per_lot:>6,.0f}  (no trades)")
        return
    print(f"  {label:14s}  {profile:11s}  ${per_lot:>6,.0f}  "
          f"{m['trades']:>3} ({m['per_year']:>3.1f}/yr)  {m['wins']:>3}W  "
          f"${m['deployed']:>10,.0f}  "
          f"L:{m['leaps_return_post']:>+6.1f}%  S:{m['spy_return_post']:>+6.1f}%  "
          f"EDGE: {m['edge_post']:>+5.1f}%  "
          f"(${m['leaps_post']-m['spy_post']:>+8,.0f})")

def main():
    df = load_data()

    print("\n" + "═" * 138)
    print("  WALK-FORWARD  •  FRESH CAPITAL  •  apples-to-apples vs SPY-DCA on same dates")
    print("  Each entry deploys fresh $X — compares LEAPS round-trip vs same $ in SPY shares held until same exit date")
    print("═" * 138)
    print(f"\n  {'Period':14s}  {'Profile':11s}  {'$/lot':>7}  {'Trades':>12}  {'W':>4}  "
          f"{'Total $':>11}  {'LEAPS post':>11}  {'SPY post':>10}  {'EDGE%':>6}  {'EDGE $':>11}")
    print("  " + "─" * 136)

    all_rows = []
    for profile in ("STRICT", "BALANCED"):
        for per_lot in (10_000,):
            for label, s, e in [
                ("Train 16-20", TRAIN_START, TRAIN_END),
                ("Test  21-26", TEST_START,  TEST_END),
                ("Full  16-26", FULL_START,  FULL_END),
            ]:
                eq, trades = run_fresh(df, profile, per_lot, 14, s, e)
                m = metrics(trades, eq)
                print_row(label, profile, per_lot, m)
                if m:
                    all_rows.append({"period": label, "profile": profile, "per_lot": per_lot, **m})

            # verdict
            tr = next(r for r in all_rows if r["period"] == "Train 16-20" and r["profile"] == profile and r["per_lot"] == per_lot)
            te = next(r for r in all_rows if r["period"] == "Test  21-26" and r["profile"] == profile and r["per_lot"] == per_lot)
            gap = tr["edge_post"] - te["edge_post"]
            verdict = (
                "✅ ROBUST  - similar edge train & test" if abs(gap) < 5 and te["edge_post"] > 0
                else "✅ POSITIVE in test but weaker than train" if te["edge_post"] > 0
                else "⚠️ EDGE EVAPORATES in test"
            )
            print(f"    Train edge {tr['edge_post']:+.1f}%  •  Test edge {te['edge_post']:+.1f}%  •  "
                  f"Gap {gap:+.1f}pp  •  {verdict}\n")

    pd.DataFrame(all_rows).to_csv("results/walk_forward_fresh.csv", index=False)
    print("\n  💾 Saved: results/walk_forward_fresh.csv")
    print("\n  Interpretation:")
    print("    EDGE% = (LEAPS after-tax - SPY-DCA after-tax) / capital deployed")
    print("    Positive edge means: $1 of fresh capital does better as LEAPS than as SPY shares,")
    print("    measured on the SAME entry/exit dates (apples-to-apples).")

if __name__ == "__main__":
    main()
