"""
Smart email simulator — mirrors the live daily_signal_top10.py logic.

Defaults match daily_signal_top10.py:
  • DEBOUNCE_DAYS         = 1   (suppress only same-day duplicates of same strategy)
  • HIGH_CONVICTION_FRESH = 3   (≥3 fresh strategies same day → 🔥 HIGH CONVICTION)

For comparison we also simulate a 14-day debounce so you can see the trade-off.

Usage:
    python simulate_email_smart.py             # past 365 days
    python simulate_email_smart.py --years 2   # past 2 years
"""

from __future__ import annotations
import argparse
import pandas as pd

from strategy_backtest import load_data, signals_in_window
from strategy_alternatives import extend_features
from daily_signal_top10 import (
    STRATEGIES, explain_rule,
    DEBOUNCE_DAYS, HIGH_CONVICTION_FRESH,
)


def simulate(window, sigs, debounce_days: int, hc_threshold: int):
    """Replays daily scan over window. Returns one row per email-day."""
    emails = []                                            # (date, fires, fresh, hc)
    last_fire = {s.key: None for s in STRATEGIES}

    for date, row in window.iterrows():
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired_today = []
        for s in STRATEGIES:
            try:
                f, _ = explain_rule(s.key, row, sigs_row)
                if f:
                    fired_today.append(s.key)
            except (KeyError, TypeError):
                pass

        if not fired_today:
            continue

        fresh = [
            s for s in fired_today
            if last_fire[s] is None or (date - last_fire[s]).days >= debounce_days
        ]

        if fresh:
            is_hc = len(fresh) >= hc_threshold
            emails.append((date, fired_today, fresh, is_hc))
            for s in fresh:
                last_fire[s] = date

    return emails


def print_email_table(emails, window, title):
    print(f"\n  " + "═" * 110)
    print(f"  {title}")
    print(f"  " + "═" * 110)
    print(f"  {'Date':<12}  {'Subject':<10}  {'SPY':>7}  {'VIX':>5}  {'+15% K':>7}  {'#fresh':>6}  Fresh strategies")
    print(f"  " + "─" * 109)
    for date, fired, fresh, is_hc in emails:
        spy = float(window.loc[date, "SPY"])
        vix = float(window.loc[date, "VIX"])
        strike = round(spy * 1.15 / 5) * 5
        flag = "🔥 HIGH" if is_hc else "🟢 fire"
        fresh_str = ", ".join(fresh)
        if len(fresh_str) > 56:
            fresh_str = fresh_str[:53] + "..."
        print(f"  {str(date.date()):<12}  {flag:<10}  "
              f"${spy:>6.0f}  {vix:>4.1f}  ${strike:>6.0f}  "
              f"{len(fresh):>6}  {fresh_str}")


def monthly_breakdown(emails):
    """Group emails by month."""
    if not emails:
        return pd.Series()
    df = pd.DataFrame({"date": [e[0] for e in emails],
                       "hc":   [e[3] for e in emails]})
    df["month"] = df["date"].dt.to_period("M")
    return df.groupby("month").agg(
        total=("date", "count"),
        high_conv=("hc", "sum"),
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=float, default=1.0,
                   help="Lookback window in years (default 1)")
    args = p.parse_args()

    df = load_data()
    feats = extend_features(df)
    sigs  = signals_in_window(feats, 1)
    end_date = feats.index[-1]
    start_date = end_date - pd.Timedelta(days=int(365 * args.years))
    window = feats.loc[start_date:end_date]
    n_trading_days = len(window)

    print("\n" + "═" * 110)
    print(f"  EMAIL SIMULATION  •  matches live daily_signal_top10.py settings")
    print(f"  DEBOUNCE_DAYS = {DEBOUNCE_DAYS}  •  HIGH_CONVICTION_FRESH = {HIGH_CONVICTION_FRESH}")
    print(f"  Period: {start_date.date()} → {end_date.date()}  ({n_trading_days} trading days)")
    print("═" * 110)

    # ── Primary simulation: matches current daily_signal_top10 defaults ─────────
    primary = simulate(window, sigs, DEBOUNCE_DAYS, HIGH_CONVICTION_FRESH)

    n_total = len(primary)
    n_hc    = sum(1 for *_, hc in primary if hc)
    n_norm  = n_total - n_hc

    print(f"\n  📨 EMAILS YOU WOULD HAVE GOTTEN ({n_total} total over the past {args.years:.1f} year{'s' if args.years != 1 else ''})")
    print(f"     ├─ 🔥 HIGH CONVICTION (≥{HIGH_CONVICTION_FRESH} fresh strategies same day): {n_hc}")
    print(f"     └─ 🟢 Normal fire alerts                                  : {n_norm}")
    print(f"\n  Cadence:")
    print(f"     • Per week (avg)     : {n_total / n_trading_days * 5:.1f}")
    print(f"     • Per month (avg)    : {n_total / n_trading_days * 21:.1f}")
    print(f"     • Annualized         : {n_total / n_trading_days * 252:.0f}")

    # ── Monthly heat-map ────────────────────────────────────────────────────────
    monthly = monthly_breakdown(primary)
    if not monthly.empty:
        print(f"\n  📅 MONTHLY BREAKDOWN")
        print(f"  {'Month':<10}  {'Total':>6}  {'HighConv':>9}  {'Bar':<30}")
        print(f"  " + "─" * 60)
        max_t = monthly["total"].max()
        for month, row in monthly.iterrows():
            bar = "█" * int(row["total"] / max_t * 25)
            print(f"  {str(month):<10}  {int(row['total']):>6}  "
                  f"{int(row['high_conv']):>9}  {bar:<30}")

    # ── Detailed list of all emails ─────────────────────────────────────────────
    print_email_table(
        primary, window,
        f"📧 FULL LIST — past year emails (1-day debounce, 🔥 = ≥3 fresh strategies)"
    )

    # ── Compare against 14-day debounce ─────────────────────────────────────────
    debounced14 = simulate(window, sigs, 14, HIGH_CONVICTION_FRESH)
    print(f"\n  " + "═" * 110)
    print(f"  📊 SETTING TRADE-OFF (same {n_trading_days}-day window)")
    print(f"  " + "═" * 110)
    print(f"""
     Debounce setting  | Total emails | High-conviction days | Action
     ──────────────────┼──────────────┼──────────────────────┼─────────────────────────
     1-day  (current)  | {n_total:>11}  | {n_hc:>20}  | One email per fresh fire
     14-day (backtest) | {len(debounced14):>11}  | {sum(1 for *_, hc in debounced14 if hc):>20}  | One per strategy / 2 weeks

     Your current setting (DEBOUNCE_DAYS=1) sends an email any day a strategy
     newly fires.  The 🔥 HIGH CONVICTION flag highlights days when ≥3 of the 10
     strategies agree — those are historically the strongest setups
     (~44%/trade, 88% win rate over 10 yrs of backtest).
""")


if __name__ == "__main__":
    main()
