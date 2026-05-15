"""
Daily SELL signal monitor for SPY LEAPS — mirror of daily_signal_top10.py.

Each market close, checks all 10 candidate sell signals and the
RECOMMENDED composite ("COMBO_only_extreme" = S5 OR S1) against today's
SPY/VIX/RSI/MACD/BB metrics.

Outputs:
  • Market state table with each metric vs sell-trigger thresholds
  • Status of every sell rule (firing or not, with measured value)
  • A clear VERDICT: HOLD / WATCH / SELL based on the backtested top rules

Usage:
    python sell_signals/daily_sell_check.py
    python sell_signals/daily_sell_check.py --positions positions.json
    python sell_signals/daily_sell_check.py --force      # email even on quiet days
"""

from __future__ import annotations
import argparse, json, sys, os
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Core BUY-side helpers (notify, strategy_*) now live in `final_leaps/`.
sys.path.insert(0, str(PROJECT_ROOT / "final_leaps"))
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌ Missing yfinance. Run: pip install yfinance")

from notify import notify_all
from strategy_backtest import add_features
from strategy_alternatives import extend_features
from sell_signals.sell_rules import explain_sell, SELL_RULES

# Recommended composite (from sell_backtest.py results): the "extreme bearish"
# rules together catch the worst drawdowns while keeping mean P&L within ~20pts
# of HOLD_ONLY but reducing big-loss rate from 20.3% to 6.9%.
RECOMMENDED_KEYS = ["S5_NEW_60D_LOW", "S1_VIX_SPIKE"]


# ─── Data ─────────────────────────────────────────────────────────────────────

def fetch_data(period_days: int = 500) -> pd.DataFrame:
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=period_days)
    print(f"  📡 Fetching SPY + VIX from Yahoo Finance...")
    spy = yf.download("SPY", start=start, end=end, progress=False, auto_adjust=False)
    vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = vix.columns.get_level_values(0)
    df = pd.DataFrame({
        "SPY": spy["Close"].astype(float),
        "VIX": vix["Close"].astype(float),
    }).dropna()
    df.index = pd.to_datetime(df.index)
    print(f"     Got {len(df)} trading days  •  last close: {df.index[-1].date()}")
    return df


# ─── Build report ─────────────────────────────────────────────────────────────

def build_report(df: pd.DataFrame, positions: list[dict] | None = None) -> dict:
    feats = extend_features(df)
    today_idx = len(feats) - 1
    row = feats.iloc[today_idx]
    today = feats.index[today_idx]

    spy = float(row["SPY"]); vix = float(row["VIX"])
    rsi = float(row["RSI14"]); macd = float(row["macd"])
    sma50 = float(row["sma50"]); sma200 = float(row["sma200"])
    bb_w = float(row.get("bb_width_pct", 0.5))
    running_max = float(feats["SPY"].iloc[:today_idx + 1].max())
    dd = (spy / running_max - 1) * 100

    fires = []
    for key, desc in SELL_RULES:
        fired, conds = explain_sell(key, row, feats, today_idx)
        fires.append({"key": key, "desc": desc, "fired": fired, "conds": conds})

    n_fires = sum(1 for f in fires if f["fired"])
    recommended_firing = [f for f in fires if f["fired"] and f["key"] in RECOMMENDED_KEYS]
    n_recommended_fired = len(recommended_firing)

    # Verdict: if any RECOMMENDED rule fires → SELL.
    # If 2+ non-recommended rules fire → WATCH.
    # Otherwise → HOLD.
    if n_recommended_fired >= 1:
        verdict = "SELL"
        verdict_reason = ("One of the high-priority sell rules is firing. "
                          "Backtest shows these reduce drawdown variance meaningfully.")
    elif n_fires >= 3:
        verdict = "WATCH"
        verdict_reason = (f"{n_fires} sell rules firing but none are the top-priority "
                          "extreme-bearish ones. Tighten stops, don't add to position.")
    elif n_fires >= 1:
        verdict = "WATCH"
        verdict_reason = "Some sell pressure but it's noise-level. Continue to hold."
    else:
        verdict = "HOLD"
        verdict_reason = "No sell signals firing. Stay in your LEAPS."

    return {
        "date": str(today.date()),
        "spy": spy, "vix": vix, "rsi": rsi, "macd": macd,
        "sma50": sma50, "sma200": sma200,
        "bb_w": bb_w, "dd": dd, "running_max": running_max,
        "fires": fires, "n_fires": n_fires,
        "n_recommended_fired": n_recommended_fired,
        "recommended_firing": [f["key"] for f in recommended_firing],
        "verdict": verdict, "verdict_reason": verdict_reason,
        "positions": positions or [],
    }


# ─── Format text ──────────────────────────────────────────────────────────────

def format_text(r: dict, compact: bool = False) -> str:
    """Format full console report. With ``compact=True`` and verdict HOLD with
    no rules firing, omit the long per-rule breakdown (used from the combined
    BUY+SELL daily runner).
    """
    out = []
    out.append("═" * 72)
    out.append(f"  SPY LEAPS — SELL-SIGNAL SCANNER  •  {r['date']}")
    out.append("═" * 72)
    out.append("")
    out.append("  Market state (today's metrics vs sell-trigger thresholds):")
    out.append("")
    out.append(f"  {'Metric':<18}  {'Value':>10}  {'Sell threshold(s)':<32}")
    out.append("  " + "─" * 68)
    out.append(f"  {'SPY price':<18}  ${r['spy']:>9.2f}  vs 50DMA ${r['sma50']:.0f} (sell <-3% break)")
    out.append(f"  {'SPY vs 50DMA':<18}  {(r['spy']/r['sma50']-1)*100:>+9.1f}%  sell when <-3% (S3)")
    out.append(f"  {'SPY drawdown':<18}  {r['dd']:>+9.1f}%  sell when <-10% (S8)")
    out.append(f"  {'VIX':<18}  {r['vix']:>10.2f}  sell when >30 (S1) or >1.5×30d avg (S10)")
    out.append(f"  {'RSI(14)':<18}  {r['rsi']:>10.1f}  sell on overbought-reversal (S7)")
    out.append(f"  {'MACD':<18}  {r['macd']:>+10.2f}  sell on bear cross (S6, <0)")
    out.append(f"  {'BB-width %ile':<18}  {r['bb_w']*100:>9.0f}%  watch widening on downside breaks (S9)")
    out.append(f"  {'Peak SPY (ATH)':<18}  ${r['running_max']:>9.0f}  current SPY {r['dd']:+.1f}% from peak")
    out.append("")

    # Verdict banner
    icon = {"SELL": "🔴", "WATCH": "🟡", "HOLD": "🟢"}[r["verdict"]]
    out.append("  " + "═" * 68)
    out.append(f"  {icon} VERDICT: {r['verdict']}  •  {r['n_fires']}/10 sell rules firing  "
               f"({r['n_recommended_fired']} of 2 priority rules)")
    out.append(f"     {r['verdict_reason']}")
    if r["recommended_firing"]:
        out.append(f"     Priority rules firing: {', '.join(r['recommended_firing'])}")
    out.append("  " + "═" * 68)
    out.append("")

    # Open positions section
    if r["positions"]:
        out.append("  📊 YOUR OPEN POSITIONS")
        out.append("  " + "─" * 68)
        for p in r["positions"]:
            out.append(f"     {p.get('description', 'LEAPS lot')}")
            for k, v in p.items():
                if k != "description":
                    out.append(f"        {k}: {v}")
        out.append("")
        if r["verdict"] == "SELL":
            out.append(f"     ⚠️  Today's verdict is SELL — consider closing these")
        else:
            out.append(f"     ✅ Today's verdict is {r['verdict']} — keep these open")
        out.append("")

    quiet_hold = (
        compact
        and r["verdict"] == "HOLD"
        and r["n_fires"] == 0
    )

    # Per-rule breakdown
    firing = [f for f in r["fires"] if f["fired"]]
    not_firing = [f for f in r["fires"] if not f["fired"]]

    if quiet_hold:
        out.append("  ── Sell rules ──")
        out.append("     Quiet HOLD (0/10 firing) — per-rule detail omitted here.")
        out.append("     Run:  python sell_signals/daily_sell_check.py")
        out.append("")
    elif firing:
        out.append("  🔴 SELL RULES FIRING")
        out.append("  " + "─" * 68)
        for f in firing:
            priority = " 🔥 PRIORITY" if f["key"] in RECOMMENDED_KEYS else ""
            out.append(f"  🔴 {f['key']:<20}{priority}")
            out.append(f"     {f['desc']}")
            for c in f["conds"]:
                out.append(f"       {c}")
            out.append("")

    if not_firing and not quiet_hold:
        out.append("  ── Not firing today (how close they are) ──")
        out.append("")
        for f in not_firing:
            priority = " 🔥 PRIORITY" if f["key"] in RECOMMENDED_KEYS else ""
            out.append(f"  🟢 {f['key']:<20}{priority}")
            out.append(f"     {f['desc']}")
            for c in f["conds"]:
                out.append(f"       {c}")
            out.append("")

    out.append("═" * 72)
    return "\n".join(out)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--positions", default=None,
                   help="JSON file with list of open LEAPS positions to include")
    p.add_argument("--force", action="store_true",
                   help="Send email even on HOLD days (default: only SELL/WATCH)")
    p.add_argument("--quiet", action="store_true", help="Print only, no email")
    args = p.parse_args()

    positions = []
    if args.positions:
        with open(args.positions) as fh:
            positions = json.load(fh)

    df = fetch_data()
    report = build_report(df, positions=positions)
    text = format_text(report)
    print(text)

    if args.quiet:
        return

    # Send email if verdict is actionable (SELL or WATCH) OR forced
    should_send = (report["verdict"] in ("SELL", "WATCH")) or args.force
    if not should_send:
        print("  ℹ️  HOLD day — not sending email (use --force to override).")
        return

    icon = {"SELL": "🔴", "WATCH": "🟡", "HOLD": "🟢"}[report["verdict"]]
    subject = (f"{icon} SPY LEAPS SELL CHECK — {report['verdict']}  •  "
               f"{report['n_fires']}/10 rules firing  •  {report['date']}")
    short = (f"VERDICT: {report['verdict']}  •  "
             f"SPY ${report['spy']:.0f}  VIX {report['vix']:.1f}  "
             f"RSI {report['rsi']:.0f}  DD {report['dd']:+.1f}%\n"
             f"Priority firing: {', '.join(report['recommended_firing']) or 'none'}\n"
             f"{report['verdict_reason']}")
    notify_all(title=subject, message=short, body=text,
               subtitle=f"{report['date']}", priority=1 if report["verdict"] == "SELL" else 0)


if __name__ == "__main__":
    main()
