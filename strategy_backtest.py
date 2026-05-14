"""
Strategy A — SPY LEAPS Tactical Backtest
=========================================

Buys 2-year ATM SPY call options when momentum reset signals fire while
the market is in an established uptrend with low fear.

Three frequency profiles available (change FREQUENCY_PROFILE below):
- STRICT     : ~1 trade/yr   — high conviction, long hold
- BALANCED   : ~2-3 trades/yr — recommended sweet spot
- AGGRESSIVE : ~4-6 trades/yr — more action, more whipsaws

Usage:
    python strategy_backtest.py --profile BALANCED
    python strategy_backtest.py --profile STRICT
    python strategy_backtest.py --profile AGGRESSIVE
"""

from __future__ import annotations
import argparse, math, os, sys
import pandas as pd
import numpy as np
from scipy.stats import norm
from dataclasses import dataclass
from typing import Callable

# ─── PROFILE CONFIGURATION ───────────────────────────────────────────────────
PROFILES = {
    "STRICT": {
        "cross_window": 1,        # signal must fire TODAY (strict cross)
        "min_hold_days": 180,
        "max_hold_days": 500,
        "cooldown_days": 30,
        "vix_max_entry": 28,
        "rsi_max_entry": 65,
    },
    "BALANCED": {
        "cross_window": 5,        # any of 3 signals fired within last 5 days
        "min_hold_days": 90,
        "max_hold_days": 365,
        "cooldown_days": 21,
        "vix_max_entry": 28,
        "rsi_max_entry": 65,
    },
    "AGGRESSIVE": {
        "cross_window": 10,
        "min_hold_days": 45,
        "max_hold_days": 240,
        "cooldown_days": 14,
        "vix_max_entry": 30,
        "rsi_max_entry": 70,
    },
}

# Common parameters
START_CAPITAL   = 8000
RISK_FREE_RATE  = 0.03
LEAPS_YEARS     = 2.0
EXIT_DD_50DMA   = 0.97      # exit when SPY < 50DMA × 0.97 (3% below)
EXIT_VIX_HIGH   = 30        # exit when VIX > 30
EXIT_VIX_SLOPE  = 6         # exit when VIX 5-day change > +6
EXIT_NEAR_EXP   = 4 / 12    # exit when option has < 4 months left

# Real-world frictions
COMMISSION_PER_CONTRACT = 0.65    # typical broker fee ($0.65/contract each side)

# ─── DATA + FEATURES ─────────────────────────────────────────────────────────
def load_data(path: str = "data_cache/term_structure.csv") -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
    df = df.dropna(subset=["SPY", "VIX"])
    return df

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma50"]  = df["SPY"].rolling(50).mean()
    df["sma200"] = df["SPY"].rolling(200).mean()
    df["vix_slope5"] = df["VIX"] - df["VIX"].shift(5)

    # RSI 14 (Wilder smoothing approximation via simple averages)
    delta = df["SPY"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # MACD 12/26/9 (true EMAs)
    ema12 = df["SPY"].ewm(span=12, adjust=False).mean()
    ema26 = df["SPY"].ewm(span=26, adjust=False).mean()
    df["macd"]     = ema12 - ema26
    df["macd_sig"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Cross detection (TODAY vs YESTERDAY)
    df["macd_cross_today"]    = (df["macd"] > df["macd_sig"]) & (df["macd"].shift(1) <= df["macd_sig"].shift(1))
    df["rsi_cross_today"]     = (df["RSI14"] >= 50) & (df["RSI14"].shift(1) < 50)
    df["spy50_reclaim_today"] = (df["SPY"] >= df["sma50"]) & (df["SPY"].shift(1) < df["sma50"].shift(1))

    df["spy_above_50"]  = df["SPY"] >= df["sma50"]
    df["spy_above_200"] = df["SPY"] >= df["sma200"]
    df["spy_peak"]      = df["SPY"].cummax()
    df["drawdown"]      = (df["SPY"] / df["spy_peak"] - 1) * 100
    return df

def signals_in_window(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """A signal is 'active' if it crossed within the last `window` days."""
    out = pd.DataFrame(index=df.index)
    out["sig_macd"]  = df["macd_cross_today"].rolling(window).sum() > 0
    out["sig_rsi"]   = df["rsi_cross_today"].rolling(window).sum() > 0
    out["sig_50dma"] = df["spy50_reclaim_today"].rolling(window).sum() > 0
    out["score"]     = out[["sig_macd", "sig_rsi", "sig_50dma"]].sum(axis=1)
    return out

# ─── BLACK-SCHOLES ───────────────────────────────────────────────────────────
def bs_call(S, K, T, r, sigma):
    if T <= 1e-9 or sigma <= 1e-9:
        return max(S - K, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)

# ─── BACKTEST ────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    entry_date:  pd.Timestamp
    exit_date:   pd.Timestamp
    entry_spy:   float
    exit_spy:    float
    entry_vix:   float
    entry_dd:    float
    contracts:   int
    cost:        float
    proceeds:    float
    pct:         float
    held_days:   int
    exit_reason: str

def run_backtest(df: pd.DataFrame, profile: str, whole_contracts: bool = True,
                 start_date: str | None = None, end_date: str | None = None):
    cfg = PROFILES[profile]
    feats = add_features(df)
    if start_date is not None:
        feats = feats.loc[start_date:]
    if end_date is not None:
        feats = feats.loc[:end_date]
    sigs  = signals_in_window(feats, cfg["cross_window"])

    cash = START_CAPITAL
    lot = None
    last_exit = None
    trades: list[Trade] = []
    equity_rows = []
    skipped_no_capital = 0

    for date, row in feats.iterrows():
        spy    = float(row["SPY"])
        sigma  = float(row["IV1Y_cal"]) if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"]) else float(row["VIX"]) / 100
        spread = float(row["spread"])   if "spread"   in row and pd.notna(row["spread"])   else 0.04
        score  = int(sigs.loc[date, "score"]) if date in sigs.index else 0
        opt_val = 0.0

        # Mark-to-market and check exits
        if lot is not None:
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
                last_exit = date
                lot = None
                opt_val = 0.0

        # Entry check
        if lot is None:
            in_cooldown = last_exit is not None and (date - last_exit).days < cfg["cooldown_days"]
            entry_ok = (
                score >= 2 and
                bool(row["spy_above_200"]) and
                row["VIX"] < cfg["vix_max_entry"] and
                row["RSI14"] < cfg["rsi_max_entry"] and
                not in_cooldown
            )
            if entry_ok:
                strike  = round(spy)
                premium = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
                if premium > 0:
                    raw_contracts = cash / (premium * 100)
                    contracts = int(raw_contracts) if whole_contracts else raw_contracts
                    if contracts >= 1:
                        buy_commission = contracts * COMMISSION_PER_CONTRACT
                        cost = contracts * premium * 100 + buy_commission
                        cash -= cost
                        lot = {
                            "strike": strike, "contracts": contracts,
                            "entry_date": date, "entry_spy": spy,
                            "entry_vix": float(row["VIX"]),
                            "entry_dd": float(row["drawdown"]),
                            "cost": cost,
                            "expiry": date + pd.Timedelta(days=int(LEAPS_YEARS * 365)),
                        }
                        opt_val = contracts * bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * 100
                    else:
                        skipped_no_capital += 1

        equity_rows.append({"date": date, "total": cash + opt_val, "in_pos": lot is not None})

    eq = pd.DataFrame(equity_rows).set_index("date")
    trades_df = pd.DataFrame([t.__dict__ for t in trades]) if trades else pd.DataFrame()
    return eq, trades_df, skipped_no_capital

# ─── SUMMARY METRICS ─────────────────────────────────────────────────────────
def summarize(eq: pd.DataFrame, trades_df: pd.DataFrame, profile_name: str):
    final = eq["total"].iloc[-1]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr  = ((final / START_CAPITAL) ** (1 / years) - 1) * 100 if final > 0 else -100
    peak  = eq["total"].cummax()
    maxdd = (eq["total"] / peak - 1).min() * 100
    n     = len(trades_df)
    wins  = (trades_df["pct"] > 0).sum() if n else 0
    avg   = trades_df["pct"].mean() * 100 if n else 0
    avg_w = trades_df[trades_df["pct"] > 0]["pct"].mean() * 100 if wins else 0
    avg_l = trades_df[trades_df["pct"] <= 0]["pct"].mean() * 100 if (n - wins) else 0

    print(f"\n{'═'*72}")
    print(f"  STRATEGY A — PROFILE: {profile_name}")
    print(f"{'─'*72}")
    print(f"  Final value     : ${final:,.0f}  (+{(final/START_CAPITAL-1)*100:.0f}%)")
    print(f"  CAGR            : {cagr:+.1f}%/yr")
    print(f"  Max drawdown    : {maxdd:.1f}%")
    print(f"  Trades          : {n} over {years:.1f} years ({n/years:.1f}/yr)")
    print(f"  Win rate        : {wins}/{n}  ({wins/max(n,1)*100:.0f}%)")
    print(f"  Avg per trade   : {avg:+.1f}%")
    print(f"  Avg win         : {avg_w:+.1f}%")
    print(f"  Avg loss        : {avg_l:+.1f}%")
    print(f"{'═'*72}\n")

    if n > 0:
        print(f"  {'#':>2}  {'BUY':>11}  {'SELL':>11}  {'Held':>5}  {'Cts':>3}  "
              f"{'SPY%':>7}  {'LEAPS%':>7}  Reason")
        print(f"  {'─'*72}")
        running = START_CAPITAL
        for i, t in enumerate(trades_df.itertuples(), 1):
            mk = "✅" if t.pct > 0 else "❌"
            running *= (1 + t.pct)
            spy_gain = (t.exit_spy / t.entry_spy - 1) * 100
            print(f"  {mk}{i:>1}  {str(t.entry_date.date()):>11}  {str(t.exit_date.date()):>11}  "
                  f"{t.held_days:>4}d  {t.contracts:>3}  {spy_gain:>+6.1f}%  {t.pct:>+6.1%}  {t.exit_reason}")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="BALANCED")
    parser.add_argument("--data", default="data_cache/term_structure.csv")
    parser.add_argument("--fractional", action="store_true",
                        help="Allow fractional contracts (unrealistic but matches old tests)")
    parser.add_argument("--out", default="results", help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        sys.exit(f"❌ Data file not found: {args.data}\n   Run data_setup.py first or supply path with --data")

    df = load_data(args.data)
    eq, trades, skipped = run_backtest(df, args.profile, whole_contracts=not args.fractional)
    summarize(eq, trades, args.profile)
    if skipped:
        print(f"  ⚠️  Skipped {skipped} entries due to insufficient capital (whole contracts only)")

    os.makedirs(args.out, exist_ok=True)
    eq.to_csv(f"{args.out}/equity_{args.profile.lower()}.csv")
    trades.to_csv(f"{args.out}/trades_{args.profile.lower()}.csv", index=False)
    print(f"  💾 Saved: {args.out}/equity_{args.profile.lower()}.csv")
    print(f"  💾 Saved: {args.out}/trades_{args.profile.lower()}.csv")

if __name__ == "__main__":
    main()
