"""
Strategy A — FRESH CAPITAL Variant
===================================

Each buy signal triggers a fresh $CAPITAL_PER_LOT deployment from outside
savings (no shared pool — you add new money on every signal). This matches
the user's stated approach: ~2 buys/year using fresh cash each time.

The fair benchmark is therefore SPY-DCA — *the same dollar amount* deployed
into SPY shares on *the same trade dates* and held until the LEAPS exited.

This isolates the question: "If I'm going to spend $10k on signal day X,
is it better to buy LEAPS or just buy SPY shares?"

Usage:
    python strategy_fresh_capital.py --profile BALANCED
    python strategy_fresh_capital.py --profile STRICT  --per-lot 10000
    python strategy_fresh_capital.py --profile BALANCED --per-lot 5000  --start 2021-01-01
"""

from __future__ import annotations
import argparse, os
import pandas as pd
from dataclasses import dataclass

from strategy_backtest import (
    PROFILES, add_features, signals_in_window, bs_call, load_data,
    RISK_FREE_RATE, LEAPS_YEARS,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
    COMMISSION_PER_CONTRACT, DEFAULT_DATA_PATH,
)

DEFAULT_PER_LOT = 10_000          # $ per entry (fresh cash)
DEFAULT_DEBOUNCE = 14             # min days between any two entries
TAX_SHORT = 0.32
TAX_LONG  = 0.20


@dataclass
class FreshLot:
    entry_date: pd.Timestamp
    exit_date:  pd.Timestamp | None
    entry_spy:  float
    exit_spy:   float | None
    entry_vix:  float
    contracts:  int
    cost:       float       # what you actually paid in (cash deployed)
    proceeds:   float       # what you got out (or current MTM if still open)
    pct:        float
    held_days:  int
    exit_reason: str
    # SPY-DCA benchmark (same cash deployed into SPY on entry date)
    spy_shares: float
    spy_value_at_exit: float


def run_fresh(
    df: pd.DataFrame,
    profile: str,
    per_lot: float = DEFAULT_PER_LOT,
    debounce_days: int = DEFAULT_DEBOUNCE,
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

    open_lots: list[dict] = []
    closed_lots: list[FreshLot] = []
    last_entry = None
    equity_rows = []

    for date, row in feats.iterrows():
        spy = float(row["SPY"])
        sigma = float(row["IV1Y_cal"]) if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"]) else float(row["VIX"]) / 100
        spread = float(row["spread"]) if "spread" in row and pd.notna(row["spread"]) else 0.04
        score = int(sigs.loc[date, "score"]) if date in sigs.index else 0

        # Update / exit open lots
        still_open = []
        for lot in open_lots:
            T_rem = max((lot["expiry"] - date).days / 365.25, 1e-6)
            mark_bid = bs_call(spy, lot["strike"], T_rem, RISK_FREE_RATE, sigma) * (1 - spread / 2)
            mtm = mark_bid * 100 * lot["contracts"]
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
                proceeds = mtm - sell_commission
                closed_lots.append(FreshLot(
                    entry_date=lot["entry_date"], exit_date=date,
                    entry_spy=lot["entry_spy"], exit_spy=spy,
                    entry_vix=lot["entry_vix"],
                    contracts=lot["contracts"], cost=lot["cost"], proceeds=proceeds,
                    pct=(proceeds - lot["cost"]) / lot["cost"],
                    held_days=held, exit_reason=reason,
                    spy_shares=lot["spy_shares"],
                    spy_value_at_exit=lot["spy_shares"] * spy,
                ))
            else:
                lot["mtm"] = mtm
                still_open.append(lot)
        open_lots = still_open

        # Entry check
        debounce_ok = last_entry is None or (date - last_entry).days >= debounce_days
        eligible = (
            score >= 2
            and bool(row["spy_above_200"])
            and row["VIX"] < cfg["vix_max_entry"]
            and row["RSI14"] < cfg["rsi_max_entry"]
            and debounce_ok
        )

        if eligible:
            strike = round(spy)
            premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
            if premium > 0:
                contracts = int(per_lot / (premium * 100))
                if contracts >= 1:
                    buy_commission = contracts * COMMISSION_PER_CONTRACT
                    cost = contracts * premium * 100 + buy_commission
                    spy_shares_equivalent = cost / spy   # SPY-DCA: same cash into shares
                    open_lots.append({
                        "strike": strike, "contracts": contracts,
                        "entry_date": date, "entry_spy": spy,
                        "entry_vix": float(row["VIX"]),
                        "cost": cost,
                        "expiry": date + pd.Timedelta(days=int(LEAPS_YEARS * 365)),
                        "spy_shares": spy_shares_equivalent,
                        "mtm": cost,
                    })
                    last_entry = date

        # Track totals each day
        open_mtm = sum(l["mtm"] for l in open_lots)
        closed_proceeds = sum(c.proceeds for c in closed_lots)
        capital_deployed = sum(l["cost"] for l in open_lots) + sum(c.cost for c in closed_lots)
        spy_dca_value = sum(l["spy_shares"] * spy for l in open_lots) + \
                        sum(c.spy_value_at_exit for c in closed_lots)
        equity_rows.append({
            "date": date,
            "capital_deployed": capital_deployed,
            "open_lots": len(open_lots),
            "open_mtm": open_mtm,
            "closed_proceeds": closed_proceeds,
            "leaps_total_value": open_mtm + closed_proceeds,
            "spy_dca_value": spy_dca_value,
        })

    eq = pd.DataFrame(equity_rows).set_index("date")
    # Force-close any remaining open lots at end (MTM) for fair comparison
    final_spy = float(feats["SPY"].iloc[-1])
    for lot in open_lots:
        held = (feats.index[-1] - lot["entry_date"]).days
        closed_lots.append(FreshLot(
            entry_date=lot["entry_date"], exit_date=feats.index[-1],
            entry_spy=lot["entry_spy"], exit_spy=final_spy,
            entry_vix=lot["entry_vix"],
            contracts=lot["contracts"], cost=lot["cost"],
            proceeds=lot["mtm"],   # MTM, no commission since not actually sold
            pct=(lot["mtm"] - lot["cost"]) / lot["cost"],
            held_days=held, exit_reason="(still open at end)",
            spy_shares=lot["spy_shares"],
            spy_value_at_exit=lot["spy_shares"] * final_spy,
        ))

    trades_df = pd.DataFrame([l.__dict__ for l in closed_lots]) if closed_lots else pd.DataFrame()
    return eq, trades_df


def summarize(eq, trades, profile, per_lot):
    if trades.empty:
        print("\n  (no trades — try a different profile or relax filters)")
        return

    deployed = trades["cost"].sum()
    leaps_value = trades["proceeds"].sum()
    spy_value = trades["spy_value_at_exit"].sum()
    leaps_pnl = leaps_value - deployed
    spy_pnl = spy_value - deployed

    years = (eq.index[-1] - eq.index[0]).days / 365.25
    n = len(trades)

    # Tax: per-lot bucket
    leaps_tax = 0.0
    for t in trades.itertuples():
        gain = t.proceeds - t.cost
        if gain > 0:
            leaps_tax += gain * (TAX_LONG if t.held_days >= 366 else TAX_SHORT)
    leaps_after_tax = leaps_value - leaps_tax
    spy_tax = max(0, spy_pnl) * TAX_LONG  # treat SPY-DCA as LTCG for benchmark
    spy_after_tax = spy_value - spy_tax

    wins = (trades["pct"] > 0).sum()
    avg = trades["pct"].mean() * 100
    avg_w = trades[trades["pct"] > 0]["pct"].mean() * 100 if wins else 0
    avg_l = trades[trades["pct"] <= 0]["pct"].mean() * 100 if n - wins else 0
    short_term = (trades["held_days"] < 366).sum()

    print(f"\n{'═' * 78}")
    print(f"  STRATEGY A — FRESH CAPITAL  •  PROFILE: {profile}  •  ${per_lot:,.0f}/entry")
    print(f"{'─' * 78}")
    print(f"  Period               : {eq.index[0].date()} → {eq.index[-1].date()}  ({years:.1f} yrs)")
    print(f"  Total trades         : {n}   ({n / years:.1f}/year)")
    print(f"  Short-term           : {short_term} of {n}   ({short_term/n*100:.0f}%)")
    print(f"  Total capital deployed : ${deployed:>12,.0f}")
    print(f"  ─" * 38)
    print(f"  LEAPS total value      : ${leaps_value:>12,.0f}    ({(leaps_value/deployed-1)*100:+.1f}% return on capital)")
    print(f"  LEAPS after tax        : ${leaps_after_tax:>12,.0f}    ({(leaps_after_tax/deployed-1)*100:+.1f}% return on capital)")
    print(f"  SPY-DCA total value    : ${spy_value:>12,.0f}    ({(spy_value/deployed-1)*100:+.1f}% return on capital)")
    print(f"  SPY-DCA after tax      : ${spy_after_tax:>12,.0f}    ({(spy_after_tax/deployed-1)*100:+.1f}% return on capital)")
    print(f"  ─" * 38)
    diff_pretax = leaps_value - spy_value
    diff_aftertax = leaps_after_tax - spy_after_tax
    print(f"  EDGE pre-tax           : ${diff_pretax:>+12,.0f}   ({diff_pretax/deployed*100:+.1f}% of capital)")
    print(f"  EDGE after-tax         : ${diff_aftertax:>+12,.0f}   ({diff_aftertax/deployed*100:+.1f}% of capital)")
    print(f"  ─" * 38)
    print(f"  Win rate             : {wins}/{n}  ({wins/n*100:.0f}%)")
    print(f"  Avg per trade        : {avg:+.1f}%")
    print(f"  Avg win              : {avg_w:+.1f}%")
    print(f"  Avg loss             : {avg_l:+.1f}%")
    print(f"{'═' * 78}\n")

    print(f"  {'#':>2}  {'BUY':>11}  {'SELL':>11}  {'Held':>5}  {'Cts':>3}  "
          f"{'$Cost':>8}  {'LEAPS$':>8}  {'SPY$':>8}  {'L%':>6}  {'SPY%':>6}  Reason")
    print(f"  {'─' * 76}")
    for i, t in enumerate(trades.itertuples(), 1):
        mk = "✅" if t.pct > 0 else "❌"
        spy_pct = (t.spy_value_at_exit / t.cost - 1) * 100
        print(f"  {mk}{i:>1}  {str(t.entry_date.date()):>11}  {str(t.exit_date.date()):>11}  "
              f"{t.held_days:>4}d  {t.contracts:>3}  ${t.cost:>7,.0f}  "
              f"${t.proceeds:>7,.0f}  ${t.spy_value_at_exit:>7,.0f}  "
              f"{t.pct*100:>+5.0f}%  {spy_pct:>+5.0f}%  {t.exit_reason}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="BALANCED")
    parser.add_argument("--per-lot", type=float, default=DEFAULT_PER_LOT)
    parser.add_argument("--debounce", type=int, default=DEFAULT_DEBOUNCE)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--data", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    df = load_data(args.data)
    eq, trades = run_fresh(df, args.profile, args.per_lot, args.debounce,
                           args.start, args.end)
    summarize(eq, trades, args.profile, args.per_lot)

    os.makedirs(args.out, exist_ok=True)
    tag = f"{args.profile.lower()}_${int(args.per_lot)}"
    eq.to_csv(f"{args.out}/fresh_equity_{tag}.csv")
    trades.to_csv(f"{args.out}/fresh_trades_{tag}.csv", index=False)
    print(f"  💾 Saved: {args.out}/fresh_equity_{tag}.csv")
    print(f"  💾 Saved: {args.out}/fresh_trades_{tag}.csv")

if __name__ == "__main__":
    main()
