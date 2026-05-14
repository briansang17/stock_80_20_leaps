"""
Simulate the past year of daily signal emails.

For every trading day in the past 12 months, runs the same logic that
`daily_signal_top10.py` would have run — and prints exactly what email
you would have received that day (if any).

Shows:
  • Total emails sent
  • Per-month frequency
  • Streaks (consecutive days with signals)
  • The exact subject + which strategies fired for each email
"""

from __future__ import annotations
from collections import Counter
import pandas as pd

from strategy_backtest import load_data, signals_in_window
from strategy_alternatives import extend_features
from daily_signal_top10 import STRATEGIES, explain_rule, suggest_contract

LOOKBACK_DAYS = 365


def main():
    df = load_data()
    feats = extend_features(df)
    sigs  = signals_in_window(feats, 1)

    end_date = feats.index[-1]
    start_date = end_date - pd.Timedelta(days=LOOKBACK_DAYS)
    window = feats.loc[start_date:end_date]

    print("\n" + "═" * 110)
    print(f"  EMAIL HISTORY SIMULATION  •  past {LOOKBACK_DAYS} days  "
          f"({start_date.date()} → {end_date.date()})")
    print("═" * 110)

    emails = []   # one entry per day that would have triggered an email
    daily_counts = []

    for date, row in window.iterrows():
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired = []
        for s in STRATEGIES:
            try:
                f, _ = explain_rule(s.key, row, sigs_row)
                if f:
                    fired.append(s.key)
            except (KeyError, TypeError):
                pass

        daily_counts.append({"date": date, "n_fired": len(fired), "fired": fired})

        if fired:  # an email would have been sent
            strike = round(float(row["SPY"]) * 1.15 / 5) * 5
            emails.append({
                "date": date,
                "fired": fired,
                "n_fired": len(fired),
                "spy": float(row["SPY"]),
                "vix": float(row["VIX"]),
                "strike": strike,
            })

    # ── Top-line summary ─────────────────────────────────────────────────────
    n_trading_days = len(window)
    n_emails = len(emails)
    n_silent = n_trading_days - n_emails
    print(f"\n  Trading days in window  : {n_trading_days}")
    print(f"  Days with ≥1 signal     : {n_emails}  ({n_emails/n_trading_days*100:.0f}%)")
    print(f"  Silent days (no email)  : {n_silent}  ({n_silent/n_trading_days*100:.0f}%)")
    print(f"  Average emails per week : {n_emails/n_trading_days*5:.1f}")
    print(f"  Average emails per month: {n_emails/n_trading_days*21:.1f}")

    # ── Per-month breakdown ─────────────────────────────────────────────────
    monthly = Counter()
    for e in emails:
        monthly[e["date"].strftime("%Y-%m")] += 1
    monthly_trading = Counter()
    for d in window.index:
        monthly_trading[d.strftime("%Y-%m")] += 1

    print(f"\n  ── EMAILS PER MONTH ──")
    print(f"     {'Month':<10}  {'Trading days':>12}  {'Emails':>7}  "
          f"{'% of days':>10}  Visual")
    print("     " + "─" * 80)
    for month in sorted(monthly_trading.keys()):
        n_td = monthly_trading[month]
        n_em = monthly.get(month, 0)
        pct = n_em / n_td * 100
        bar = "█" * n_em + "·" * (n_td - n_em)
        print(f"     {month:<10}  {n_td:>12}  {n_em:>7}  {pct:>8.0f}%   {bar[:35]}")

    # ── Streaks ─────────────────────────────────────────────────────────────
    streaks = []
    cur_len = 0
    cur_start = None
    for d in daily_counts:
        if d["n_fired"] > 0:
            if cur_len == 0:
                cur_start = d["date"]
            cur_len += 1
        else:
            if cur_len > 0:
                streaks.append((cur_start, cur_len))
            cur_len = 0
    if cur_len > 0:
        streaks.append((cur_start, cur_len))

    streaks.sort(key=lambda x: -x[1])
    print(f"\n  ── LONGEST SIGNAL STREAKS ──")
    print(f"     (consecutive trading days with at least one strategy firing)")
    for start, length in streaks[:10]:
        end = pd.bdate_range(start=start, periods=length)[-1]
        print(f"     {length:>3} days   {start.date()} → {end.date()}")

    # ── Most-firing strategies ──────────────────────────────────────────────
    per_strategy = Counter()
    for e in emails:
        for s in e["fired"]:
            per_strategy[s] += 1
    print(f"\n  ── HOW OFTEN EACH STRATEGY FIRED ──")
    print(f"     {'Strategy':<17}  {'Days fired':>10}  {'% of trading days':>17}")
    print("     " + "─" * 55)
    for s in STRATEGIES:
        c = per_strategy.get(s.key, 0)
        print(f"     {s.key:<17}  {c:>10}  {c/n_trading_days*100:>15.0f}%")

    # ── Show the actual emails ──────────────────────────────────────────────
    print(f"\n  ── EVERY EMAIL YOU WOULD HAVE RECEIVED ──")
    print(f"\n  {'Date':<12}  {'SPY':>7}  {'VIX':>5}  {'Strike':>6}  "
          f"{'#':>2}  Subject")
    print("  " + "─" * 105)
    for e in emails:
        n = e["n_fired"]
        fired_str = ", ".join(e["fired"])
        if len(fired_str) > 65:
            fired_str = fired_str[:62] + "..."
        subject = f"🟢 {n} signal{'s' if n>1 else ''}: {fired_str}"
        print(f"  {str(e['date'].date()):<12}  "
              f"${e['spy']:>6.0f}  "
              f"{e['vix']:>4.1f}  "
              f"${e['strike']:>5.0f}  "
              f"{n:>2}  "
              f"{subject}")

    # ── Spam check ──────────────────────────────────────────────────────────
    print(f"\n  ── SPAM CHECK ──")
    weekly = Counter()
    for e in emails:
        weekly[e["date"].strftime("%G-W%V")] += 1
    busy_weeks = sorted(weekly.items(), key=lambda x: -x[1])[:5]
    print(f"     Busiest weeks (most emails in 5 days):")
    for week, count in busy_weeks:
        print(f"       {week}: {count} emails")
    quiet_weeks = sum(1 for c in weekly.values() if c == 0)
    print(f"     Silent weeks (0 emails): {quiet_weeks} of {len(weekly)} weeks")


if __name__ == "__main__":
    main()
