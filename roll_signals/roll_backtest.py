"""
Back-test of the 10 ROLLOVER rules vs a "never-roll" baseline.

For every BUY entry produced by the top-10 buy strategies we simulate
the trade forward one day at a time and ask, at each step:

    "Would rule R_x fire today?"

If yes:
  • Close the current LEAPS at the bid (model: BS price × (1 − spread/2)).
  • Immediately open a new +15% OTM 2-year LEAPS at the ask.
  • Continue holding the *new* lot under the same exit rules until either
    another roll fires OR a sell-side exit fires OR end-of-data.

The baseline is `NEVER_ROLL` — i.e. hold the original lot until the
standard sell-side exit fires.

Output:
  • Console table — total $ NAV, win rate, # rolls, avg held per leg.
  • CSV: `roll_signals/roll_backtest_results.csv` for further analysis.

Usage:
    cd /Users/briansang/Desktop/stock_80_20_leaps
    python roll_signals/roll_backtest.py
    python roll_signals/roll_backtest.py --years 10 --per-lot 7500
    python roll_signals/roll_backtest.py --rules R1_PROFIT_50,R7_PROFIT_AND_CAL
"""

from __future__ import annotations
import argparse, sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import numpy as np
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "final_leaps"))
sys.path.insert(0, str(PROJECT_ROOT))

from strategy_backtest import (
    load_data, signals_in_window, bs_call,
    RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
    EXIT_DD_50DMA, EXIT_VIX_HIGH, EXIT_VIX_SLOPE, EXIT_NEAR_EXP,
)
from strategy_alternatives import extend_features
from daily_signal_top10 import STRATEGIES, explain_rule, HIGH_CONVICTION_FRESH
from roll_signals.roll_rules import (
    ROLL_RULES, explain_roll, snap_position, bs_call_px, bs_call_delta,
)


# ─── CONFIG (kept inline so this file is self-documenting) ───────────────────
CONFIG = {
    "otm_pct":         0.15,
    "per_lot":         7_500,         # $ deployed per (re)entry
    "leaps_years":     LEAPS_YEARS,   # 2.0 by default
    "min_hold_days":   180,           # sell-side stop-out floor
    "max_hold_days":   500,           # forced sell after this many days
    "hc_threshold":    HIGH_CONVICTION_FRESH,  # ≥3 strategies = HC entry
    "debounce_days":   14,
    "default_years":   10,
    "output_csv":      "roll_backtest_results.csv",
}


# ─── Helpers — sigma / spread / sell-side stop ───────────────────────────────

def _sigma_and_spread(row) -> tuple[float, float]:
    sigma = (float(row["IV1Y_cal"])
             if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"])
             else float(row["VIX"]) / 100.0)
    spread = (float(row["spread"])
              if "spread" in row and pd.notna(row["spread"])
              else 0.04)
    return sigma, spread


def _sellside_exit(row, leg_entry_date: pd.Timestamp, expiry: pd.Timestamp,
                   d: pd.Timestamp) -> tuple[bool, str]:
    """Return (should_exit, reason) — mirrors strategy_backtest exits."""
    T_rem = max((expiry - d).days / 365.25, 1e-6)
    if T_rem <= EXIT_NEAR_EXP:
        return True, "Near expiry"
    held = (d - leg_entry_date).days
    if held >= CONFIG["max_hold_days"]:
        return True, "Max hold"
    if held >= CONFIG["min_hold_days"]:
        if float(row["SPY"]) < float(row["sma50"]) * EXIT_DD_50DMA:
            return True, "SPY broke 50DMA"
        if float(row["VIX"]) > EXIT_VIX_HIGH:
            return True, f"VIX>{EXIT_VIX_HIGH}"
        if float(row["vix_slope5"]) > EXIT_VIX_SLOPE:
            return True, f"VIX +{EXIT_VIX_SLOPE}/5d"
    return False, ""


# ─── BUY-entry detection (re-uses production top-10 strategies) ──────────────

def find_buy_entries(feats: pd.DataFrame, sigs: pd.DataFrame,
                     start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Return every day where ≥1 buy strategy fired (after a 14-d debounce
    on the *most recent* fire, so we don't double-count back-to-back days)."""
    out = []
    last = None
    window = feats.loc[start:end]
    for d, row in tqdm(window.iterrows(), total=len(window),
                       desc="  Finding BUY entries", ncols=80):
        sigs_row = sigs.loc[d] if d in sigs.index else pd.Series({"score": 0})
        fired = 0
        for s in STRATEGIES:
            try:
                if explain_rule(s.key, row, sigs_row)[0]:
                    fired += 1
                    break
            except (KeyError, TypeError):
                pass
        if fired and (last is None
                      or (d - last).days >= CONFIG["debounce_days"]):
            out.append(d); last = d
    return out


# ─── Per-leg simulation with a given roll rule ───────────────────────────────

@dataclass
class Leg:
    entry_date: pd.Timestamp
    expiry:     pd.Timestamp
    strike:     float
    entry_premium: float
    contracts:  int
    cost:       float
    exit_date:  pd.Timestamp | None = None
    exit_value: float = np.nan
    exit_reason: str = ""


def _open_leg(date: pd.Timestamp, row, capital: float) -> Leg | None:
    """Open a fresh +15% OTM LEAPS at the ask using `capital` dollars."""
    spy = float(row["SPY"])
    sigma, spread = _sigma_and_spread(row)
    strike = round(spy * (1 + CONFIG["otm_pct"]) / 5) * 5
    ask = bs_call(spy, strike, CONFIG["leaps_years"], RISK_FREE_RATE, sigma) * (1 + spread / 2)
    if ask <= 0:
        return None
    contracts = max(int(capital / (ask * 100)), 1)
    cost = contracts * ask * 100 + contracts * COMMISSION_PER_CONTRACT
    expiry = date + pd.Timedelta(days=int(CONFIG["leaps_years"] * 365))
    return Leg(entry_date=date, expiry=expiry, strike=strike,
               entry_premium=ask, contracts=contracts, cost=cost)


def _close_leg_at(leg: Leg, d: pd.Timestamp, row, reason: str) -> Leg:
    """Mark-to-market a leg at the bid on day `d` and record exit info."""
    spy = float(row["SPY"])
    sigma, spread = _sigma_and_spread(row)
    T_rem = max((leg.expiry - d).days / 365.25, 1e-6)
    bid = bs_call(spy, leg.strike, T_rem, RISK_FREE_RATE, sigma) * (1 - spread / 2)
    leg.exit_date = d
    leg.exit_value = bid * 100 * leg.contracts - leg.contracts * COMMISSION_PER_CONTRACT
    leg.exit_reason = reason
    return leg


def simulate_one_entry(feats: pd.DataFrame, entry_date: pd.Timestamp,
                       rule_key: str | None) -> list[Leg]:
    """Simulate a single BUY entry forward.  `rule_key=None` -> never-roll baseline.

    Returns a list of legs (≥1; first leg is the original buy, subsequent
    legs are rollovers).
    """
    legs: list[Leg] = []
    row0 = feats.loc[entry_date]
    leg  = _open_leg(entry_date, row0, CONFIG["per_lot"])
    if leg is None:
        return []
    legs.append(leg)

    future = feats.loc[entry_date + pd.Timedelta(days=1):].index

    while True:
        cur = legs[-1]
        rolled = False
        exited = False
        for d in future[future > cur.entry_date]:
            row = feats.loc[d]
            today_idx = feats.index.get_loc(d)

            # ── 1. Always check sell-side stop (overrides rolls) ─────────────
            stop, reason = _sellside_exit(row, cur.entry_date, cur.expiry, d)
            if stop:
                _close_leg_at(cur, d, row, reason)
                exited = True
                break

            # ── 2. Check the candidate roll rule on the *current* leg ───────
            if rule_key is not None:
                pos_dict = dict(
                    entry_date    = cur.entry_date,
                    expiry        = cur.expiry,
                    strike        = cur.strike,
                    entry_premium = cur.entry_premium,
                    contracts     = cur.contracts,
                )
                snap = snap_position(pos_dict, row, r=RISK_FREE_RATE)
                fired, _ = explain_roll(rule_key, snap, row, feats, today_idx)
                if fired:
                    # Close current, open a new leg with the *realised* $$.
                    _close_leg_at(cur, d, row, f"ROLL:{rule_key}")
                    new_capital = max(cur.exit_value, 0.0)
                    if new_capital < 100:   # too little left to redeploy
                        exited = True
                        break
                    new_leg = _open_leg(d, row, new_capital)
                    if new_leg is None:
                        exited = True
                        break
                    legs.append(new_leg)
                    rolled = True
                    break       # restart inner loop scanning from new leg

        if exited or not rolled:
            # If we fell off the end of `future` without exiting, MTM at last close.
            if not exited:
                last_d = feats.index[-1]
                last_row = feats.iloc[-1]
                _close_leg_at(cur, last_d, last_row, "still open")
            break

    return legs


# ─── Aggregate stats per rule ────────────────────────────────────────────────

def summarise_legs(all_legs_per_entry: list[list[Leg]]) -> dict:
    """Roll up leg-level data into per-rule headline stats."""
    flat = [l for legs in all_legs_per_entry for l in legs]
    if not flat:
        return {"n_entries": 0, "n_legs": 0, "n_rolls": 0,
                "invested": 0, "realized": 0, "net": 0,
                "win_rate": float("nan"), "avg_pct": float("nan"),
                "avg_held": float("nan")}
    # invested = ONLY the original entries' cost (rolls re-use realized $$).
    invested = sum(legs[0].cost for legs in all_legs_per_entry if legs)
    # realized = exit_value of the LAST leg per entry (the live/closed position).
    realized = sum(legs[-1].exit_value for legs in all_legs_per_entry if legs)
    n_legs   = len(flat)
    n_rolls  = sum(max(0, len(legs) - 1) for legs in all_legs_per_entry)
    # Wins: each ORIGINAL entry counts as a win if final realised > original cost.
    wins = sum(1 for legs in all_legs_per_entry
               if legs and legs[-1].exit_value > legs[0].cost)
    n_e  = len(all_legs_per_entry)
    avg_pct = np.mean([
        (legs[-1].exit_value - legs[0].cost) / legs[0].cost
        for legs in all_legs_per_entry if legs and legs[0].cost > 0
    ]) if all_legs_per_entry else float("nan")
    avg_held = np.mean([
        (l.exit_date - l.entry_date).days
        for l in flat if l.exit_date is not None
    ]) if flat else float("nan")
    return {
        "n_entries": n_e,
        "n_legs": n_legs,
        "n_rolls": n_rolls,
        "invested": invested,
        "realized": realized,
        "net": realized - invested,
        "win_rate": wins / n_e * 100 if n_e else float("nan"),
        "avg_pct": avg_pct * 100 if avg_pct == avg_pct else float("nan"),
        "avg_held": avg_held,
    }


# ─── Main back-test loop ─────────────────────────────────────────────────────

def run_backtest(feats: pd.DataFrame, entries: list[pd.Timestamp],
                 rules: list[str]) -> pd.DataFrame:
    """For each rule, walk every entry forward with that rule applied.

    Returns a tidy summary DataFrame (one row per rule, including baseline).
    """
    summaries = {}
    rules_to_run = ["NEVER_ROLL"] + rules
    for rk in rules_to_run:
        key = None if rk == "NEVER_ROLL" else rk
        per_entry: list[list[Leg]] = []
        for d in tqdm(entries, desc=f"  Rule {rk:<20}", ncols=80, leave=False):
            per_entry.append(simulate_one_entry(feats, d, key))
        summaries[rk] = summarise_legs(per_entry)

    rows = []
    for rk, s in summaries.items():
        rows.append({"rule": rk, **s})
    df = pd.DataFrame(rows).sort_values("net", ascending=False).reset_index(drop=True)
    return df


def print_table(df: pd.DataFrame, baseline_net: float):
    print("\n" + "═" * 95)
    print("  ROLLOVER RULE BACK-TEST  •  vs NEVER_ROLL baseline")
    print("═" * 95)
    hdr = (f"  {'Rule':<22}{'Entries':>9}{'Rolls':>7}{'WinRt':>7}"
           f"{'Avg P&L':>10}{'AvgHeld':>10}{'Invested':>12}"
           f"{'Realized':>12}{'Net':>12}{'Δ vs base':>12}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for r in df.itertuples():
        diff = r.net - baseline_net
        sign = "+" if diff >= 0 else ""
        print(
            f"  {r.rule:<22}{r.n_entries:>9}{r.n_rolls:>7}"
            f"{r.win_rate:>6.0f}%"
            f"{r.avg_pct:>+9.1f}%"
            f"{r.avg_held:>8.0f}d"
            f"  ${r.invested:>9,.0f}"
            f"  ${r.realized:>9,.0f}"
            f"  ${r.net:>+9,.0f}"
            f"  ${sign}{diff:>+9,.0f}"
        )
    print("═" * 95)
    print("  Notes:")
    print("    • Δ vs base = net P&L of this rule minus the NEVER_ROLL baseline.")
    print("    • Positive Δ ⇒ the rule beat hold-to-stop on the same entry set.")
    print("    • Each 'roll' realises the current leg at the bid and opens a")
    print("      new +15% OTM 2-year LEAPS at the ask using that realised $$.")
    print("═" * 95 + "\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--years",   type=float, default=CONFIG["default_years"],
                   help=f"Look-back window in years (default {CONFIG['default_years']})")
    p.add_argument("--per-lot", type=float, default=CONFIG["per_lot"],
                   help=f"$ deployed per ORIGINAL entry (default {CONFIG['per_lot']})")
    p.add_argument("--rules",   type=str,   default=None,
                   help="Comma-separated rule keys to test "
                        "(default: all 10 roll rules + baseline)")
    args = p.parse_args()
    CONFIG["per_lot"] = args.per_lot

    print("\n" + "═" * 95)
    print("  ROLLOVER BACK-TEST — top-10 candidate rules vs never-roll baseline")
    print("═" * 95)
    print(f"  Config:")
    for k, v in CONFIG.items():
        print(f"     {k:<18} {v}")

    df_raw = load_data()
    feats  = extend_features(df_raw)
    sigs   = signals_in_window(feats, 1)

    end   = feats.index[-1]
    start = end - pd.Timedelta(days=int(365 * args.years))
    print(f"\n  Period: {start.date()} → {end.date()}  ({args.years:.1f} years)")

    entries = find_buy_entries(feats, sigs, start, end)
    print(f"  Found {len(entries)} BUY entries after "
          f"{CONFIG['debounce_days']}-day debounce.")
    if not entries:
        print("  ⚠️  No entries in window — exiting."); return

    if args.rules:
        rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    else:
        rules = [k for k, _ in ROLL_RULES]

    summary = run_backtest(feats, entries, rules)
    baseline_net = float(
        summary.loc[summary["rule"] == "NEVER_ROLL", "net"].iloc[0]
    )
    print_table(summary, baseline_net)

    out = Path(__file__).resolve().parent / CONFIG["output_csv"]
    summary.to_csv(out, index=False)
    print(f"  💾 Saved per-rule summary: {out}\n")

    # Friendly call-out of the best 2 rules
    challengers = summary[summary["rule"] != "NEVER_ROLL"].head(2)
    if len(challengers):
        names = list(challengers["rule"])
        print(f"  ⭐ Top-performing roll rules vs baseline: {', '.join(names)}")
        print(f"     Update RECOMMENDED_KEYS in `roll_signals/daily_roll_check.py`")
        print(f"     if you want the daily scanner to flag these as 🔥 PRIORITY.\n")


if __name__ == "__main__":
    main()
