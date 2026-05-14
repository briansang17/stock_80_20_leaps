"""
HIGH-CONVICTION LEAPS — back-test + chart
==========================================

Walks every HIGH-CONVICTION day in the past 10 years (≥3 of the 10
strategies firing same day) and simulates buying one +15% OTM 2-year
SPY LEAPS call on each.  Each trade is priced day-by-day with the same
Black-Scholes model and exit rules used by the production system.

Output:
  • Multi-panel PNG saved to `final_leaps/graphs/high_conviction_returns.png`
  • Per-trade CSV saved to `final_leaps/graphs/high_conviction_trades.csv`
  • Console summary (win rate, avg gain, total $$, best / worst trade)

Layout of the PNG:
  Panel 1 (top)    SPY price with HC entry markers (green=win, red=loss)
  Panel 2 (middle) Per-trade % gain bar chart
  Panel 3 (bottom) Cumulative $ invested vs $ realized (equity curve)

Example
-------
    cd /Users/briansang/Desktop/stock_80_20_leaps
    python final_leaps/plot_high_conviction_returns.py
    python final_leaps/plot_high_conviction_returns.py --years 5
    python final_leaps/plot_high_conviction_returns.py --per-lot 10000

    # NEW — print a side-by-side 1y / 2y / 5y / 10y comparison
    # (runs simulation once on the longest window, then slices it).
    python final_leaps/plot_high_conviction_returns.py --compare
"""

from __future__ import annotations
import argparse
from itertools import combinations
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")           # headless backend — must be set before pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from tqdm import tqdm

from strategy_backtest import (
    load_data, signals_in_window, bs_call,
    RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
)
from strategy_alternatives import extend_features
from daily_signal_top10 import STRATEGIES, explain_rule, HIGH_CONVICTION_FRESH


# ─── CONFIG ──────────────────────────────────────────────────────────────────
CONFIG = {
    "otm_pct":         0.15,          # 15% out-of-the-money strike
    "min_hold_days":   180,           # safety rules can't fire before 180d
    "max_hold_days":   500,           # force-exit after 500d
    "debounce_days":   14,            # min days between HC entries
    "per_lot":         7_500,         # $ deployed per HC entry
    "default_years":   10,
    "output_chart":    "graphs/high_conviction_returns.png",
    "output_csv":      "graphs/high_conviction_trades.csv",
    "hc_threshold":    HIGH_CONVICTION_FRESH,   # ≥3 strategies same day
}


# ─── HC DAY DETECTION ────────────────────────────────────────────────────────

def scan_all_fires(feats: pd.DataFrame, sigs: pd.DataFrame,
                   start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    """One pass over [start, end] recording the FULL fired-strategy set per day.

    Cheaper than calling find_hc_days repeatedly with different gates — the
    sweeper filters this list in pure Python instead of re-scanning."""
    window = feats.loc[start:end]
    rows = []
    for date, row in tqdm(window.iterrows(), total=len(window),
                          desc="  Scanning all fires", ncols=80):
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired = []
        for s in STRATEGIES:
            try:
                if explain_rule(s.key, row, sigs_row)[0]:
                    fired.append(s.key)
            except (KeyError, TypeError):
                pass
        rows.append({"date": date, "fired": fired, "n": len(fired)})
    return rows


def apply_gate(all_days: list[dict], threshold: int,
               require_any: list[str] | None = None,
               require_all: list[str] | None = None) -> list[dict]:
    """Filter a `scan_all_fires` list by HC threshold + anchor gates.

    require_any:  at least ONE of these must be in the firing set
    require_all:  ALL of these must be in the firing set
    """
    any_set = set(require_any or [])
    all_set = set(require_all or [])
    out = []
    for d in all_days:
        if d["n"] < threshold:
            continue
        fset = set(d["fired"])
        if any_set and not (any_set & fset):
            continue
        if all_set and not all_set.issubset(fset):
            continue
        out.append(d)
    return out


def find_hc_days(feats: pd.DataFrame, sigs: pd.DataFrame,
                 start: pd.Timestamp, end: pd.Timestamp,
                 require_any: list[str] | None = None) -> list[dict]:
    """Scan [start, end] day-by-day, return every day where ≥`hc_threshold`
    of the 10 strategies fired.

    If `require_any` is non-empty, also require that at least ONE strategy
    from that set is among the day's firing strategies (e.g. only count an
    HC day if it includes A_CHEAP_IV or C_BREAKOUT)."""
    window = feats.loc[start:end]
    hc = []
    required = set(require_any or [])
    for date, row in tqdm(window.iterrows(), total=len(window),
                          desc="  Finding HC days", ncols=80):
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired = []
        for s in STRATEGIES:
            try:
                if explain_rule(s.key, row, sigs_row)[0]:
                    fired.append(s.key)
            except (KeyError, TypeError):
                pass
        if len(fired) >= CONFIG["hc_threshold"]:
            if required and not (required & set(fired)):
                continue   # HC by count, but missing the required-any anchor
            hc.append({"date": date, "fired": fired, "n": len(fired)})
    return hc


# ─── TRADE SIMULATION ────────────────────────────────────────────────────────

def _sigma_and_spread(row) -> tuple[float, float]:
    """Pull IV + spread from the row (fall back to VIX/100 / 0.04 if missing)."""
    sigma = (float(row["IV1Y_cal"])
             if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"])
             else float(row["VIX"]) / 100)
    spread = (float(row["spread"])
              if "spread" in row and pd.notna(row["spread"])
              else 0.04)
    return sigma, spread


def simulate_trades(feats: pd.DataFrame, hc_days: list[dict]) -> pd.DataFrame:
    """For each HC day, simulate buying ~$per_lot of +15% OTM LEAPS and
    holding until our standard exit rule fires.  Returns one row per trade."""
    trades = []
    last_entry: pd.Timestamp | None = None

    for hc in tqdm(hc_days, desc="  Simulating LEAPS", ncols=80):
        date = hc["date"]
        # 14-day debounce between any two HC entries (matches production rule)
        if last_entry is not None and (date - last_entry).days < CONFIG["debounce_days"]:
            continue

        row = feats.loc[date]
        spy = float(row["SPY"])
        sigma, spread = _sigma_and_spread(row)
        strike = round(spy * (1 + CONFIG["otm_pct"]) / 5) * 5
        premium_ask = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, sigma) * (1 + spread / 2)
        if premium_ask <= 0:
            continue
        contracts = max(int(CONFIG["per_lot"] / (premium_ask * 100)), 1)
        cost = contracts * premium_ask * 100 + contracts * COMMISSION_PER_CONTRACT
        expiry = date + pd.Timedelta(days=int(LEAPS_YEARS * 365))

        # March forward until an exit rule fires
        exit_date = exit_reason = None
        exit_value = np.nan
        future = feats.loc[date + pd.Timedelta(days=1):].index
        for d in future:
            r = feats.loc[d]
            T_rem = max((expiry - d).days / 365.25, 1e-6)
            s_now = float(r["SPY"])
            sig_now, sp_now = _sigma_and_spread(r)
            mark_bid = bs_call(s_now, strike, T_rem, RISK_FREE_RATE, sig_now) * (1 - sp_now / 2)
            mtm = mark_bid * 100 * contracts
            held = (d - date).days

            sell, reason = False, ""
            if T_rem <= EXIT_NEAR_EXP:
                sell, reason = True, "Near expiry"
            elif held >= CONFIG["max_hold_days"]:
                sell, reason = True, "Max hold"
            elif held >= CONFIG["min_hold_days"]:
                if s_now < r["sma50"] * EXIT_DD_50DMA:
                    sell, reason = True, "SPY broke 50DMA"
                elif r["VIX"] > EXIT_VIX_HIGH:
                    sell, reason = True, f"VIX>{EXIT_VIX_HIGH}"
                elif r["vix_slope5"] > EXIT_VIX_SLOPE:
                    sell, reason = True, f"VIX +{EXIT_VIX_SLOPE}/5d"
            if sell:
                exit_date, exit_reason = d, reason
                exit_value = mtm - contracts * COMMISSION_PER_CONTRACT
                break

        # Still open at end of data → mark-to-market
        if exit_date is None:
            exit_date = feats.index[-1]
            last = feats.iloc[-1]
            T_rem = max((expiry - exit_date).days / 365.25, 1e-6)
            sig_last, _ = _sigma_and_spread(last)
            exit_value = bs_call(float(last["SPY"]), strike, T_rem,
                                 RISK_FREE_RATE, sig_last) * 100 * contracts
            exit_reason = "still open"

        trades.append({
            "entry_date":    date,
            "exit_date":     exit_date,
            "entry_spy":     spy,
            "exit_spy":      float(feats.loc[exit_date, "SPY"]),
            "strike":        strike,
            "contracts":     contracts,
            "cost":          cost,
            "exit_value":    exit_value,
            "pnl":           exit_value - cost,
            "pct":           (exit_value - cost) / cost,
            "held_days":     (exit_date - date).days,
            "exit_reason":   exit_reason,
            "n_strategies":  hc["n"],
            "fired":         ", ".join(hc["fired"]),
        })
        last_entry = date

    return pd.DataFrame(trades)


# ─── SUMMARY ─────────────────────────────────────────────────────────────────

def print_summary(trades: pd.DataFrame, years: float):
    """Print a console summary of the back-test results."""
    n = len(trades)
    wins = (trades["pct"] > 0).sum()
    losses = n - wins
    total_invested = trades["cost"].sum()
    total_realized = trades["exit_value"].sum()
    net = total_realized - total_invested

    print("\n" + "═" * 78)
    print(f"  HIGH-CONVICTION LEAPS BACK-TEST  •  past {years:.1f} years")
    print("═" * 78)
    print(f"  HC entries     : {n} ({n/years:.1f}/yr)")
    print(f"  Win rate       : {wins}/{n}  ({wins/max(n,1)*100:.0f}%)")
    print(f"  Avg per trade  : {trades['pct'].mean()*100:+.1f}%")
    print(f"  Median per trd : {trades['pct'].median()*100:+.1f}%")
    print(f"  Best  / Worst  : {trades['pct'].max()*100:+.1f}%  /  "
          f"{trades['pct'].min()*100:+.1f}%")
    print(f"  Avg held       : {trades['held_days'].mean():.0f} days")
    print(f"  Total invested : ${total_invested:>11,.0f}  ({n} entries × "
          f"~${CONFIG['per_lot']:,}/each)")
    print(f"  Total realized : ${total_realized:>11,.0f}")
    print(f"  NET P&L        : ${net:>+11,.0f}  ({net/total_invested*100:+.1f}% on capital deployed)")
    # Per-trade annualised — money is held ~`avg_days` per trade, so the
    # annualised return per dollar deployed is (1+avg_pct) ^ (365/avg_days) - 1.
    avg_days = trades['held_days'].mean()
    avg_pct  = trades['pct'].mean()
    if avg_days > 0 and avg_pct > -1:
        ann_per_trade = ((1 + avg_pct) ** (365 / avg_days) - 1) * 100
        print(f"  Annualized/trd : ~{ann_per_trade:+.1f}%/yr (per-trade, "
              f"avg held {avg_days:.0f}d)")
    if losses > 0:
        worst = trades.nsmallest(3, "pct")
        print(f"\n  Three worst trades:")
        for t in worst.itertuples():
            print(f"     {t.entry_date.date()} → {t.exit_date.date()}  "
                  f"{t.pct*100:+5.1f}%  ({t.exit_reason})")
    print("═" * 78)


# ─── MULTI-WINDOW COMPARISON ─────────────────────────────────────────────────

def _window_stats(trades: pd.DataFrame, years: float, end: pd.Timestamp) -> dict:
    """Compute headline stats for trades entered in the last `years` years."""
    start = end - pd.Timedelta(days=int(365 * years))
    sub = trades[trades["entry_date"] >= start].copy()
    n = len(sub)
    if n == 0:
        return {"years": years, "n": 0}
    wins   = (sub["pct"] > 0).sum()
    losses = n - wins
    cost   = sub["cost"].sum()
    val    = sub["exit_value"].sum()
    net    = val - cost
    avg_days = sub["held_days"].mean()
    avg_pct  = sub["pct"].mean()
    ann_per_trade = (((1 + avg_pct) ** (365 / avg_days) - 1) * 100
                     if avg_days > 0 and avg_pct > -1 else float("nan"))
    return {
        "years": years, "n": n, "wins": wins, "losses": losses,
        "win_rate": wins / n * 100,
        "avg_pct": avg_pct * 100,
        "median_pct": sub["pct"].median() * 100,
        "best_pct": sub["pct"].max() * 100,
        "worst_pct": sub["pct"].min() * 100,
        "avg_days": avg_days,
        "cost": cost, "val": val, "net": net,
        "roi": net / cost * 100 if cost else float("nan"),
        "ann_per_trade": ann_per_trade,
        "still_open": int((sub["exit_reason"] == "still open").sum()),
    }


def print_multi_window(trades: pd.DataFrame, end: pd.Timestamp,
                       windows: list[float] = (1, 2, 5, 10),
                       require_any: list[str] | None = None):
    """Side-by-side comparison of HC LEAPS performance over multiple windows."""
    rows = [_window_stats(trades, y, end) for y in windows]
    gate = (f"  •  must include one of {{{','.join(require_any)}}}"
            if require_any else "")
    print("\n" + "═" * 78)
    print("  HIGH-CONVICTION LEAPS — multi-window comparison")
    print(f"  As of {end.date()}  •  per-lot ${CONFIG['per_lot']:,}  "
          f"•  OTM {CONFIG['otm_pct']*100:.0f}%  •  ≥{CONFIG['hc_threshold']}/10 strategies"
          f"{gate}")
    print("═" * 78)
    hdr = f"  {'Window':<10}{'Trades':>8}{'Win rate':>12}{'Avg/trd':>10}" \
          f"{'Best':>10}{'Worst':>10}{'Held':>8}{'Invested':>12}" \
          f"{'Net P&L':>12}{'ROI':>9}{'Ann/trd':>10}"
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for r in rows:
        if r["n"] == 0:
            print(f"  {r['years']:>3.0f}-yr     "
                  f"{'(no HC days in window)':>76}")
            continue
        open_tag = f" ({r['still_open']} open)" if r["still_open"] else ""
        print(
            f"  {r['years']:>3.0f}-yr    "
            f"{r['n']:>5}{open_tag:<3}"
            f"{r['wins']:>4}/{r['n']:<3} "
            f"{r['win_rate']:>4.0f}% "
            f"{r['avg_pct']:>+8.1f}%"
            f"{r['best_pct']:>+8.0f}%"
            f"{r['worst_pct']:>+8.0f}%"
            f"{r['avg_days']:>6.0f}d"
            f"  ${r['cost']:>9,.0f}"
            f"  ${r['net']:>+10,.0f}"
            f"{r['roi']:>+7.0f}%"
            f"{r['ann_per_trade']:>+8.1f}%"
        )
    print("═" * 78)
    print("  Notes:")
    print("    • 'Trades' counts HC entries after the 14-day debounce.")
    print("    • Open trades (held < 500 d) are marked-to-market at the last close.")
    print("    • 'Ann/trd' = annualized return per held dollar, "
          "= (1+avg_pct)^(365/avg_days) - 1.")
    print("    • Longer windows include older trades that are already closed; "
          "shorter windows can include open positions.")
    print("═" * 78 + "\n")


# ─── GATE SWEEP ──────────────────────────────────────────────────────────────

def _gate_label(threshold: int, ra: list[str] | None, rl: list[str] | None) -> str:
    """Short readable label for a gate spec (used in leaderboard rows)."""
    short = lambda k: k.split("_")[0]   # "A_CHEAP_IV" -> "A"
    if rl:
        return f"≥{threshold}  &  ALL of {{{'+'.join(short(k) for k in rl)}}}"
    if ra:
        if len(ra) == 1:
            return f"≥{threshold}  &  includes {short(ra[0])}"
        return f"≥{threshold}  &  one of {{{','.join(short(k) for k in ra)}}}"
    return f"≥{threshold}  (no anchor gate)"


def sweep_combinations(all_days: list[dict], feats: pd.DataFrame,
                       end_date: pd.Timestamp,
                       windows: list[float] = (1, 2, 5, 10)) -> pd.DataFrame:
    """Try a battery of (threshold, anchor-gate) combinations.

    Combos tested:
      • threshold ∈ {2, 3}, no anchor gate
      • threshold ∈ {2, 3}, "must include X" for each top-5 strategy
      • threshold = 3, "must include one of {X, Y}" for every top-5 pair
      • threshold = 3, "must include BOTH X and Y" for every top-3 pair

    Returns a DataFrame sorted by 10-yr net P&L (descending)."""
    keys  = [s.key for s in STRATEGIES]
    top5  = keys[:5]
    top3  = keys[:3]
    combos: list[tuple[int, list[str] | None, list[str] | None]] = []
    for thr in (2, 3):
        combos.append((thr, None, None))
        for k in top5:
            combos.append((thr, [k], None))
    for k1, k2 in combinations(top5, 2):
        combos.append((3, [k1, k2], None))
    for k1, k2 in combinations(top3, 2):
        combos.append((3, None, [k1, k2]))

    rows = []
    for thr, ra, rl in tqdm(combos, desc="  Sweeping gates", ncols=80):
        gated = apply_gate(all_days, thr, ra, rl)
        label = _gate_label(thr, ra, rl)
        if not gated:
            rows.append({"gate": label, "n_hc_days": 0, "n_trades": 0})
            continue
        trades = simulate_trades(feats, gated)
        if trades.empty:
            rows.append({"gate": label, "n_hc_days": len(gated), "n_trades": 0})
            continue
        row = {"gate": label, "n_hc_days": len(gated), "n_trades": len(trades)}
        for y in windows:
            s = _window_stats(trades, y, end_date)
            row[f"{y:.0f}y_trades"] = s.get("n", 0)
            row[f"{y:.0f}y_win"]    = s.get("win_rate", float("nan"))
            row[f"{y:.0f}y_avg"]    = s.get("avg_pct", float("nan"))
            row[f"{y:.0f}y_net"]    = s.get("net", 0.0)
            row[f"{y:.0f}y_roi"]    = s.get("roi", float("nan"))
        rows.append(row)
    df = pd.DataFrame(rows)
    if "10y_net" in df.columns:
        df = df.sort_values("10y_net", ascending=False).reset_index(drop=True)
    return df


def print_sweep_per_window(df: pd.DataFrame, top_n: int = 12,
                           windows: tuple[float, ...] = (1, 2, 5, 10)):
    """For the top-N gates (already sorted by 10y net P&L), print a compact
    per-window breakdown (rows = windows, cols = trades/win/avg/net/ROI)."""
    rows = df.head(top_n)
    print("\n" + "═" * 95)
    print(f"  PER-WINDOW BREAKDOWN — top {len(rows)} gates  •  1y / 2y / 5y / 10y")
    print("═" * 95)
    for idx, r in enumerate(rows.itertuples(), 1):
        print(f"  [{idx:>2}] {r.gate:<42}  ({int(r.n_trades)} trades, "
              f"{int(r.n_hc_days)} HC days raw)")
        print(f"       {'Window':<8}{'Trades':>9}{'Win rate':>11}"
              f"{'Avg/trd':>11}{'Net $':>14}{'ROI':>9}")
        print(f"       " + "─" * 65)
        for y in windows:
            n   = getattr(r, f"_{int(y)}y_trades", None) if False else None
            yk  = f"{int(y)}y"
            # Pull values by dict-like access so we don't depend on dataclass attrs
            d   = df.loc[r.Index].to_dict()
            n   = int(d.get(f"{yk}_trades", 0) or 0)
            wn  = d.get(f"{yk}_win", float("nan"))
            av  = d.get(f"{yk}_avg", float("nan"))
            nt  = d.get(f"{yk}_net", float("nan"))
            ro  = d.get(f"{yk}_roi", float("nan"))
            if n == 0:
                print(f"       {yk:<8}{'-':>9}{'-':>11}{'-':>11}{'-':>14}{'-':>9}")
                continue
            print(f"       {yk:<8}{n:>9}"
                  f"{wn:>9.0f}% "
                  f"{av:>+9.1f}% "
                  f"${nt:>+11,.0f}"
                  f"{ro:>+8.0f}%")
        print()
    print("═" * 95 + "\n")


def print_sweep_leaderboard(df: pd.DataFrame, top_n: int = 20):
    """Print the gate-sweep leaderboard, top + bottom rows for context."""
    print("\n" + "═" * 113)
    print("  HIGH-CONVICTION GATE SWEEP — leaderboard (sorted by 10-yr net P&L on $7,500/lot)")
    print("═" * 113)
    hdr = (f"  {'#':>3}  {'Gate':<40}"
           f"{'HC days':>9}{'Trades':>8}"
           f"{'Win10y':>9}{'Avg10y':>9}{'Net10y':>13}{'ROI10y':>9}"
           f"{'Win2y':>9}{'Win1y':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    nrows = len(df)
    take_top    = min(top_n, nrows)
    show_bottom = max(0, min(3, nrows - take_top))
    def _fmt(r, rank):
        win10 = (f"{r['10y_win']:>5.0f}%" if pd.notna(r.get('10y_win')) else "   - ")
        avg10 = (f"{r['10y_avg']:>+6.1f}%" if pd.notna(r.get('10y_avg')) else "   -  ")
        net10 = (f"${r['10y_net']:>+10,.0f}" if pd.notna(r.get('10y_net')) else "     -    ")
        roi10 = (f"{r['10y_roi']:>+6.0f}%" if pd.notna(r.get('10y_roi')) else "   -  ")
        win2  = (f"{r['2y_win']:>5.0f}%"  if pd.notna(r.get('2y_win'))  else "   - ")
        win1  = (f"{r['1y_win']:>5.0f}%"  if pd.notna(r.get('1y_win'))  else "   - ")
        print(f"  {rank:>3}  {r['gate']:<40}"
              f"{int(r['n_hc_days']):>9}{int(r['n_trades']):>8}"
              f"   {win10}  {avg10}  {net10}    {roi10}"
              f"   {win2}   {win1}")
    for i in range(take_top):
        _fmt(df.iloc[i], i + 1)
    if show_bottom:
        print("  " + "·" * (len(hdr) - 2))
        for i in range(nrows - show_bottom, nrows):
            _fmt(df.iloc[i], i + 1)
    print("═" * 113)
    print("  • 'HC days' = raw matching days before debounce.  'Trades' = after 14-day debounce.")
    print("  • Win10y = 10-yr win rate.  Win2y / Win1y = recent-window win rates.")
    print("  • Sorted by absolute 10-yr net P&L on $7,500 deployed per entry — "
          "the gate that compounds the most capital wins.")
    print("═" * 113 + "\n")


# ─── CHART ───────────────────────────────────────────────────────────────────

def plot_results(feats: pd.DataFrame, trades: pd.DataFrame, out_path: Path,
                 gate_label: str = "",
                 all_days: list[dict] | None = None):
    """Build the four-panel summary chart.

    `gate_label`: optional human-readable string of the anchor gate, shown
    in the title.  Empty string = no gate.
    `all_days`: list of {date, fired, n} from `scan_all_fires`.  If given,
    a 4th panel is drawn showing # strategies firing per day (red=0,
    orange=1-2, green=HC threshold reached) plus a yellow ★ on each
    actual trade entry — mirrors the bottom panel of FINAL_strategy_A.png.
    """
    has_signals_panel = all_days is not None and len(all_days) > 0
    if has_signals_panel:
        fig = plt.figure(figsize=(15, 13))
        gs  = fig.add_gridspec(4, 1, height_ratios=[2.0, 1.0, 1.4, 1.1],
                               hspace=0.42)
    else:
        fig = plt.figure(figsize=(15, 11))
        gs  = fig.add_gridspec(3, 1, height_ratios=[2.0, 1.0, 1.4], hspace=0.42)

    win_rate = (trades["pct"] > 0).mean() * 100
    avg_pct  = trades["pct"].mean() * 100
    total_invested = trades["cost"].sum()
    total_realized = trades["exit_value"].sum()
    net = total_realized - total_invested

    # Disable mathtext parsing in titles so '$' renders literally.
    plt.rcParams["text.usetex"]    = False
    plt.rcParams["mathtext.default"] = "regular"
    title_dollars = f"\\${net:+,.0f} on \\${total_invested:,.0f}"
    gate_suffix = f"  •  {gate_label}" if gate_label else ""
    n_anchor_plus = CONFIG["hc_threshold"] - 1
    fig.suptitle(
        f"SPY +15% OTM LEAPS bought on every HIGH-CONVICTION day  "
        f"(anchor + {n_anchor_plus} more of any 10 strategies){gate_suffix}\n"
        f"{len(trades)} trades  •  win rate {win_rate:.0f}%  •  "
        f"avg trade {avg_pct:+.1f}%  •  net {title_dollars} deployed",
        fontsize=13, fontweight="bold", y=0.995,
    )

    # ── Panel 1: SPY price with HC entry markers ─────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.plot(feats.index, feats["SPY"], color="steelblue", lw=1.0, alpha=0.85,
             label="SPY close")
    wins = trades[trades["pct"] > 0]
    losses = trades[trades["pct"] <= 0]
    ax1.scatter(wins["entry_date"], wins["entry_spy"],
                marker="^", s=70, c="#28a745", edgecolor="black",
                linewidth=0.6, label=f"HC entry — WIN  ({len(wins)})", zorder=4)
    ax1.scatter(losses["entry_date"], losses["entry_spy"],
                marker="v", s=70, c="#dc3545", edgecolor="black",
                linewidth=0.6, label=f"HC entry — LOSS ({len(losses)})", zorder=4)
    ax1.set_ylabel("SPY price ($)")
    ax1.set_title("SPY price with HIGH-CONVICTION LEAPS entries", fontsize=11)
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # ── Panel 2: per-trade % return bar chart ────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    colors = ["#28a745" if p > 0 else "#dc3545" for p in trades["pct"]]
    bars = ax2.bar(trades["entry_date"], trades["pct"] * 100,
                   color=colors, alpha=0.85, width=18)
    ax2.axhline(0, color="black", lw=0.6)
    ax2.axhline(avg_pct, color="navy", ls="--", lw=1.2,
                label=f"Mean {avg_pct:+.1f}%")
    ax2.set_ylabel("Trade return (%)")
    ax2.set_title(f"Per-trade LEAPS return  •  win rate {win_rate:.0f}%  "
                  f"•  best {trades['pct'].max()*100:+.0f}%  "
                  f"•  worst {trades['pct'].min()*100:+.0f}%", fontsize=11)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(alpha=0.3, axis="y")
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # ── Panel 3: cumulative invested vs realized equity curve ────────────────
    ax3 = fig.add_subplot(gs[2])
    sorted_t = trades.sort_values("exit_date")
    cum_invested = sorted_t["cost"].cumsum().values
    cum_realized = sorted_t["exit_value"].cumsum().values
    x_dates = sorted_t["exit_date"].values
    ax3.plot(x_dates, cum_invested, color="gray", lw=1.4,
             label=f"Total deployed   \\${cum_invested[-1]:,.0f}")
    ax3.plot(x_dates, cum_realized, color="#28a745", lw=2.0,
             label=f"Total realized   \\${cum_realized[-1]:,.0f}  "
                   f"({(cum_realized[-1]/cum_invested[-1]-1)*100:+.0f}% on capital)")
    ax3.fill_between(x_dates, cum_invested, cum_realized,
                     where=(cum_realized >= cum_invested),
                     color="#28a745", alpha=0.18, label="Profit zone")
    ax3.fill_between(x_dates, cum_invested, cum_realized,
                     where=(cum_realized <  cum_invested),
                     color="#dc3545", alpha=0.18, label="Loss zone")
    ax3.set_ylabel("$ cumulative")
    ax3.set_title("Cumulative capital deployed vs realized (sorted by exit date)",
                  fontsize=11)
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(alpha=0.3)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # ── Panel 4: signal-count-per-day with HC threshold + entry stars ────────
    if has_signals_panel:
        ax4 = fig.add_subplot(gs[3])
        thr = CONFIG["hc_threshold"]
        dates  = [d["date"] for d in all_days]
        counts = [d["n"]    for d in all_days]
        # Colour: red=0, orange=1..(thr-1), green=>=thr (HC reached)
        colors = ["#dc3545" if n == 0
                  else ("#ffc107" if n < thr else "#28a745")
                  for n in counts]
        ax4.bar(dates, counts, color=colors, width=1.2, alpha=0.95)
        ax4.axhline(thr, color="white", ls="--", lw=1.2, alpha=0.85,
                    label=f"HC threshold = {thr}")
        # Yellow stars on each ACTUAL entry day (after gate + debounce)
        if not trades.empty:
            star_y = max(counts) + 0.6
            ax4.scatter(trades["entry_date"],
                        [star_y] * len(trades),
                        marker="*", s=130, c="#FFD700",
                        edgecolor="black", linewidth=0.5, zorder=6,
                        label=f"Trade entry ({len(trades)})")
            ax4.set_ylim(0, star_y + 0.8)
        ax4.set_ylabel("# strategies firing")
        ax4.set_title(f"Signals fired per day  •  green = HC "
                      f"(≥{thr} firing)  •  orange = 1..{thr-1}  "
                      f"•  red = 0  •  ★ = trade entry",
                      fontsize=11)
        ax4.legend(loc="upper left", fontsize=9)
        ax4.grid(alpha=0.3, axis="y")
        ax4.xaxis.set_major_locator(mdates.YearLocator())
        ax4.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    print(f"\n  💾 Saved chart: {out_path}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--years",   type=float, default=CONFIG["default_years"],
                   help=f"Look-back in years (default {CONFIG['default_years']})")
    p.add_argument("--per-lot", type=float, default=CONFIG["per_lot"],
                   help=f"$ deployed per entry (default {CONFIG['per_lot']})")
    p.add_argument("--otm",     type=float, default=CONFIG["otm_pct"],
                   help=f"OTM percentage (default {CONFIG['otm_pct']})")
    p.add_argument("--compare", action="store_true",
                   help="Print 1y / 2y / 5y / 10y side-by-side comparison "
                        "(runs simulation once on 10y window, no chart).")
    p.add_argument("--windows", type=str, default="1,2,5,10",
                   help="Comma-separated list of look-back years for --compare "
                        "(default 1,2,5,10).")
    p.add_argument("--require", type=str, default="",
                   help="OR-gate: only count an HC day if AT LEAST ONE of these "
                        "strategy keys is in the firing set.  "
                        "Example: --require A_CHEAP_IV,C_BREAKOUT  "
                        "= (A fires OR C fires) AND ≥2 others fire.")
    p.add_argument("--require-all", type=str, default="",
                   help="AND-gate: only count an HC day if ALL of these strategy "
                        "keys are in the firing set.  "
                        "Example: --require-all A_CHEAP_IV,B_TREND_FOLLOW  "
                        "= A AND B fire AND ≥1 other fires.  Stricter than --require.")
    p.add_argument("--sweep", action="store_true",
                   help="Run every sensible gate combo (single anchors, pairs, "
                        "thresholds 2 and 3, AND/OR gates) and print a "
                        "leaderboard sorted by 10-yr net P&L.  No chart.")
    p.add_argument("--top", type=int, default=20,
                   help="How many leaderboard rows to show in --sweep mode "
                        "(default 20).")
    p.add_argument("--per-window", type=int, default=0,
                   help="In --sweep mode, also print a per-window (1y/2y/5y/10y) "
                        "breakdown for the top N gates (default 0 = skip).")
    args = p.parse_args()

    require_any = [s.strip() for s in args.require.split(",") if s.strip()]
    require_all = [s.strip() for s in args.require_all.split(",") if s.strip()]
    if require_any and require_all:
        sys.exit("❌ Pass either --require (OR-gate) OR --require-all (AND-gate), "
                 "not both.")

    CONFIG["per_lot"] = args.per_lot
    CONFIG["otm_pct"] = args.otm

    print("\n" + "═" * 78)
    print("  HIGH-CONVICTION LEAPS — back-test + chart")
    print("═" * 78)
    print(f"  Config:")
    for k, v in CONFIG.items():
        print(f"     {k:<18} {v}")

    df    = load_data()
    feats = extend_features(df)
    sigs  = signals_in_window(feats, 1)

    windows = [float(w) for w in args.windows.split(",") if w.strip()]
    # In --compare and --sweep we always look back over the longest window
    # so smaller windows can be sliced from the same data set.
    look_years = max(windows) if (args.compare or args.sweep) else args.years

    end_date   = feats.index[-1]
    start_date = end_date - pd.Timedelta(days=int(365 * look_years))
    print(f"\n  Period: {start_date.date()} → {end_date.date()}  "
          f"({look_years:.1f} years)")

    # ── SWEEP: one scan of all daily fires, then iterate gate combos ────────
    if args.sweep:
        all_days = scan_all_fires(feats, sigs, start_date, end_date)
        sweep_df = sweep_combinations(all_days, feats, end_date,
                                      windows=windows)
        out_dir = Path(__file__).resolve().parent
        csv_path = out_dir / "graphs" / "hc_gate_sweep.csv"
        csv_path.parent.mkdir(exist_ok=True)
        sweep_df.to_csv(csv_path, index=False)
        print(f"\n  💾 Saved sweep results: {csv_path}")
        print_sweep_leaderboard(sweep_df, top_n=args.top)
        if args.per_window > 0:
            print_sweep_per_window(sweep_df, top_n=args.per_window,
                                   windows=tuple(windows))
        return

    # Scan ALL days once (cheap), then filter to HC days for trade sim.
    # The full per-day fires list is what powers the bottom 'Signals fired' panel.
    all_days = scan_all_fires(feats, sigs, start_date, end_date)
    hc_days  = apply_gate(all_days, threshold=CONFIG["hc_threshold"],
                          require_any=require_any or None,
                          require_all=require_all or None)
    if require_all:
        extra = f"  +  ALL required: {', '.join(require_all)}"
    elif require_any:
        extra = f"  +  one of: {', '.join(require_any)}"
    else:
        extra = ""
    print(f"  Found {len(hc_days)} HC days "
          f"(≥{CONFIG['hc_threshold']} strategies firing same day{extra})")

    if not hc_days:
        print("  ⚠️  No HC days in window — exiting.")
        return

    trades = simulate_trades(feats, hc_days)
    print(f"  After {CONFIG['debounce_days']}-day debounce: "
          f"{len(trades)} unique LEAPS trades.")

    out_dir = Path(__file__).resolve().parent
    csv_path = out_dir / CONFIG["output_csv"]
    csv_path.parent.mkdir(exist_ok=True)
    trades.to_csv(csv_path, index=False)
    print(f"\n  💾 Saved trades CSV: {csv_path}")

    if args.compare:
        # Skip the chart — print the comparison table and exit.
        print_multi_window(trades, end_date, windows=windows,
                           require_any=require_any)
        return

    print_summary(trades, args.years)

    # Gate-aware filename so re-running with --require doesn't overwrite the
    # baseline chart.  e.g. require=[A_CHEAP_IV, C_BREAKOUT] → "hc_A+C.png"
    if require_all:
        short = "&".join(k.split("_")[0] for k in require_all)
        chart_path = out_dir / "graphs" / f"hc_AND_{short}.png"
        gate_label = (f"AND-gate: ALL of {{{', '.join(require_all)}}} "
                      f"must fire + ≥{CONFIG['hc_threshold'] - len(require_all)} more")
    elif require_any:
        short = "+".join(k.split("_")[0] for k in require_any)
        chart_path = out_dir / "graphs" / f"hc_{short}.png"
        anchor_str = " OR ".join(f"{k.split('_')[0]} + others"
                                 for k in require_any)
        gate_label = f"anchor ∈ {{{', '.join(require_any)}}}  ({anchor_str})"
    else:
        chart_path = out_dir / CONFIG["output_chart"]
        gate_label = ""

    # Filter all_days to the chart window so the bottom panel matches the x-axis
    plot_window = [d for d in all_days if start_date <= d["date"] <= end_date]
    plot_results(feats.loc[start_date:end_date], trades, chart_path,
                 gate_label=gate_label, all_days=plot_window)
    print()


if __name__ == "__main__":
    main()
