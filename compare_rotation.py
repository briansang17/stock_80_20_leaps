"""
ROTATION MODEL — LEAPS uses money ONLY when signal fires, drawn from VOO position.

Setup:
  • $2,500/mo arrives → bought as VOO shares immediately
  • LEAPS signal fires → SELL VOO worth $10k → buy 2yr ATM SPY call
  • LEAPS exits → proceeds → BUY VOO with full amount
  • Result: cash never sits in HYSA earning low yield

This is the fairest comparison for LEAPS because it eliminates ALL cash-drag.
The only question becomes: does LEAPS leverage during signal periods outperform
SPY shares for the same dollars during those same periods?

Periods: past 2 years and past 10 years.
Strategies: Strategy A and BB_SQUEEZE.
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import (
    load_data, bs_call, RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
)
from strategy_alternatives import (
    extend_features, rule_I_bb_squeeze, rule_A_current, run_strategy,
)
from strategy_fresh_capital import TAX_SHORT, TAX_LONG

PER_LOT = 10_000
MONTHLY_SAVINGS = 2_500

PERIODS = {
    "PAST 2 YEARS  (May 2024 → May 2026)": ("2024-05-13", "2026-05-13"),
    "PAST 10 YEARS (May 2016 → May 2026)": ("2016-05-13", "2026-05-13"),
}


def get_strategy_trades(df, rule, start, end):
    all_trades = run_strategy(df, rule, PER_LOT)
    if all_trades.empty:
        return all_trades
    return all_trades[
        (all_trades["entry_date"] >= start) & (all_trades["entry_date"] <= end)
    ].copy().reset_index(drop=True)


def rotation_portfolio(trades, feats, monthly_savings):
    """
    Track a portfolio where:
      - $monthly_savings/mo buys VOO shares
      - LEAPS entries draw cash from selling VOO shares
      - LEAPS exits return cash to buy more VOO shares
    Returns: dict with final value, total deposited, LEAPS contribution.
    """
    voo_shares = 0.0
    total_deposited = 0.0
    open_lots = []
    leaps_proceeds_total = 0.0
    leaps_cost_total = 0.0
    last_month = None

    trade_by_entry = {t.entry_date: t for t in trades.itertuples()} if not trades.empty else {}
    trade_by_exit  = {}
    for t in trades.itertuples():
        trade_by_exit.setdefault(t.exit_date, []).append(t)

    for date in feats.index:
        spy = float(feats.loc[date, "SPY"])
        m = (date.year, date.month)

        # Monthly $2,500 → VOO shares
        if last_month != m:
            voo_shares += monthly_savings / spy
            total_deposited += monthly_savings
            last_month = m

        # LEAPS entries fire → sell VOO to fund
        if date in trade_by_entry:
            t = trade_by_entry[date]
            cost = t.cost
            voo_sell_value = voo_shares * spy
            if voo_sell_value >= cost:
                shares_to_sell = cost / spy
                voo_shares -= shares_to_sell
                open_lots.append(t)
                leaps_cost_total += cost
            else:
                # Not enough VOO to fund LEAPS — skip (rare in this period)
                pass

        # LEAPS exits → proceeds buy VOO back
        if date in trade_by_exit:
            for t in trade_by_exit[date]:
                if t in open_lots:
                    voo_shares += t.proceeds / spy
                    leaps_proceeds_total += t.proceeds
                    open_lots.remove(t)

    # MTM remaining open lots at end (don't sell — measure paper value)
    final_spy = float(feats["SPY"].iloc[-1])
    final_sigma = float(feats["IV1Y_cal"].iloc[-1]) if pd.notna(feats["IV1Y_cal"].iloc[-1]) else float(feats["VIX"].iloc[-1]) / 100
    open_mtm = 0.0
    open_cost = 0.0
    for t in open_lots:
        T_rem = max(((t.entry_date + pd.Timedelta(days=int(LEAPS_YEARS * 365))) - feats.index[-1]).days / 365.25, 1e-6)
        strike = round(t.entry_spy)
        mark = bs_call(final_spy, strike, T_rem, RISK_FREE_RATE, final_sigma)
        open_mtm += mark * 100 * t.contracts
        open_cost += t.cost

    voo_value = voo_shares * final_spy
    total_value = voo_value + open_mtm

    return {
        "total": total_value,
        "deposited": total_deposited,
        "voo_value": voo_value,
        "open_mtm": open_mtm,
        "open_cost": open_cost,
        "leaps_proceeds": leaps_proceeds_total,
        "leaps_cost": leaps_cost_total,
        "voo_shares_final": voo_shares,
    }


def voo_only_portfolio(feats, monthly_savings):
    """Pure VOO monthly DCA. No LEAPS at all."""
    shares = 0.0
    deposited = 0.0
    last_month = None
    for date in feats.index:
        m = (date.year, date.month)
        if last_month != m:
            shares += monthly_savings / float(feats.loc[date, "SPY"])
            deposited += monthly_savings
            last_month = m
    final = shares * float(feats["SPY"].iloc[-1])
    return {"total": final, "deposited": deposited}


def estimate_taxes(rotation_result, trades):
    """Estimate full tax bill if everything were sold today.
    - LEAPS realized gains: short-term (32%) if <366d, long-term (20%) else
    - VOO appreciation: treat as long-term cap gains (20%) — most lots held >1yr
    - Basis for VOO at end = monthly deposits + LEAPS proceeds reinvested
    """
    leaps_tax = sum(
        (t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
        for t in trades.itertuples() if t.proceeds > t.cost
    ) if not trades.empty else 0.0

    voo_basis = rotation_result["deposited"] + rotation_result["leaps_proceeds"]
    voo_gain = rotation_result["voo_value"] - voo_basis
    voo_tax = max(0, voo_gain) * TAX_LONG

    return leaps_tax + voo_tax


def main():
    df = load_data()
    feats_full = extend_features(df)

    for period_label, (start, end) in PERIODS.items():
        print("\n" + "═" * 110)
        print(f"  {period_label}  •  ${MONTHLY_SAVINGS:,}/mo savings  •  ROTATION MODEL")
        print("  (cash never idle: VOO by default → sell VOO to fund LEAPS → buy back VOO on exit)")
        print("═" * 110)

        feats = feats_full.loc[start:end]
        n_months = max(1, len(pd.date_range(start, end, freq="MS")))
        spy_return = (feats["SPY"].iloc[-1] / feats["SPY"].iloc[0] - 1) * 100
        print(f"  SPY return: {spy_return:+.1f}%   |   {n_months} months  |   "
              f"Total deposited: ${MONTHLY_SAVINGS * n_months:,.0f}")

        voo_pure = voo_only_portfolio(feats, MONTHLY_SAVINGS)
        pure_pnl = voo_pure["total"] - voo_pure["deposited"]
        pure_pnl_after = pure_pnl - max(0, pure_pnl) * TAX_LONG

        print(f"\n  ── BENCHMARK: Pure VOO monthly DCA ──")
        print(f"     Deposited      : ${voo_pure['deposited']:>11,.0f}")
        print(f"     Final value    : ${voo_pure['total']:>11,.0f}")
        print(f"     Profit         : ${pure_pnl:>+11,.0f}   ({pure_pnl/voo_pure['deposited']*100:+.1f}%)")
        print(f"     After-tax      : ${pure_pnl_after:>+11,.0f}   ({pure_pnl_after/voo_pure['deposited']*100:+.1f}%)")

        for label, rule in [("Strategy A", rule_A_current), ("BB_SQUEEZE", rule_I_bb_squeeze)]:
            trades = get_strategy_trades(df, rule, start, end)
            n = len(trades)
            if n == 0:
                print(f"\n  ── {label}: 0 trades, identical to pure VOO ──")
                continue

            result = rotation_portfolio(trades, feats, MONTHLY_SAVINGS)
            total = result["total"]
            deposited = result["deposited"]
            profit = total - deposited
            total_tax = estimate_taxes(result, trades)
            leaps_realized_pnl = result["leaps_proceeds"] - result["leaps_cost"]
            leaps_tax_only = sum(
                (t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
                for t in trades.itertuples() if t.proceeds > t.cost
            )
            voo_tax_only = total_tax - leaps_tax_only
            after_tax = profit - total_tax
            edge = total - voo_pure["total"]
            edge_after_tax = after_tax - pure_pnl_after

            print(f"\n  ── {label}: ROTATION (VOO+LEAPS rotating, {n} signals fired) ──")
            print(f"     Deposited            : ${deposited:>11,.0f}")
            print(f"     Final VOO value      : ${result['voo_value']:>11,.0f}")
            print(f"     Open LEAPS MTM       : ${result['open_mtm']:>11,.0f}  (cost ${result['open_cost']:,.0f})")
            print(f"     TOTAL value          : ${total:>11,.0f}")
            print(f"     Profit               : ${profit:>+11,.0f}   ({profit/deposited*100:+.1f}%)")
            print(f"     LEAPS realized P&L   : ${leaps_realized_pnl:>+11,.0f}")
            print(f"     LEAPS tax bill       : ${leaps_tax_only:>11,.0f}")
            print(f"     VOO LTCG tax (if sold): ${voo_tax_only:>11,.0f}")
            print(f"     Total tax            : ${total_tax:>11,.0f}")
            print(f"     After-tax profit     : ${after_tax:>+11,.0f}   ({after_tax/deposited*100:+.1f}%)")
            print(f"     ═══════════════════════════════════════════════════════════")
            verdict_pre = "✅ LEAPS WINS" if edge > 0 else "❌ VOO wins"
            verdict_post = "✅ LEAPS WINS" if edge_after_tax > 0 else "❌ VOO wins"
            print(f"     EDGE vs pure VOO     : ${edge:>+11,.0f}  pre-tax   {verdict_pre}")
            print(f"                          : ${edge_after_tax:>+11,.0f}  after-tax {verdict_post}")

    print("\n" + "═" * 110)
    print("  HOW TO READ THIS:")
    print("    • Pure VOO   = ALL savings go to VOO each month, never sold")
    print("    • ROTATION   = monthly savings go to VOO, but VOO is SOLD to fund LEAPS,")
    print("                   then VOO is REBOUGHT when LEAPS exits.  Cash never sits idle.")
    print("    • EDGE > 0   = LEAPS strategy added value to a pure VOO portfolio")
    print("    • EDGE < 0   = the rotation cost you money vs just holding VOO")
    print("═" * 110)


if __name__ == "__main__":
    main()
