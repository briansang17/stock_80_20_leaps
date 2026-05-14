"""
Strategy A — MULTI-LOT Variant
===============================

Same entry signals as strategy_backtest.py, but allows multiple concurrent
LEAPS positions (up to MAX_LOTS). Each lot is sized at ~1/N of total capital
and exits independently. This deploys capital on more "strong buy" days
instead of sitting idle while a single position is held.

Hypothesis: stacking entries during sustained bull trends should improve
returns while diversifying timing risk on any single trade.

Test design:
- TOTAL_CAPITAL = $20,000 (vs $8k single-lot — need more for multi-lot to work)
- MAX_LOTS = 4 concurrent positions
- ENTRY_DEBOUNCE = 14 days between any two entries (prevents same-cross stacking)
- Each new lot sized at min(START_CAPITAL/MAX_LOTS, available_cash)

Usage:
    python strategy_backtest_multilot.py --profile STRICT
    python strategy_backtest_multilot.py --profile STRICT --max-lots 3
    python strategy_backtest_multilot.py --profile STRICT --capital 30000 --max-lots 5
"""

from __future__ import annotations
import argparse, math, os, sys
import pandas as pd
import numpy as np
from scipy.stats import norm
from dataclasses import dataclass

from strategy_backtest import (
    PROFILES, add_features, signals_in_window, bs_call, load_data,
    RISK_FREE_RATE, LEAPS_YEARS,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
    COMMISSION_PER_CONTRACT, Trade,
)

# Multi-lot defaults
DEFAULT_CAPITAL = 20_000
DEFAULT_MAX_LOTS = 4
DEFAULT_ENTRY_DEBOUNCE_DAYS = 14

def run_backtest_multilot(
    df: pd.DataFrame,
    profile: str,
    total_capital: float = DEFAULT_CAPITAL,
    max_lots: int = DEFAULT_MAX_LOTS,
    entry_debounce_days: int = DEFAULT_ENTRY_DEBOUNCE_DAYS,
    start_date: str | None = None,
    end_date: str | None = None,
):
    cfg = PROFILES[profile]
    feats = add_features(df)
    if start_date is not None:
        feats = feats.loc[start_date:]
    if end_date is not None:
        feats = feats.loc[:end_date]
    sigs = signals_in_window(feats, cfg["cross_window"])

    capital_per_slot = total_capital / max_lots
    cash = total_capital
    lots: list[dict] = []
    last_entry_date = None
    trades: list[Trade] = []
    equity_rows = []
    skipped_no_capital = 0
    skipped_full_lots = 0

    for date, row in feats.iterrows():
        spy = float(row["SPY"])
        sigma = float(row["IV1Y_cal"]) if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"]) else float(row["VIX"]) / 100
        spread = float(row["spread"]) if "spread" in row and pd.notna(row["spread"]) else 0.04
        score = int(sigs.loc[date, "score"]) if date in sigs.index else 0
        opt_val_total = 0.0

        # Mark-to-market all open lots and check exits
        new_lots = []
        for lot in lots:
            T_rem = max((lot["expiry"] - date).days / 365.25, 1e-6)
            mark_bid = bs_call(spy, lot["strike"], T_rem, RISK_FREE_RATE, sigma) * (1 - spread / 2)
            opt_val = mark_bid * 100 * lot["contracts"]
            held = (date - lot["entry_date"]).days
            sell, reason = False, ""
            if T_rem <= EXIT_NEAR_EXP:
                sell, reason = True, "Near expiry"
            elif held >= cfg["max_hold_days"]:
                sell, reason = True, "Max hold"
            elif held >= cfg["min_hold_days"]:
                if spy < row["sma50"] * EXIT_DD_50DMA:
                    sell, reason = True, "SPY broke 50DMA"
                elif row["VIX"] > EXIT_VIX_HIGH:
                    sell, reason = True, f"VIX>{EXIT_VIX_HIGH}"
                elif row["vix_slope5"] > EXIT_VIX_SLOPE:
                    sell, reason = True, f"VIX +{EXIT_VIX_SLOPE}/5d"

            if sell:
                sell_commission = lot["contracts"] * COMMISSION_PER_CONTRACT
                net_proceeds = opt_val - sell_commission
                trades.append(Trade(
                    entry_date=lot["entry_date"], exit_date=date,
                    entry_spy=lot["entry_spy"], exit_spy=spy,
                    entry_vix=lot["entry_vix"], entry_dd=lot["entry_dd"],
                    contracts=lot["contracts"], cost=lot["cost"], proceeds=net_proceeds,
                    pct=(net_proceeds - lot["cost"]) / lot["cost"], held_days=held,
                    exit_reason=reason,
                ))
                cash += net_proceeds
            else:
                new_lots.append(lot)
                opt_val_total += opt_val
        lots = new_lots

        # Entry check — only when an open slot exists AND debounce period passed
        slots_open = len(lots) < max_lots
        debounce_ok = (last_entry_date is None) or ((date - last_entry_date).days >= entry_debounce_days)

        entry_ok = (
            score >= 2 and
            bool(row["spy_above_200"]) and
            row["VIX"] < cfg["vix_max_entry"] and
            row["RSI14"] < cfg["rsi_max_entry"] and
            slots_open and
            debounce_ok
        )

        if entry_ok:
            strike = round(spy)
            premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
            if premium > 0:
                slot_budget = min(capital_per_slot, cash)
                raw_contracts = slot_budget / (premium * 100)
                contracts = int(raw_contracts)
                if contracts >= 1:
                    buy_commission = contracts * COMMISSION_PER_CONTRACT
                    cost = contracts * premium * 100 + buy_commission
                    cash -= cost
                    new_lot = {
                        "strike": strike, "contracts": contracts,
                        "entry_date": date, "entry_spy": spy,
                        "entry_vix": float(row["VIX"]),
                        "entry_dd": float(row["drawdown"]),
                        "cost": cost,
                        "expiry": date + pd.Timedelta(days=int(LEAPS_YEARS * 365)),
                    }
                    lots.append(new_lot)
                    last_entry_date = date
                    opt_val_total += contracts * bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * 100
                else:
                    skipped_no_capital += 1
        elif score >= 2 and not slots_open:
            skipped_full_lots += 1

        equity_rows.append({
            "date": date,
            "total": cash + opt_val_total,
            "cash": cash,
            "open_lots": len(lots),
        })

    eq = pd.DataFrame(equity_rows).set_index("date")
    trades_df = pd.DataFrame([t.__dict__ for t in trades]) if trades else pd.DataFrame()
    return eq, trades_df, {
        "skipped_no_capital": skipped_no_capital,
        "skipped_full_lots": skipped_full_lots,
    }

def summarize(eq, trades_df, profile, total_capital, max_lots, debug):
    final = eq["total"].iloc[-1]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = ((final / total_capital) ** (1 / years) - 1) * 100 if final > 0 else -100
    peak = eq["total"].cummax()
    maxdd = (eq["total"] / peak - 1).min() * 100
    n = len(trades_df)
    wins = (trades_df["pct"] > 0).sum() if n else 0
    avg = trades_df["pct"].mean() * 100 if n else 0
    avg_w = trades_df[trades_df["pct"] > 0]["pct"].mean() * 100 if wins else 0
    avg_l = trades_df[trades_df["pct"] <= 0]["pct"].mean() * 100 if (n - wins) else 0
    avg_concurrent = eq["open_lots"].mean()
    max_concurrent = eq["open_lots"].max()
    pct_invested = (eq["open_lots"] > 0).mean() * 100

    print(f"\n{'═' * 78}")
    print(f"  STRATEGY A — MULTI-LOT  •  PROFILE: {profile}  •  MAX_LOTS: {max_lots}")
    print(f"{'─' * 78}")
    print(f"  Starting capital     : ${total_capital:,.0f}")
    print(f"  Final value          : ${final:,.0f}  (+{(final / total_capital - 1) * 100:.0f}%)")
    print(f"  CAGR                 : {cagr:+.1f}%/yr")
    print(f"  Max drawdown         : {maxdd:.1f}%")
    print(f"  Trades               : {n} over {years:.1f} years ({n / years:.1f}/yr)")
    print(f"  Win rate             : {wins}/{n}  ({wins / max(n, 1) * 100:.0f}%)")
    print(f"  Avg per trade        : {avg:+.1f}%")
    print(f"  Avg win              : {avg_w:+.1f}%")
    print(f"  Avg loss             : {avg_l:+.1f}%")
    print(f"  Avg concurrent lots  : {avg_concurrent:.2f}")
    print(f"  Max concurrent lots  : {max_concurrent}")
    print(f"  % of time invested   : {pct_invested:.0f}%")
    print(f"  Skipped (no cash)    : {debug['skipped_no_capital']}")
    print(f"  Skipped (lots full)  : {debug['skipped_full_lots']}")
    print(f"{'═' * 78}\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="STRICT")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--max-lots", type=int, default=DEFAULT_MAX_LOTS)
    parser.add_argument("--debounce", type=int, default=DEFAULT_ENTRY_DEBOUNCE_DAYS)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--data", default="data_cache/term_structure.csv")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    df = load_data(args.data)
    eq, trades, dbg = run_backtest_multilot(
        df, args.profile,
        total_capital=args.capital,
        max_lots=args.max_lots,
        entry_debounce_days=args.debounce,
        start_date=args.start,
        end_date=args.end,
    )
    summarize(eq, trades, args.profile, args.capital, args.max_lots, dbg)

    os.makedirs(args.out, exist_ok=True)
    tag = f"{args.profile.lower()}_x{args.max_lots}"
    eq.to_csv(f"{args.out}/equity_multilot_{tag}.csv")
    trades.to_csv(f"{args.out}/trades_multilot_{tag}.csv", index=False)
    print(f"  💾 Saved: {args.out}/equity_multilot_{tag}.csv")
    print(f"  💾 Saved: {args.out}/trades_multilot_{tag}.csv")

if __name__ == "__main__":
    main()
