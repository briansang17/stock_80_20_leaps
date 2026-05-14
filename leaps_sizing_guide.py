"""
LEAPS Sizing Guide
==================

Computes the EXACT dollar amount needed to fund a SPY 2-year ATM LEAPS
contract at various SPY prices and VIX levels, using Black-Scholes with
calibrated 1-year IV.

Outputs:
  1. Current-conditions estimate (based on latest cached data)
  2. Sensitivity table (cost vs SPY price × VIX level)
  3. Position-sizing recommendation table
"""

from __future__ import annotations
import pandas as pd

from strategy_backtest import load_data, bs_call, RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT
from strategy_alternatives import extend_features


def contract_cost(spy: float, iv: float, spread: float = 0.045) -> dict:
    """Cost to BUY one 2-year ATM SPY call contract."""
    mid = bs_call(spy, spy, LEAPS_YEARS, RISK_FREE_RATE, iv)
    ask = mid * (1 + spread / 2)
    cost_per_contract = ask * 100 + COMMISSION_PER_CONTRACT
    return {
        "mid_premium": mid,
        "ask_premium": ask,
        "cost_per_contract": cost_per_contract,
    }


def main():
    df = load_data()
    feats = extend_features(df)
    latest = feats.iloc[-1]

    spy_now = latest["SPY"]
    vix_now = latest["VIX"]
    iv_now  = latest["IV1Y_cal"]
    spread_now = latest["spread"]

    print("\n" + "═" * 80)
    print("  LEAPS SIZING — How Much VOO to Sell to Fund 1 Contract")
    print("═" * 80)

    print(f"\n  ── CURRENT MARKET (as of {latest.name.date()}) ──")
    print(f"     SPY              : ${spy_now:>7,.2f}")
    print(f"     VIX              : {vix_now:>6.1f}")
    print(f"     Calibrated 1Y IV : {iv_now*100:>5.1f}%")
    print(f"     Bid/ask spread   : {spread_now*100:>5.1f}%")

    c = contract_cost(spy_now, iv_now, spread_now)
    print(f"\n     >> 1 contract (SPY 2-yr ATM call) cost TODAY: "
          f"${c['cost_per_contract']:,.0f}")
    print(f"        (premium: ${c['ask_premium']:.2f} × 100 + $0.65 fee)")

    # ── Sensitivity table ────────────────────────────────────────────────────
    print(f"\n  ── COST TABLE — what 1 contract costs at different SPY/VIX levels ──")
    print(f"\n     {'SPY price':>10} │", end="")
    vix_levels = [12, 14, 16, 18, 20, 22, 25]
    for v in vix_levels:
        print(f"  VIX {v:>3}", end="")
    print()
    print(f"     {'─' * 10}─┼" + "─" * (len(vix_levels) * 9))

    spy_levels = [450, 500, 550, 580, 600, 650, 700]
    for s in spy_levels:
        print(f"     ${s:>8,}  │", end="")
        for v in vix_levels:
            # rough mapping: 1Y IV ~ VIX * 0.92  (term-structure dampening)
            iv = (v / 100) * 0.92
            cost = contract_cost(s, iv, 0.045)["cost_per_contract"]
            print(f"  ${cost:>5,.0f}", end="")
        print()

    print(f"\n     Note: Numbers assume 1Y IV ≈ VIX × 0.92 (typical term structure)")

    # ── Sizing rules ─────────────────────────────────────────────────────────
    print(f"\n  ── HOW MANY CONTRACTS — based on your portfolio size ──")

    print(f"\n     Rule of thumb: never put more than ~3-5% of total portfolio into LEAPS.")
    print(f"     Each LEAPS contract today ≈ ${c['cost_per_contract']:,.0f}.\n")

    print(f"     {'Portfolio':>11}  {'3% max':>11}  {'5% max':>11}  {'Suggested #':>14}")
    print(f"     {'─' * 52}")
    cost = c["cost_per_contract"]
    for portfolio in [50_000, 100_000, 150_000, 200_000, 300_000, 500_000, 1_000_000]:
        max_3 = portfolio * 0.03
        max_5 = portfolio * 0.05
        suggested = max(1, int(max_3 / cost))
        print(f"     ${portfolio:>9,}  ${max_3:>9,.0f}  ${max_5:>9,.0f}  "
              f"{suggested:>10} contract{'s' if suggested != 1 else ''}")

    # ── VOO mechanics ────────────────────────────────────────────────────────
    print(f"\n  ── HOW TO ACTUALLY SELL THE VOO ──")
    voo_now = spy_now * 0.918    # VOO trades ~91.8% of SPY (rough proxy)
    shares_for_1 = c["cost_per_contract"] / voo_now
    print(f"     1. VOO price ≈ ${voo_now:,.2f}  (roughly 91.8% of SPY)")
    print(f"     2. To raise ${c['cost_per_contract']:,.0f} for 1 contract:")
    print(f"        sell ~{shares_for_1:.1f} shares of VOO")
    print(f"     3. Place a LIMIT order on VOO (don't market-order — VOO has wide spreads pre/post-mkt)")
    print(f"     4. Once filled, place a LIMIT order on SPY 2-yr ATM call near the mid price")
    print(f"     5. If you can't fill mid + $0.05, raise to mid + $0.10.  Don't chase higher.")

    # ── Tax timing ───────────────────────────────────────────────────────────
    print(f"\n  ── TAX-EFFICIENT VOO LOTS TO SELL ──")
    print(f"     When selling VOO to fund LEAPS, sell the LOTS in this order:")
    print(f"     1. Any lots at a LOSS  (harvest the loss to offset future LEAPS gains)")
    print(f"     2. Long-term lots ≥1 year old, smallest gain first")
    print(f"     3. Short-term lots (avoid these if possible — taxed at 32%)")
    print(f"     This is called 'specific-lot identification' — your broker (Fidelity, Schwab,")
    print(f"     Robinhood) lets you choose lots when you sell.  Default is usually FIFO which")
    print(f"     sells your OLDEST lots first.  Switch to 'tax-efficient' or specify lots.")

    print(f"\n  ── ROUND-TRIP EXAMPLE (using TODAY's prices) ──")
    print(f"     STEP 1  Signal fires (P_DEEP_SQUEEZE)")
    print(f"     STEP 2  Sell {shares_for_1:.1f} shares VOO @ ${voo_now:.2f} = ${c['cost_per_contract']:,.0f} proceeds")
    print(f"     STEP 3  Buy 1 SPY {LEAPS_YEARS:.0f}-yr ${spy_now:.0f} call @ ${c['ask_premium']:.2f}")
    print(f"             = ${c['cost_per_contract']:,.0f} (incl. $0.65 fee)")
    print(f"     STEP 4  Hold 6-14 months, exit on sell trigger")
    print(f"     STEP 5  If LEAPS exits @ +40% gain  →  ${c['cost_per_contract']*1.4:,.0f} proceeds")
    print(f"             ↳ Buy back ${c['cost_per_contract']*1.4 / voo_now:.1f} shares of VOO")
    print(f"     STEP 6  Resume monthly $2,500 VOO DCA")

    print()
    print("  " + "═" * 78)
    print("  REMEMBER:")
    print("    • P_DEEP_SQUEEZE fires ~once per year — most days you do NOTHING")
    print("    • Don't 'pre-position' or 'almost-buy' — wait for ALL 5 conditions")
    print("    • Use limit orders only — never market orders on LEAPS")
    print("    • Pick contracts with open interest > 100 to ensure liquidity")
    print("  " + "═" * 78)


if __name__ == "__main__":
    main()
