"""
Comprehensive head-to-head: LEAPS strategies vs realistic VOO alternatives.

Compares Strategy A and BB_SQUEEZE against three benchmarks:

  1. SPY-DCA-on-same-dates    — same $ on same days as LEAPS  (apples-to-apples)
  2. EQUAL-DEPLOYMENT DCA     — same total $ deployed, but spread evenly
                                across the period as monthly VOO buys
  3. FULL CASH-FLOW VIEW      — $2,500/mo savings.  LEAPS uses what it needs,
                                idle cash sits in HYSA at 4.5% APY.
                                Compare to: pure $2,500/mo into VOO

Two windows: past 2 years and past 10 years.
"""

from __future__ import annotations
import os
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
MONTHLY_SAVINGS = 2_500       # user's realistic monthly cash inflow
HYSA_APY = float(os.environ.get("HYSA_APY", "0.0385"))   # 3.85% — current market midpoint
TAX_HYSA = 0.32               # HYSA interest is ordinary income

PERIODS = {
    "PAST 2 YEARS  (May 2024 → May 2026)": ("2024-05-13", "2026-05-13"),
    "PAST 10 YEARS (May 2016 → May 2026)": ("2016-05-13", "2026-05-13"),
}


def get_strategy_trades(df, rule, start, end):
    all_trades = run_strategy(df, rule, PER_LOT)
    if all_trades.empty:
        return all_trades
    sub = all_trades[
        (all_trades["entry_date"] >= start) & (all_trades["entry_date"] <= end)
    ].copy().reset_index(drop=True)
    return sub


def head_to_head(trades):
    """LEAPS round-trip vs SPY-shares round-trip on same dates.
    Uses spy_value_at_exit which is SPY shares × SPY price on LEAPS exit date.
    """
    if trades.empty:
        return 0.0, 0.0, 0.0
    cum_cost  = trades["cost"].sum()
    leaps_val = trades["proceeds"].sum()
    spy_dca   = trades["spy_value_at_exit"].sum()
    return leaps_val, cum_cost, spy_dca


def voo_monthly_dca(feats, monthly):
    """Buy $monthly of VOO on the 1st trading day of each month."""
    shares = 0.0
    deployed = 0.0
    last_month = None
    for date in feats.index:
        m = (date.year, date.month)
        if last_month != m:
            shares += monthly / float(feats.loc[date, "SPY"])
            deployed += monthly
            last_month = m
    final_value = shares * float(feats["SPY"].iloc[-1])
    return final_value, deployed


def voo_equal_total(feats, total_dollars, n_months):
    """Spread `total_dollars` across the period via equal monthly buys."""
    per_month = total_dollars / n_months
    return voo_monthly_dca(feats, per_month)


def hysa_value(start_dollars, days):
    """Compounding HYSA value for a lump sum held for `days` days."""
    years = days / 365.25
    return start_dollars * (1 + HYSA_APY) ** years


def hysa_growth_with_deposits(feats, monthly):
    """Track HYSA balance with $monthly deposits, compounding daily at HYSA_APY."""
    daily_rate = (1 + HYSA_APY) ** (1 / 365.25) - 1
    bal = 0.0
    interest = 0.0
    last_month = None
    rows = []
    for date in feats.index:
        m = (date.year, date.month)
        if last_month is not None:
            bal *= (1 + daily_rate)
            interest += bal * daily_rate
        if last_month != m:
            bal += monthly
            last_month = m
        rows.append({"date": date, "balance": bal, "interest": interest})
    return pd.DataFrame(rows).set_index("date")


def cashflow_view(trades, feats, monthly):
    """
    Model: $monthly arrives each month; LEAPS draws from the savings pile when
    a signal fires.  Idle cash compounds at HYSA APY.  Return final portfolio
    value (LEAPS proceeds + HYSA + open LEAPS MTM).
    """
    daily_rate = (1 + HYSA_APY) ** (1 / 365.25) - 1
    hysa = 0.0
    leaps_proceeds = 0.0
    open_lots = []
    last_month = None
    interest_earned = 0.0

    trade_lookup = {t.entry_date: t for t in trades.itertuples()} if not trades.empty else {}

    for date in feats.index:
        m = (date.year, date.month)

        # Daily interest accrual on cash
        if hysa > 0:
            d_int = hysa * daily_rate
            hysa += d_int
            interest_earned += d_int

        # Monthly inflow
        if last_month != m:
            hysa += monthly
            last_month = m

        # LEAPS entries on signal dates (use HYSA cash)
        if date in trade_lookup:
            t = trade_lookup[date]
            if hysa >= t.cost:
                hysa -= t.cost
                open_lots.append({
                    "trade": t,
                    "still_open": True,
                })
            # If not enough HYSA, skip (rare)

        # LEAPS exits at trade exit date
        for lot in open_lots:
            if lot["still_open"] and lot["trade"].exit_date <= date:
                leaps_proceeds += lot["trade"].proceeds
                hysa += lot["trade"].proceeds  # cash returns to HYSA
                lot["still_open"] = False

    # MTM any remaining open lots at end
    final_spy = float(feats["SPY"].iloc[-1])
    final_sigma = float(feats["IV1Y_cal"].iloc[-1]) if pd.notna(feats["IV1Y_cal"].iloc[-1]) else float(feats["VIX"].iloc[-1]) / 100
    open_mtm = 0.0
    for lot in open_lots:
        if lot["still_open"]:
            t = lot["trade"]
            T_rem = max(((t.entry_date + pd.Timedelta(days=int(LEAPS_YEARS * 365))) - feats.index[-1]).days / 365.25, 1e-6)
            strike = round(t.entry_spy)
            mark = bs_call(final_spy, strike, T_rem, RISK_FREE_RATE, final_sigma)
            open_mtm += mark * 100 * t.contracts

    total = hysa + open_mtm
    return {
        "total": total,
        "hysa_end": hysa,
        "open_mtm": open_mtm,
        "interest": interest_earned,
        "leaps_proceeds": leaps_proceeds,
    }


def apply_leaps_tax(trades):
    if trades.empty:
        return 0.0
    return sum((t.proceeds - t.cost) * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
               for t in trades.itertuples() if t.proceeds > t.cost)


def main():
    df = load_data()
    feats_full = extend_features(df)

    for period_label, (start, end) in PERIODS.items():
        print("\n" + "═" * 110)
        print(f"  {period_label}")
        print("═" * 110)

        feats = feats_full.loc[start:end]
        period_days = (feats.index[-1] - feats.index[0]).days
        period_months = max(1, round(period_days / 30.44))
        spy_return = (feats["SPY"].iloc[-1] / feats["SPY"].iloc[0] - 1) * 100
        print(f"  SPY return over period: {spy_return:+.1f}%   ({period_months} months)")

        for label, rule in [("Strategy A", rule_A_current), ("BB_SQUEEZE", rule_I_bb_squeeze)]:
            trades = get_strategy_trades(df, rule, start, end)
            n = len(trades)
            if n == 0:
                print(f"\n  {label}: 0 trades — nothing to compare")
                continue

            # ─── 1. LEAPS strategy alone (deployed-only) ──────────────────────
            leaps_val, cum_cost, spy_dca_val = head_to_head(trades)
            leaps_pnl = leaps_val - cum_cost
            spy_dca_pnl = spy_dca_val - cum_cost
            leaps_tax = apply_leaps_tax(trades)
            leaps_after = leaps_val - leaps_tax
            leaps_after_pnl = leaps_after - cum_cost
            spy_tax = max(0, spy_dca_pnl) * TAX_LONG
            spy_dca_after_pnl = spy_dca_pnl - spy_tax

            # ─── 2. Equal-deployment VOO DCA (same total $) ───────────────────
            voo_eq_final, voo_eq_dep = voo_equal_total(feats, cum_cost, period_months)
            voo_eq_pnl = voo_eq_final - voo_eq_dep
            voo_eq_after_pnl = voo_eq_pnl - max(0, voo_eq_pnl) * TAX_LONG

            # ─── 3. Full cash-flow view: $2,500/mo, LEAPS + HYSA ──────────────
            #     (a) LEAPS+HYSA mixed portfolio
            cf = cashflow_view(trades, feats, MONTHLY_SAVINGS)
            #     (b) Pure VOO monthly DCA at same $2,500/mo
            voo_full_final, voo_full_dep = voo_monthly_dca(feats, MONTHLY_SAVINGS)
            voo_full_pnl = voo_full_final - voo_full_dep

            # Mixed portfolio tax: HYSA interest + LEAPS gains + open MTM (unrealized)
            mixed_total_pnl = cf["total"] - voo_full_dep   # same total deposits
            mixed_after_tax_pnl = (
                mixed_total_pnl
                - leaps_tax
                - cf["interest"] * TAX_HYSA
            )
            voo_full_after_pnl = voo_full_pnl - max(0, voo_full_pnl) * TAX_LONG

            print(f"\n  ┌─ {label}  ({n} trades, ${cum_cost:,.0f} deployed in LEAPS)")
            print(f"  │")
            print(f"  │  HEAD-TO-HEAD (apples-to-apples on the same $ on same dates):")
            print(f"  │    LEAPS alone               ${leaps_pnl:>+10,.0f}  ({leaps_pnl/cum_cost*100:>+5.1f}%)   "
                  f"after-tax: ${leaps_after_pnl:>+10,.0f}  ({leaps_after_pnl/cum_cost*100:>+5.1f}%)")
            print(f"  │    SPY-DCA on same dates     ${spy_dca_pnl:>+10,.0f}  ({spy_dca_pnl/cum_cost*100:>+5.1f}%)   "
                  f"after-tax: ${spy_dca_after_pnl:>+10,.0f}  ({spy_dca_after_pnl/cum_cost*100:>+5.1f}%)")
            print(f"  │    >>> EDGE                  ${leaps_pnl - spy_dca_pnl:>+10,.0f}                       "
                  f"after-tax: ${leaps_after_pnl - spy_dca_after_pnl:>+10,.0f}")
            print(f"  │")
            print(f"  │  VS EQUAL-DEPLOYMENT VOO DCA (${cum_cost:,.0f} spread across {period_months} months):")
            print(f"  │    VOO equal DCA             ${voo_eq_pnl:>+10,.0f}  ({voo_eq_pnl/voo_eq_dep*100:>+5.1f}%)   "
                  f"after-tax: ${voo_eq_after_pnl:>+10,.0f}  ({voo_eq_after_pnl/voo_eq_dep*100:>+5.1f}%)")
            print(f"  │    >>> EDGE                  ${leaps_pnl - voo_eq_pnl:>+10,.0f}                       "
                  f"after-tax: ${leaps_after_pnl - voo_eq_after_pnl:>+10,.0f}")
            print(f"  │")
            print(f"  │  FULL CASH-FLOW VIEW (${MONTHLY_SAVINGS:,}/mo savings; idle cash earns {HYSA_APY*100:.1f}% HYSA):")
            print(f"  │    LEAPS + HYSA mixed        ${mixed_total_pnl:>+10,.0f}  "
                  f"(${cf['interest']:>6,.0f} from HYSA interest)        "
                  f"after-tax: ${mixed_after_tax_pnl:>+10,.0f}")
            print(f"  │    Pure VOO monthly DCA      ${voo_full_pnl:>+10,.0f}                                  "
                  f"after-tax: ${voo_full_after_pnl:>+10,.0f}")
            print(f"  │    >>> EDGE                  ${mixed_total_pnl - voo_full_pnl:>+10,.0f}                       "
                  f"after-tax: ${mixed_after_tax_pnl - voo_full_after_pnl:>+10,.0f}")
            print(f"  └─")

    print("\n" + "═" * 110)
    print("  INTERPRETATION KEYS:")
    print("    • HEAD-TO-HEAD   = best pure test of LEAPS leverage on identical $ & dates")
    print("    • EQUAL-DEPLOY   = does LEAPS-timing beat dumb-spread-out-the-same-money?")
    print("    • CASH-FLOW VIEW = which approach makes more money on your real monthly savings?")
    print("    • EDGE > 0       = LEAPS strategy WON")
    print("    • EDGE < 0       = the VOO benchmark won")
    print("═" * 110)


if __name__ == "__main__":
    main()
