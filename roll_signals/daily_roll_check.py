"""
Daily ROLLOVER signal monitor for SPY LEAPS.

For each open LEAPS lot (from positions.json) we evaluate all 10
candidate rollover rules + a RECOMMENDED composite, against today's
SPY/VIX/IV plus the position's DTE / delta / P&L.

Output:
  • Per-position table: today's mark, P&L, delta, DTE
  • Per-rule status: firing or not, with measured value
  • Per-position VERDICT: HOLD / WATCH / ROLL

Positions file format (JSON list):
  [
    {
      "description": "Lot opened on April 8 2025",
      "entry_date":  "2025-04-08",
      "expiry":      "2027-04-08",
      "strike":      540,
      "entry_premium": 75.40,
      "contracts":   1
    }
  ]

Usage:
    python roll_signals/daily_roll_check.py --positions positions.json
    python roll_signals/daily_roll_check.py --positions positions.json --force
    python roll_signals/daily_roll_check.py --positions positions.json --quiet
"""

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "final_leaps"))
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌ Missing yfinance. Run: pip install yfinance")

from notify import notify_all
from strategy_alternatives import extend_features
from roll_signals.roll_rules import (
    ROLL_RULES, explain_roll, snap_position,
    RECOMMENDED_KEYS_DEFAULT,
)


# ─── Data ─────────────────────────────────────────────────────────────────────

def fetch_data(period_days: int = 500) -> pd.DataFrame:
    """Pull SPY + VIX from Yahoo for the lookback window."""
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=period_days)
    print("  📡 Fetching SPY + VIX from Yahoo Finance...")
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


# ─── Build per-position report ───────────────────────────────────────────────

def build_position_report(pos: dict, feats: pd.DataFrame,
                          recommended_keys: list[str]) -> dict:
    """Evaluate all rollover rules against one open position."""
    today_idx = len(feats) - 1
    row = feats.iloc[today_idx]
    snap = snap_position(pos, row)

    fires = []
    for key, desc in ROLL_RULES:
        fired, conds = explain_roll(key, snap, row, feats, today_idx)
        fires.append({"key": key, "desc": desc, "fired": fired, "conds": conds})

    n_fires = sum(1 for f in fires if f["fired"])
    recommended_firing = [f for f in fires
                          if f["fired"] and f["key"] in recommended_keys]
    n_rec = len(recommended_firing)

    if n_rec >= 1:
        verdict = "ROLL"
        reason  = ("One of the high-priority roll rules is firing. "
                   "Closing and re-opening will lock gain and reset duration.")
    elif n_fires >= 3:
        verdict = "WATCH"
        reason  = (f"{n_fires} roll rules firing but none are the top-priority "
                   "composites — re-evaluate next week.")
    elif n_fires >= 1:
        verdict = "WATCH"
        reason  = "Some roll pressure but it's noise-level — keep holding."
    else:
        verdict = "HOLD"
        reason  = "No roll signals firing. Keep this lot open."

    return {
        "description":         pos.get("description", f"Lot {pos.get('entry_date')}"),
        "entry_date":          str(snap.entry_date.date()),
        "expiry":              str(snap.expiry.date()),
        "strike":              snap.strike,
        "entry_premium":       snap.entry_premium,
        "contracts":           snap.contracts,
        "mark_now":            snap.mark_now,
        "pct_pnl":             snap.pct_pnl,
        "delta_now":           snap.delta_now,
        "dte_days":            snap.dte_days,
        "fires":               fires,
        "n_fires":             n_fires,
        "n_recommended_fired": n_rec,
        "recommended_firing":  [f["key"] for f in recommended_firing],
        "verdict":             verdict,
        "verdict_reason":      reason,
    }


# ─── Format text ─────────────────────────────────────────────────────────────

def format_text(pos_reports: list[dict], today_str: str) -> str:
    out = []
    out.append("═" * 78)
    out.append(f"  SPY LEAPS — ROLLOVER SCANNER  •  {today_str}")
    out.append("═" * 78)
    out.append("")
    if not pos_reports:
        out.append("  ℹ️  No open positions provided — nothing to roll.")
        out.append("═" * 78)
        return "\n".join(out)

    for pr in pos_reports:
        icon = {"ROLL": "🔄", "WATCH": "🟡", "HOLD": "🟢"}.get(pr["verdict"], "•")
        out.append(f"  {icon} {pr['description']}  →  VERDICT: {pr['verdict']}")
        out.append(f"     {pr['verdict_reason']}")
        out.append(f"     Lot:  ${pr['strike']:.0f} strike  •  "
                   f"opened {pr['entry_date']}  •  expires {pr['expiry']}")
        out.append(f"     Today: mark ${pr['mark_now']:.2f}/sh  •  "
                   f"P&L {pr['pct_pnl']*100:+.1f}%  •  Δ {pr['delta_now']:.2f}  "
                   f"•  {pr['dte_days']}d to expiry")
        if pr["recommended_firing"]:
            out.append(f"     🔥 Priority firing: {', '.join(pr['recommended_firing'])}")

        firing = [f for f in pr["fires"] if f["fired"]]
        if firing:
            out.append("     Rules firing:")
            for f in firing:
                out.append(f"       🔄 {f['key']:<22} {f['desc']}")
                for c in f["conds"]:
                    out.append(f"          {c}")
        out.append("")

    out.append("─" * 78)
    out.append("  Rules NOT firing (close-but-not-quite — first lot only):")
    not_firing = [f for f in pos_reports[0]["fires"] if not f["fired"]]
    for f in not_firing:
        out.append(f"     🟢 {f['key']:<22} {f['desc']}")
        for c in f["conds"]:
            out.append(f"        {c}")
    out.append("═" * 78)
    return "\n".join(out)


# ─── Lightweight, email-friendly section (for combined daily email) ──────────

def format_email_section(pos_reports: list[dict]) -> str:
    """Compact ROLL block for the combined daily email — only the verdicts + reasons."""
    lines = []
    actionable = [pr for pr in pos_reports
                  if pr["verdict"] in ("ROLL", "WATCH") or pr["n_fires"] > 0]
    if not actionable:
        return ""   # caller decides whether to skip entirely
    lines.append(f"  ── ROLLOVER ({len(actionable)} of {len(pos_reports)} "
                 f"lot{'s' if len(pos_reports) != 1 else ''} flagged) ──")
    for pr in actionable:
        icon = {"ROLL": "🔄", "WATCH": "🟡", "HOLD": "🟢"}.get(pr["verdict"], "•")
        lines.append(f"     {icon} {pr['description']}  →  {pr['verdict']}  "
                     f"(P&L {pr['pct_pnl']*100:+.1f}%, Δ {pr['delta_now']:.2f}, "
                     f"{pr['dte_days']}d left)")
        lines.append(f"        {pr['verdict_reason']}")
        if pr["recommended_firing"]:
            lines.append(f"        🔥 Priority firing: "
                         f"{', '.join(pr['recommended_firing'])}")
    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--positions", required=True,
                   help="JSON file listing open LEAPS lots (see header for format).")
    p.add_argument("--force", action="store_true",
                   help="Send email even on HOLD days")
    p.add_argument("--quiet", action="store_true",
                   help="Print only — no email")
    p.add_argument("--recommended", type=str,
                   default=",".join(RECOMMENDED_KEYS_DEFAULT),
                   help="Comma-separated rule keys to treat as PRIORITY "
                        "(default from roll_rules.RECOMMENDED_KEYS_DEFAULT)")
    args = p.parse_args()

    with open(args.positions) as fh:
        positions = json.load(fh)
    if not isinstance(positions, list) or not positions:
        sys.exit("❌ positions.json must be a non-empty JSON list.")

    recommended = [r.strip() for r in args.recommended.split(",") if r.strip()]

    df = fetch_data()
    feats = extend_features(df)

    pos_reports = [build_position_report(p, feats, recommended) for p in positions]

    text = format_text(pos_reports, str(feats.index[-1].date()))
    print(text)

    if args.quiet:
        return

    verdicts = [pr["verdict"] for pr in pos_reports]
    should_send = any(v in ("ROLL", "WATCH") for v in verdicts) or args.force
    if not should_send:
        print("  ℹ️  All HOLD — not sending email (use --force to override).")
        return

    icon = "🔄" if "ROLL" in verdicts else ("🟡" if "WATCH" in verdicts else "🟢")
    subject = (f"{icon} SPY LEAPS ROLL CHECK — "
               f"{sum(v == 'ROLL' for v in verdicts)} ROLL, "
               f"{sum(v == 'WATCH' for v in verdicts)} WATCH  •  "
               f"{feats.index[-1].date()}")
    short_lines = [
        (f"{pr['description'][:30]}: {pr['verdict']}  "
         f"P&L {pr['pct_pnl']*100:+.1f}%  Δ {pr['delta_now']:.2f}  "
         f"DTE {pr['dte_days']}d")
        for pr in pos_reports
    ]
    notify_all(title=subject, message="\n".join(short_lines), body=text,
               subtitle=f"{feats.index[-1].date()}",
               priority=1 if "ROLL" in verdicts else 0)


if __name__ == "__main__":
    main()
