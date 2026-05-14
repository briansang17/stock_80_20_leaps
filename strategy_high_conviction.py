"""
High-Conviction Rules — Only fire on "very obvious" setups (~1/year)
=====================================================================

Tests several rules designed to be EXTREMELY selective.  We sacrifice
trade frequency for setup quality.  Each rule must have at least
3-4 conditions all aligning simultaneously.

Rules tested:
  P_DEEP_SQUEEZE   : Tightest squeeze + low VIX + uptrend (BB <10%, VIX <18)
  Q_TRIPLE_ALIGN   : BB squeeze AND cheap IV AND breakout all on same day
  R_FEAR_RECOVERY  : Real correction occurred + now recovering (drawdown >10%)
  S_PERFECT_SETUP  : Multiple "best case" conditions: BB squeeze + super-low VIX
                     + RSI mid-range + SPY > 50DMA > 200DMA + recent calm
  T_ANNUAL_ELITE   : The strictest possible — combines everything

All use rotation model: cash never sits idle, drawn from VOO when signal fires.
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import load_data, bs_call, RISK_FREE_RATE, LEAPS_YEARS
from strategy_alternatives import (
    extend_features, run_strategy,
    rule_I_bb_squeeze, rule_C_cheap_iv, rule_D_breakout, rule_A_current,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG
from compare_rotation import rotation_portfolio, voo_only_portfolio

PER_LOT = 10_000
MONTHLY_SAVINGS = 2_500


# ─── HIGH-CONVICTION ENTRY RULES ─────────────────────────────────────────────

def rule_P_deep_squeeze(row, sigs_row) -> bool:
    """Deep BB squeeze (<10% percentile) + low VIX (<18) + uptrend."""
    return (
        row["bb_width_pct"] < 0.10 and          # tightest 10% of compression
        row["SPY"] >= row["bb_upper"] and       # breaking out
        bool(row["spy_above_200"]) and
        row["VIX"] < 18 and                     # really cheap options
        row["RSI14"] < 65
    )

def rule_Q_triple_align(row, sigs_row) -> bool:
    """BB squeeze + cheap IV + 60-day high all on same day."""
    return (
        rule_I_bb_squeeze(row, sigs_row) and
        rule_C_cheap_iv(row, sigs_row) and
        bool(row["is_new_high60"])
    )

def rule_R_fear_recovery(row, sigs_row) -> bool:
    """Buy after a real correction (10%+ drawdown), once recovery is confirmed."""
    return (
        row["drawdown"] <= -10 and              # was a real correction
        row["drawdown"] >= -25 and              # not a crash
        bool(row["spy_above_200"]) and          # uptrend reasserted
        bool(row["spy_above_50"]) and           # recovery confirmed
        row["VIX"] < 25 and
        row["vix_slope5"] < 0                   # VIX falling (fear receding)
    )

def rule_S_perfect_setup(row, sigs_row) -> bool:
    """Many things lined up: squeeze + super-low VIX + trend + 30d calm."""
    return (
        row["bb_width_pct"] < 0.15 and
        row["SPY"] >= row["bb_upper"] and
        bool(row["spy_above_50"]) and
        bool(row["spy_above_200"]) and
        row["sma50"] > row["sma200"] and
        row["VIX"] < 16 and
        row["vix_30d_mean"] < 18 and            # VIX been calm for a month
        40 <= row["RSI14"] <= 65
    )

def rule_T_annual_elite(row, sigs_row) -> bool:
    """Strictest possible — everything must align."""
    return (
        row["bb_width_pct"] < 0.15 and
        row["SPY"] >= row["bb_upper"] and
        bool(row["is_new_high60"]) and
        bool(row["spy_above_200"]) and
        row["sma50"] > row["sma200"] and
        row["VIX"] < 17 and
        row["vix_30d_mean"] < 19 and
        45 <= row["RSI14"] <= 65
    )


HIGH_CONV_STRATEGIES = {
    "P_DEEP_SQUEEZE":  rule_P_deep_squeeze,
    "Q_TRIPLE_ALIGN":  rule_Q_triple_align,
    "R_FEAR_RECOVERY": rule_R_fear_recovery,
    "S_PERFECT_SETUP": rule_S_perfect_setup,
    "T_ANNUAL_ELITE":  rule_T_annual_elite,
    "I_BB_SQUEEZE":    rule_I_bb_squeeze,   # baseline for comparison
    "A_CURRENT":       rule_A_current,
}

PERIODS = {
    "PAST 2 YEARS":  ("2024-05-13", "2026-05-13"),
    "PAST 10 YEARS": ("2016-05-13", "2026-05-13"),
}


def evaluate_strategy(df, rule, label, period_label, start, end, feats_period):
    trades_all = run_strategy(df, rule, PER_LOT)
    if trades_all.empty:
        return None
    trades = trades_all[
        (trades_all["entry_date"] >= start) & (trades_all["entry_date"] <= end)
    ].copy().reset_index(drop=True)
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0}

    wins = (trades["pct"] > 0).sum()
    avg_win  = trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0
    avg_loss = trades[trades["pct"] <= 0]["pct"].mean() * 100 if n - wins else 0
    worst    = trades["pct"].min() * 100
    avg_held = trades["held_days"].mean()
    years = (feats_period.index[-1] - feats_period.index[0]).days / 365.25

    # Rotation portfolio
    result = rotation_portfolio(trades, feats_period, MONTHLY_SAVINGS)
    leaps_tax = sum(
        (t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
        for t in trades.itertuples() if t.proceeds > t.cost
    )
    voo_basis = result["deposited"] + result["leaps_proceeds"]
    voo_gain = result["voo_value"] - voo_basis
    voo_tax = max(0, voo_gain) * TAX_LONG

    after_tax_profit = result["total"] - result["deposited"] - leaps_tax - voo_tax

    return {
        "label": label,
        "trades": n,
        "per_year": n / years,
        "wins": wins,
        "win_rate": wins / n * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "worst": worst,
        "avg_held": avg_held,
        "after_tax_profit": after_tax_profit,
        "total": result["total"],
        "deposited": result["deposited"],
    }


def main():
    df = load_data()
    feats_full = extend_features(df)

    print("\n" + "═" * 124)
    print("  HIGH-CONVICTION RULES  •  ROTATION MODEL  •  $2,500/mo savings  •  Cash never idle")
    print("═" * 124)

    for period_label, (start, end) in PERIODS.items():
        feats = feats_full.loc[start:end]
        voo = voo_only_portfolio(feats, MONTHLY_SAVINGS)
        voo_pnl = voo["total"] - voo["deposited"]
        voo_after = voo_pnl - max(0, voo_pnl) * TAX_LONG

        print(f"\n  ── {period_label}  ({start} → {end}) ──")
        print(f"     Pure VOO benchmark: ${voo_after:,.0f} after-tax profit on ${voo['deposited']:,.0f}")
        print(f"\n     {'Strategy':18}  {'Trades':>14}  {'Win%':>5}  {'AvgW':>6}  "
              f"{'AvgL':>6}  {'Worst':>7}  {'AvgHeld':>7}  {'After-tax':>12}  {'Edge vs VOO':>12}")
        print(f"     {'─' * 109}")

        # rank strategies by edge
        results = []
        for label, rule in HIGH_CONV_STRATEGIES.items():
            r = evaluate_strategy(df, rule, label, period_label, start, end, feats)
            if r is not None and r["trades"] > 0:
                r["edge"] = r["after_tax_profit"] - voo_after
                results.append(r)
            elif r is not None:
                results.append({"label": label, "trades": 0, "edge": -999_999})

        results.sort(key=lambda r: -r.get("edge", -1e9))

        for r in results:
            if r["trades"] == 0:
                print(f"     {r['label']:18}  {'no fires':>14}")
                continue
            verdict = "✅" if r["edge"] > 0 else "❌"
            print(f"     {r['label']:18}  "
                  f"{r['trades']:>3} ({r['per_year']:>3.1f}/yr)  "
                  f"{r['win_rate']:>4.0f}%  "
                  f"{r['avg_win']:>+5.0f}%  "
                  f"{r['avg_loss']:>+5.0f}%  "
                  f"{r['worst']:>+6.0f}%  "
                  f"{r['avg_held']:>5.0f}d  "
                  f"${r['after_tax_profit']:>+10,.0f}  "
                  f"${r['edge']:>+10,.0f} {verdict}")

    print("\n  " + "═" * 122)
    print("  Interpretation:")
    print("    • AvgHeld < 365 days = all gains taxed at 32% short-term")
    print("    • Edge > 0           = strategy beats pure VOO monthly DCA after tax")
    print("    • Worst              = single worst trade % return (your downside)")
    print("    • For ~1/yr target, look at rules firing 0.5-1.5 trades/year")
    print("  " + "═" * 122)


if __name__ == "__main__":
    main()
