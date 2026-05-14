"""
Daily Signal Monitor — Top 10 Strategies
=========================================

Runs all 10 winning strategies daily and sends ONE email per day when any
of them fire.  The email lists which strategy(ies) flagged, why (each
condition with current values), and the exact LEAPS contract to buy.

Strategies monitored (+15% OTM 2-yr LEAPS, rotation model):
  1. D_BREAKOUT       — 60d high + SPY>200DMA + VIX<20         (8.1/yr)
  2. M_QUAL_BREAKOUT  — 60d high + VIX<18 + SPY>50DMA>200DMA   (5.8/yr)
  3. F_VIX_CRUSH      — VIX dropped 30%+ in 10d + SPY>200DMA   (2.8/yr)
  4. C_CHEAP_IV       — VIX<16 + SPY>50DMA + RSI 40-65         (8.0/yr)
  5. H_TREND_FOLLOW   — SPY>50DMA>200DMA + MACD>0 + RSI<65     (11.6/yr)
  6. L_A_OR_SQUEEZE   — A_CURRENT OR BB_SQUEEZE                (4.3/yr)
  7. I_BB_SQUEEZE     — BB<20% + breakout + SPY>200DMA + VIX<22 (2.3/yr)
  8. A_CURRENT        — 2-of-3 momentum + filters              (2.3/yr)
  9. N_FILTER_CURR    — A_CURRENT + extra filters              (1.6/yr)
 10. E_OVERSOLD       — RSI<35 + SPY>200DMA + VIX<28           (2.8/yr)

Usage:
  python daily_signal_top10.py                # check & maybe email
  python daily_signal_top10.py --force        # email even with no fires
  python daily_signal_top10.py --quiet        # just print, no email
  python daily_signal_top10.py --otm 0.15     # change OTM target (default 15%)
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌ Missing yfinance. Run: pip install yfinance")

from notify import notify_all
from strategy_backtest import (
    add_features, signals_in_window, bs_call,
    RISK_FREE_RATE, LEAPS_YEARS, COMMISSION_PER_CONTRACT,
)
from strategy_alternatives import (
    extend_features,
    rule_A_current, rule_C_cheap_iv, rule_D_breakout,
    rule_E_oversold_uptrend, rule_F_vix_crush,
    rule_H_trend_follow, rule_I_bb_squeeze,
    rule_L_squeeze_or_current, rule_M_quality_breakout, rule_N_filter_current,
)

PROJECT_DIR = Path(__file__).resolve().parent
LOG_PATH = PROJECT_DIR / "results" / "daily_top10_log.csv"
LAST_NOTIFIED_PATH = PROJECT_DIR / ".last_notified_top10.json"
DEBOUNCE_STATE_PATH = PROJECT_DIR / ".strategy_debounce.json"
DEFAULT_OTM = 0.15
DEBOUNCE_DAYS = 1            # only suppress same-day duplicates per strategy
HIGH_CONVICTION_FRESH = 3    # ≥3 fresh fires same day = "🔥 HIGH CONVICTION"

STRATEGIES = [
    # (key, rule, freq/yr, layman description)
    ("D_BREAKOUT",      rule_D_breakout,         8.1,
     "SPY hit new 60-day high with VIX still low"),
    ("M_QUAL_BREAKOUT", rule_M_quality_breakout, 5.8,
     "Quality breakout: 60d high + very low VIX + clean uptrend"),
    ("F_VIX_CRUSH",     rule_F_vix_crush,        2.8,
     "Fear collapsed: VIX dropped 30%+ in 10 days"),
    ("C_CHEAP_IV",      rule_C_cheap_iv,         8.0,
     "Options are cheap (VIX<16) and trend intact"),
    ("H_TREND_FOLLOW",  rule_H_trend_follow,    11.6,
     "Trending uptrend with MACD bullish"),
    ("L_A_OR_SQUEEZE",  rule_L_squeeze_or_current, 4.3,
     "Momentum entry OR Bollinger squeeze breakout"),
    ("I_BB_SQUEEZE",    rule_I_bb_squeeze,       2.3,
     "Bollinger Band squeeze + breakout"),
    ("A_CURRENT",       rule_A_current,          2.3,
     "2-of-3 momentum signals fired + filters pass"),
    ("N_FILTER_CURR",   rule_N_filter_current,   1.6,
     "Strict momentum entry (A_CURRENT + extra filters)"),
    ("E_OVERSOLD",      rule_E_oversold_uptrend, 2.8,
     "Oversold dip in established uptrend (RSI<35)"),
]


# ─── Data ────────────────────────────────────────────────────────────────────

def fetch_data(period_days: int = 500) -> pd.DataFrame:
    """Fetch SPY + VIX from Yahoo (need ≥252 days for BB percentile)."""
    print(f"  📡 Fetching SPY + VIX from Yahoo Finance...")
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=period_days)
    spy = yf.download("SPY",  start=start, end=end, progress=False, auto_adjust=False)
    vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)
    if spy.empty or vix.empty:
        sys.exit("❌ Yahoo returned empty data")
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


# ─── Per-strategy "why fired" explanation ───────────────────────────────────

def explain_rule(key: str, row, sigs_row) -> tuple[bool, list[str]]:
    """Returns (fired, conditions_with_values) for a rule."""
    conds = []

    def cv(label, ok, val):
        return f"{'✅' if ok else '❌'} {label}  ({val})"

    spy = row["SPY"]
    vix = row["VIX"]

    if key == "D_BREAKOUT":
        c1 = spy >= row["high60"]
        c2 = bool(row["spy_above_200"])
        c3 = vix < 20
        conds = [
            cv("SPY at new 60-day high", c1, f"SPY ${spy:.0f} vs 60d high ${row['high60']:.0f}"),
            cv("SPY > 200DMA",            c2, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 20",                c3, f"VIX {vix:.1f}"),
        ]
        return (c1 and c2 and c3), conds

    if key == "M_QUAL_BREAKOUT":
        c1 = bool(row["is_new_high60"])
        c2 = vix < 18
        c3 = bool(row["spy_above_50"])
        c4 = bool(row["spy_above_200"])
        conds = [
            cv("New 60-day high",     c1, f"SPY ${spy:.0f} vs 60d ${row['high60']:.0f}"),
            cv("VIX < 18",            c2, f"VIX {vix:.1f}"),
            cv("SPY > 50DMA",         c3, f"50DMA ${row['sma50']:.0f}"),
            cv("SPY > 200DMA",        c4, f"200DMA ${row['sma200']:.0f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "F_VIX_CRUSH":
        c1 = row["vix_crush"] >= 0.30
        c2 = bool(row["spy_above_200"])
        conds = [
            cv("VIX dropped 30%+ in 10d", c1, f"crush {row['vix_crush']*100:.0f}%, VIX {vix:.1f} (10d max {row['vix_max10']:.1f})"),
            cv("SPY > 200DMA",            c2, f"200DMA ${row['sma200']:.0f}"),
        ]
        return c1 and c2, conds

    if key == "C_CHEAP_IV":
        c1 = vix < 16
        c2 = bool(row["spy_above_50"])
        c3 = 40 <= row["RSI14"] <= 65
        conds = [
            cv("VIX < 16",            c1, f"VIX {vix:.1f}"),
            cv("SPY > 50DMA",         c2, f"50DMA ${row['sma50']:.0f}"),
            cv("RSI 40-65",           c3, f"RSI {row['RSI14']:.1f}"),
        ]
        return all([c1, c2, c3]), conds

    if key == "H_TREND_FOLLOW":
        c1 = bool(row["spy_above_50"])
        c2 = row["sma50"] > row["sma200"]
        c3 = row["macd"] > 0
        c4 = row["RSI14"] < 65
        conds = [
            cv("SPY > 50DMA",         c1, f"50DMA ${row['sma50']:.0f}"),
            cv("50DMA > 200DMA",      c2, f"50DMA ${row['sma50']:.0f}, 200DMA ${row['sma200']:.0f}"),
            cv("MACD > 0",            c3, f"MACD {row['macd']:+.2f}"),
            cv("RSI < 65",            c4, f"RSI {row['RSI14']:.1f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "L_A_OR_SQUEEZE":
        # Either rule_A_current OR rule_I_bb_squeeze
        a_fired, a_conds = explain_rule("A_CURRENT", row, sigs_row)
        i_fired, i_conds = explain_rule("I_BB_SQUEEZE", row, sigs_row)
        conds = [
            f"{'✅' if a_fired else '❌'} Branch 1 — A_CURRENT fires",
            *[f"    {c}" for c in a_conds],
            f"{'✅' if i_fired else '❌'} Branch 2 — I_BB_SQUEEZE fires",
            *[f"    {c}" for c in i_conds],
        ]
        return a_fired or i_fired, conds

    if key == "I_BB_SQUEEZE":
        c1 = row["bb_width_pct"] < 0.20
        c2 = spy >= row["bb_upper"]
        c3 = bool(row["spy_above_200"])
        c4 = vix < 22
        conds = [
            cv("BB width < 20th %ile", c1, f"width %ile {row['bb_width_pct']*100:.0f}%"),
            cv("SPY ≥ upper band",     c2, f"SPY ${spy:.0f} vs upper ${row['bb_upper']:.0f}"),
            cv("SPY > 200DMA",         c3, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 22",             c4, f"VIX {vix:.1f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "A_CURRENT":
        score = int(sigs_row.get("score", 0))
        c1 = score >= 2
        c2 = bool(row["spy_above_200"])
        c3 = vix < 28
        c4 = row["RSI14"] < 65
        conds = [
            cv(f"≥2 momentum signals (MACD/RSI cross/50DMA reclaim)",
               c1, f"{score}/3"),
            cv("SPY > 200DMA",        c2, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 28",            c3, f"VIX {vix:.1f}"),
            cv("RSI < 65",            c4, f"RSI {row['RSI14']:.1f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "N_FILTER_CURR":
        a_fired, _ = explain_rule("A_CURRENT", row, sigs_row)
        c1 = a_fired
        c2 = row["vix_30d_mean"] < 22
        c3 = row["macd"] > 0
        c4 = bool(row["spy_above_50"])
        conds = [
            cv("A_CURRENT fires",     c1, ""),
            cv("VIX 30d avg < 22",    c2, f"{row['vix_30d_mean']:.1f}"),
            cv("MACD > 0",            c3, f"MACD {row['macd']:+.2f}"),
            cv("SPY > 50DMA",         c4, f"50DMA ${row['sma50']:.0f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "E_OVERSOLD":
        c1 = row["RSI14"] < 35
        c2 = bool(row["spy_above_200"])
        c3 = vix < 28
        conds = [
            cv("RSI < 35 (oversold)", c1, f"RSI {row['RSI14']:.1f}"),
            cv("SPY > 200DMA",        c2, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 28",            c3, f"VIX {vix:.1f}"),
        ]
        return all([c1, c2, c3]), conds

    return False, ["(unknown rule)"]


# ─── LEAPS Contract Suggestion ───────────────────────────────────────────────

def suggest_contract(spy: float, vix: float, otm_pct: float) -> dict:
    """Compute the +OTM% 2-year SPY call contract details.

    1Y IV is *higher* than raw VIX/100 because of the volatility term
    structure (longer-dated options price in mean-reversion to long-run
    vol).  Calibration on 10 yrs of cached SPY data shows
    IV1Y ≈ VIX × 1.33  on average (range 1.2-1.5 depending on regime).
    """
    iv = (vix / 100) * 1.33   # calibrated to historical IV1Y / VIX ratio
    strike = round(spy * (1 + otm_pct) / 5) * 5
    premium_mid = bs_call(spy, strike, LEAPS_YEARS, RISK_FREE_RATE, iv)
    premium_ask = premium_mid * (1 + 0.045 / 2)
    cost = premium_ask * 100 + COMMISSION_PER_CONTRACT
    # December expiry roughly 24 months out
    today = pd.Timestamp.today()
    target_year = today.year + 2
    return {
        "strike": strike,
        "expiry": f"Dec {target_year}",
        "premium_mid": premium_mid,
        "premium_ask": premium_ask,
        "cost": cost,
        "otm_pct": otm_pct,
    }


# ─── Reporting & Notification ────────────────────────────────────────────────

def load_debounce_state() -> dict:
    """Last-fire date per strategy, so we don't re-email during a streak."""
    if DEBOUNCE_STATE_PATH.exists():
        try:
            return json.loads(DEBOUNCE_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_debounce_state(state: dict):
    DEBOUNCE_STATE_PATH.write_text(json.dumps(state, default=str))


def build_report(df: pd.DataFrame, otm_pct: float, mode: str = "DEBOUNCED") -> dict:
    feats = extend_features(df)
    sigs  = signals_in_window(feats, 1)
    today = feats.index[-1]
    row   = feats.loc[today]
    sigs_row = sigs.loc[today] if today in sigs.index else pd.Series({"score": 0})

    spy = float(row["SPY"])
    vix = float(row["VIX"])

    debounce_state = load_debounce_state() if mode == "DEBOUNCED" else {}
    today_ts = today

    fires = []
    fresh_fires = []   # strategies that are FRESH (not within 14d of last fire)
    for key, rule, freq, layman in STRATEGIES:
        fired, conds = explain_rule(key, row, sigs_row)
        is_fresh = True
        if fired and mode == "DEBOUNCED":
            last = debounce_state.get(key)
            if last:
                last_dt = pd.Timestamp(last)
                if (today_ts - last_dt).days < DEBOUNCE_DAYS:
                    is_fresh = False
        fires.append({
            "key": key, "fired": fired, "freq": freq, "layman": layman,
            "conds": conds, "is_fresh": fired and is_fresh,
        })
        if fired and is_fresh:
            fresh_fires.append(key)

    contract = suggest_contract(spy, vix, otm_pct)

    # In DEBOUNCED mode we only notify if at least one FRESH fire happened.
    # In RAW mode we notify on any fire.  In HIGH_CONVICTION we need ≥3 fresh fires.
    n_fires = sum(1 for f in fires if f["fired"])
    n_fresh = len(fresh_fires)

    if mode == "RAW":
        any_actionable = n_fires > 0
    elif mode == "HIGH_CONVICTION":
        any_actionable = n_fresh >= HIGH_CONVICTION_FRESH
    else:  # DEBOUNCED
        any_actionable = n_fresh > 0

    is_high_conviction = n_fresh >= HIGH_CONVICTION_FRESH

    return {
        "date": str(today.date()),
        "today_ts": today_ts,
        "mode": mode,
        "spy": spy, "vix": vix,
        "sma50": float(row["sma50"]), "sma200": float(row["sma200"]),
        "rsi14": float(row["RSI14"]), "macd": float(row["macd"]),
        "bb_width_pct": float(row["bb_width_pct"]) * 100,
        "drawdown": float(row["drawdown"]),
        "fires": fires,
        "fresh_fires": fresh_fires,
        "any_fired": n_fires > 0,
        "any_actionable": any_actionable,
        "is_high_conviction": is_high_conviction,
        "n_fires": n_fires,
        "n_fresh": n_fresh,
        "contract": contract,
    }


def format_text_report(r: dict) -> str:
    out = []
    out.append("═" * 72)
    out.append(f"  SPY LEAPS — TOP 10 STRATEGY SCANNER  •  {r['date']}  •  Mode: {r['mode']}")
    out.append("═" * 72)
    out.append("")
    out.append("  Market state (today's metrics vs the thresholds we care about):")
    out.append("")
    out.append(f"  {'Metric':<18}  {'Value':>10}  {'Threshold(s)':<32}")
    out.append("  " + "─" * 68)
    spy_v = r["spy"];      sma50 = r["sma50"];   sma200 = r["sma200"]
    vix_v = r["vix"];      rsi   = r["rsi14"];   macd_v = r["macd"]
    bb    = r["bb_width_pct"]; dd  = r["drawdown"]
    out.append(f"  {'SPY price':<18}  ${spy_v:>9.2f}  vs 50DMA ${sma50:.0f} / 200DMA ${sma200:.0f}")
    out.append(f"  {'SPY vs 50DMA':<18}  {(spy_v/sma50-1)*100:>+9.1f}%  >0% = bullish trend intact")
    out.append(f"  {'SPY vs 200DMA':<18}  {(spy_v/sma200-1)*100:>+9.1f}%  >0% = long-term uptrend OK")
    out.append(f"  {'SPY drawdown':<18}  {dd:>+9.1f}%  >-10% = healthy / <-15% = oversold zone")
    out.append(f"  {'VIX':<18}  {vix_v:>10.2f}  <16 cheap • <20 calm • >25 fear • >30 spike")
    out.append(f"  {'RSI(14)':<18}  {rsi:>10.1f}  <35 oversold • 40-65 sweet spot • >70 overbought")
    out.append(f"  {'MACD':<18}  {macd_v:>+10.2f}  >0 = momentum positive")
    out.append(f"  {'BB-width %ile':<18}  {bb:>9.0f}%  <20% = squeeze (low vol, breakout pending)")
    out.append("")

    if r["mode"] == "DEBOUNCED":
        out.append(f"  🚦 {r['n_fires']} strategies firing  •  "
                   f"{r['n_fresh']} are FRESH (fired ≥{DEBOUNCE_DAYS}d after last fire)")
    elif r["mode"] == "HIGH_CONVICTION":
        out.append(f"  🚦 {r['n_fires']} strategies firing  •  "
                   f"{r['n_fresh']} fresh  •  need ≥3 for HIGH_CONVICTION email")
    else:
        out.append(f"  🚦 {r['n_fires']} of {len(r['fires'])} strategies firing today")
    out.append("")

    if r["any_actionable"]:
        c = r["contract"]
        out.append("  " + "─" * 68)
        if r["is_high_conviction"]:
            out.append(f"  🔥 HIGH CONVICTION DAY — {r['n_fresh']} strategies agree!")
            out.append(f"     (≥3 independent strategies firing same day — past 10 yrs of these")
            out.append(f"      averaged +44% per LEAPS trade, 88% win rate)")
            out.append(f"     → Consider 2 contracts instead of 1 if portfolio supports it")
            out.append("  " + "─" * 68)
        out.append(f"  🟢 SUGGESTED CONTRACT (+{c['otm_pct']*100:.0f}% OTM 2-yr LEAPS):")
        out.append(f"     Strike     : ${c['strike']:.0f}  (based on tonight's close)")
        out.append(f"     Expiry     : {c['expiry']}")
        out.append(f"     Mid premium: ${c['premium_mid']:.2f} / share")
        out.append(f"     Cost/cntrct: ${c['cost']:.0f}  (limit ≤ ${c['premium_ask']:.2f})")
        out.append("  " + "─" * 68)
        out.append("")
        # ── Tomorrow morning execution guide ────────────────────────────
        spy_now = r["spy"]
        out.append(f"  📌 TOMORROW MORNING EXECUTION:")
        out.append(f"     1. At 9:30am ET, look up SPY current price")
        out.append(f"     2. Recalc +15% strike  =  SPY × 1.15, rounded to nearest $5")
        out.append(f"        (Tonight's close = ${spy_now:.2f} → strike $"
                   f"{round(spy_now*1.15/5)*5:.0f}; "
                   f"adjust if SPY gaps)")
        out.append(f"     3. Wait until ~9:45am ET for spreads to tighten")
        out.append(f"     4. Sell ~{int(c['cost']/spy_now * 1.18) + 1} shares VOO "
                   f"(specific-lot, prefer loss/long-term lots)")
        out.append(f"     5. Buy 1 SPY [STRIKE] {c['expiry']} call at LIMIT ≤ mid + $0.10")
        out.append(f"     6. Set 6-month calendar reminder to check exit conditions")
        out.append(f"     ⚠️  Skip if SPY gaps down >1.5%, FOMC day, or major data release morning of")
        out.append("")

    for f in r["fires"]:
        if f["fired"]:
            tag = "🟢" if f["is_fresh"] else "⏸️ "
            note = " ✅ FRESH" if f["is_fresh"] else " (within 14d cooldown — already alerted)"
            out.append(f"  {tag} {f['key']:<18} ({f['freq']:.1f}/yr){note}")
            out.append(f"     {f['layman']}")
            for c in f["conds"]:
                out.append(f"       {c}")
            out.append("")

    not_fired = [f for f in r["fires"] if not f["fired"]]
    if not_fired:
        out.append("  ── Not firing today (full condition breakdown) ──")
        out.append("")
        for f in not_fired:
            failed = [c for c in f["conds"] if c.startswith("❌")]
            out.append(f"  🔴 {f['key']:<18} ({f['freq']:.1f}/yr) — "
                       f"{len(failed)} of {len(f['conds'])} conditions failed")
            out.append(f"     {f['layman']}")
            for c in f["conds"]:
                out.append(f"       {c}")
            out.append("")

    out.append("")
    out.append("═" * 72)
    return "\n".join(out)


def format_email_report(r: dict) -> tuple[str, str]:
    """Returns (subject, body) for email."""
    if r["any_actionable"]:
        n = r["n_fresh"] if r["mode"] != "RAW" else r["n_fires"]
        fired_keys = ", ".join(r["fresh_fires"] if r["mode"] != "RAW"
                               else [f["key"] for f in r["fires"] if f["fired"]])
        if r["is_high_conviction"]:
            subject = f"🔥 HIGH CONVICTION — {n} SPY LEAPS signals agree ({fired_keys})"
        else:
            subject = f"🟢 SPY LEAPS — {n} signal{'s' if n != 1 else ''} firing ({fired_keys})"
    else:
        subject = f"⚪️ SPY LEAPS — no fresh signals today ({r['date']})"
    body = format_text_report(r)
    return subject, body


def should_notify_again(r: dict) -> bool:
    if not LAST_NOTIFIED_PATH.exists():
        return True
    try:
        last = json.loads(LAST_NOTIFIED_PATH.read_text())
        if last.get("date") == r["date"]:
            return False
    except Exception:
        pass
    return True


def remember_notification(r: dict):
    LAST_NOTIFIED_PATH.write_text(json.dumps({
        "date": r["date"], "n_fires": r["n_fires"],
        "fired": [f["key"] for f in r["fires"] if f["fired"]],
        "sent_at": datetime.now().isoformat(),
    }))


def append_log(r: dict):
    LOG_PATH.parent.mkdir(exist_ok=True)
    flat = {
        "date": r["date"], "spy": r["spy"], "vix": r["vix"],
        "rsi": r["rsi14"], "macd": r["macd"],
        "n_fires": r["n_fires"],
        "fired": ",".join(f["key"] for f in r["fires"] if f["fired"]),
        "strike": r["contract"]["strike"],
        "contract_cost": r["contract"]["cost"],
    }
    df = pd.DataFrame([flat])
    if LOG_PATH.exists():
        df.to_csv(LOG_PATH, mode="a", header=False, index=False)
    else:
        df.to_csv(LOG_PATH, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Send notification even when no strategies fire")
    parser.add_argument("--quiet", action="store_true",
                        help="Don't send notification, just print + log")
    parser.add_argument("--otm",   type=float, default=DEFAULT_OTM,
                        help="OTM percentage for suggested contract (default 0.15)")
    parser.add_argument("--mode",  choices=["RAW", "DEBOUNCED", "HIGH_CONVICTION"],
                        default="DEBOUNCED",
                        help="Notification filter mode (default DEBOUNCED, ~43 emails/yr; "
                             "RAW ~151/yr, HIGH_CONVICTION ~10/yr)")
    args = parser.parse_args()

    df = fetch_data()
    report = build_report(df, otm_pct=args.otm, mode=args.mode)
    text = format_text_report(report)
    print(text)
    append_log(report)

    should_send = (report["any_actionable"] or args.force) and not args.quiet
    if should_send and (args.force or should_notify_again(report)):
        subject, body = format_email_report(report)
        notify_all(
            title=subject,
            message=(
                f"{report['n_fresh']} fresh strategies firing  •  "
                f"SPY ${report['spy']:.0f}  VIX {report['vix']:.1f}\n"
                f"Suggested: SPY ${report['contract']['strike']:.0f} "
                f"{report['contract']['expiry']} call @ ${report['contract']['premium_mid']:.2f}/share "
                f"(~${report['contract']['cost']:.0f}/contract)"
                if report["any_actionable"]
                else f"SPY ${report['spy']:.0f}  VIX {report['vix']:.1f}  — no fresh signals today."
            ),
            subtitle=f"{report['date']}  •  {report['n_fresh']}/10 fresh",
            priority=1 if report["any_actionable"] else 0,
        )
        if report["any_actionable"]:
            remember_notification(report)
            # Update debounce state for each fresh fire
            if report["mode"] == "DEBOUNCED":
                state = load_debounce_state()
                for k in report["fresh_fires"]:
                    state[k] = report["date"]
                save_debounce_state(state)
    elif report["any_actionable"] and not args.force:
        print("  ℹ️  Already notified for this date.")
    elif args.quiet:
        print("  🔇 Quiet mode — no notification.")
    else:
        print("  ℹ️  No strategies firing — no notification sent.")

    print(f"  📝 Logged to {LOG_PATH}\n")


if __name__ == "__main__":
    main()
