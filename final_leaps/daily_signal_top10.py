"""
Daily Signal Monitor — Top 10 Strategies
=========================================

Runs all 10 winning strategies daily and sends ONE email per day when any
of them fire.  The email lists which strategy(ies) flagged, why (each
condition with current values), and the exact LEAPS contract to buy.

Strategies are ordered by **10-year after-tax edge** (biggest $$ first)
and **renamed A→J to match the rank** (A_ = best, J_ = worst).
Win rate and edge figures come from `FINAL_STRATEGY.md` backtests
(+15% OTM 2-yr LEAPS, rotation model, 2016-2026 SPY+VIX).

    Rank  Strategy           Win%   10yr edge $   Fires/yr  Idea
    ────  ─────────────────  ────   ───────────   ────────  ──────────────────
     1.   A_CHEAP_IV         82%    +$390,073      8.0      VIX<16 + RSI sweet spot
     2.   B_TREND_FOLLOW     74%    +$378,454     11.6      50>200DMA + MACD>0
     3.   C_BREAKOUT         81%    +$372,959      8.1      New 60-day high
     4.   D_QUAL_BREAKOUT    79%    +$341,693      5.8      Breakout + VIX<18
     5.   E_A_OR_SQUEEZE     77%    +$181,197      4.3      Momentum OR squeeze
     6.   F_VIX_CRUSH        68%    +$132,260      2.8      VIX -30% in 10d
     7.   G_BB_SQUEEZE       78%    +$114,301      2.3      BB squeeze breakout
     8.   H_CURRENT          70%     +$64,727      2.3      2-of-3 momentum
     9.   I_FILTER_CURR      75%     +$63,826      1.6      H_CURRENT + extra filters
    10.   J_OVERSOLD         71%     +$58,465      2.8      RSI<35 in uptrend

Historical scan default: 1,260 trading days ≈ 5 years.

This script also runs the SELL-side scanner from `sell_signals/` in the
same process so BOTH BUY and SELL verdicts are combined into ONE email.
Pass `--no-sell` to keep the email BUY-only.

Usage:
  python daily_signal_top10.py                # BUY + SELL combined email
  python daily_signal_top10.py --no-sell      # BUY only (legacy behaviour)
  python daily_signal_top10.py --force        # email even with no fires
  python daily_signal_top10.py --quiet        # just print, no email
  python daily_signal_top10.py --otm 0.15     # change OTM target (default 15%)
  python daily_signal_top10.py --scan 252     # use 1-year scan instead of 5y
  python daily_signal_top10.py --scan 0       # disable historical scan
"""

from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
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
# Repo root needs to be on sys.path so we can import the sell-side scanner
# (`sell_signals/daily_sell_check.py`) — it lives in a sibling folder.
if str(PROJECT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR.parent))
LOG_PATH = PROJECT_DIR / "results" / "daily_top10_log.csv"
LAST_NOTIFIED_PATH = PROJECT_DIR / ".last_notified_top10.json"
DEBOUNCE_STATE_PATH = PROJECT_DIR / ".strategy_debounce.json"
DEFAULT_OTM = 0.15
DEBOUNCE_DAYS = 1            # only suppress same-day duplicates per strategy
HIGH_CONVICTION_FRESH = 3    # ≥3 fresh fires same day = "🔥 HIGH CONVICTION"
# Super-HC = HC + the anchor strategy fires too.  Per the back-test (see
# final_leaps/graphs/tier_SHC_A+any2_*.png) requiring A_CHEAP_IV pushes
# 10-yr win rate from 78% to 82% and 2-yr win rate from 89% to 100%.
SUPER_HC_ANCHOR = "A_CHEAP_IV"
DEFAULT_SCAN_DAYS = 1260     # past N trading days scanned for HC days (~5 years)


@dataclass(frozen=True)
class StrategyDef:
    """One backtested entry strategy, with its historical performance figures."""
    key: str            # internal short name (D_BREAKOUT, C_CHEAP_IV, ...)
    rule: Callable      # row -> bool decision function (from strategy_alternatives)
    freq_yr: float      # average fires per year (10-yr backtest)
    win_rate: int       # historical win % at +15% OTM 2-yr LEAPS
    edge_10yr: int      # after-tax $$ edge over pure VOO DCA, 10-yr
    layman: str         # plain-English description for the email body


# Ordered by 10-year after-tax edge (biggest $$ first).  Public keys are
# renamed A→J so the prefix letter matches the rank (A_=best, J_=worst).
# The python rule_* function names from strategy_alternatives.py are kept
# intact (they are internal-only).
# Numbers from FINAL_STRATEGY.md  ($2,500/mo VOO DCA + rotation model).
STRATEGIES: list[StrategyDef] = [
    StrategyDef("A_CHEAP_IV",       rule_C_cheap_iv,           8.0, 82, 390_073,
                "Options are cheap (VIX<16) and trend intact"),
    StrategyDef("B_TREND_FOLLOW",   rule_H_trend_follow,      11.6, 74, 378_454,
                "Trending uptrend with MACD bullish"),
    StrategyDef("C_BREAKOUT",       rule_D_breakout,           8.1, 81, 372_959,
                "SPY hit new 60-day high with VIX still low"),
    StrategyDef("D_QUAL_BREAKOUT",  rule_M_quality_breakout,   5.8, 79, 341_693,
                "Quality breakout: 60d high + very low VIX + clean uptrend"),
    StrategyDef("E_A_OR_SQUEEZE",   rule_L_squeeze_or_current, 4.3, 77, 181_197,
                "Momentum entry (H_CURRENT) OR Bollinger squeeze breakout"),
    StrategyDef("F_VIX_CRUSH",      rule_F_vix_crush,          2.8, 68, 132_260,
                "Fear collapsed: VIX dropped 30%+ in 10 days"),
    StrategyDef("G_BB_SQUEEZE",     rule_I_bb_squeeze,         2.3, 78, 114_301,
                "Bollinger Band squeeze + breakout"),
    StrategyDef("H_CURRENT",        rule_A_current,            2.3, 70,  64_727,
                "2-of-3 momentum signals fired + filters pass"),
    StrategyDef("I_FILTER_CURR",    rule_N_filter_current,     1.6, 75,  63_826,
                "Strict momentum entry (H_CURRENT + extra VIX/MACD filters)"),
    StrategyDef("J_OVERSOLD",       rule_E_oversold_uptrend,   2.8, 71,  58_465,
                "Oversold dip in established uptrend (RSI<35)"),
]


# ─── Data ────────────────────────────────────────────────────────────────────

def fetch_data(scan_days: int = 0) -> pd.DataFrame:
    """Fetch SPY + VIX from Yahoo Finance.

    Need ≥252 days for BB-percentile + an extra buffer for whatever
    --scan window the user requested.
    """
    period_days = max(500, 380 + int(scan_days * 1.5))   # ~1.5 cal/trading
    print(f"  📡 Fetching SPY + VIX from Yahoo Finance ({period_days}d)...")
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

    if key == "C_BREAKOUT":
        c1 = spy >= row["high60"]
        c2 = bool(row["spy_above_200"])
        c3 = vix < 20
        conds = [
            cv("SPY at new 60-day high", c1, f"SPY ${spy:.0f} vs 60d high ${row['high60']:.0f}"),
            cv("SPY > 200DMA",            c2, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 20",                c3, f"VIX {vix:.1f}"),
        ]
        return (c1 and c2 and c3), conds

    if key == "D_QUAL_BREAKOUT":
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

    if key == "A_CHEAP_IV":
        c1 = vix < 16
        c2 = bool(row["spy_above_50"])
        c3 = 40 <= row["RSI14"] <= 65
        conds = [
            cv("VIX < 16",            c1, f"VIX {vix:.1f}"),
            cv("SPY > 50DMA",         c2, f"50DMA ${row['sma50']:.0f}"),
            cv("RSI 40-65",           c3, f"RSI {row['RSI14']:.1f}"),
        ]
        return all([c1, c2, c3]), conds

    if key == "B_TREND_FOLLOW":
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

    if key == "E_A_OR_SQUEEZE":
        # Either H_CURRENT (momentum) OR G_BB_SQUEEZE fires
        a_fired, a_conds = explain_rule("H_CURRENT", row, sigs_row)
        i_fired, i_conds = explain_rule("G_BB_SQUEEZE", row, sigs_row)
        conds = [
            f"{'✅' if a_fired else '❌'} Branch 1 — H_CURRENT fires",
            *[f"    {c}" for c in a_conds],
            f"{'✅' if i_fired else '❌'} Branch 2 — G_BB_SQUEEZE fires",
            *[f"    {c}" for c in i_conds],
        ]
        return a_fired or i_fired, conds

    if key == "G_BB_SQUEEZE":
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

    if key == "H_CURRENT":
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

    if key == "I_FILTER_CURR":
        a_fired, _ = explain_rule("H_CURRENT", row, sigs_row)
        c1 = a_fired
        c2 = row["vix_30d_mean"] < 22
        c3 = row["macd"] > 0
        c4 = bool(row["spy_above_50"])
        conds = [
            cv("H_CURRENT fires",     c1, ""),
            cv("VIX 30d avg < 22",    c2, f"{row['vix_30d_mean']:.1f}"),
            cv("MACD > 0",            c3, f"MACD {row['macd']:+.2f}"),
            cv("SPY > 50DMA",         c4, f"50DMA ${row['sma50']:.0f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "J_OVERSOLD":
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


def build_report(df: pd.DataFrame, otm_pct: float, mode: str = "DEBOUNCED",
                 scan_days: int = 0) -> dict:
    """Build the daily report dict.

    If `scan_days > 0`, also replay the past `scan_days` trading days and
    attach a `history` entry so HIGH-CONVICTION days are visible alongside
    today's fires.
    """
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
    fresh_fires = []   # strategies that are FRESH (not within DEBOUNCE_DAYS of last fire)
    for s in STRATEGIES:
        fired, conds = explain_rule(s.key, row, sigs_row)
        is_fresh = True
        if fired and mode == "DEBOUNCED":
            last = debounce_state.get(s.key)
            if last:
                last_dt = pd.Timestamp(last)
                if (today_ts - last_dt).days < DEBOUNCE_DAYS:
                    is_fresh = False
        fires.append({
            "key": s.key, "fired": fired, "freq": s.freq_yr,
            "win_rate": s.win_rate, "edge_10yr": s.edge_10yr,
            "layman": s.layman, "conds": conds,
            "is_fresh": fired and is_fresh,
        })
        if fired and is_fresh:
            fresh_fires.append(s.key)

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
    # Super-HC = HC + the anchor strategy is among the fresh fires (or, in
    # RAW mode, among today's fires).  Per back-test this jumps win rate
    # +4pp and shrinks worst-case loss from -67% to -37%.
    consider_fires = fresh_fires if mode != "RAW" else [f["key"] for f in fires if f["fired"]]
    is_super_high_conviction = (is_high_conviction
                                and SUPER_HC_ANCHOR in consider_fires)

    history = build_history_scan(feats, sigs, scan_days) if scan_days > 0 else None

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
        "is_super_high_conviction": is_super_high_conviction,
        "n_fires": n_fires,
        "n_fresh": n_fresh,
        "contract": contract,
        "history": history,
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
        if r.get("is_super_high_conviction"):
            out.append(f"  🔥🔥 SUPER HIGH CONVICTION DAY — {r['n_fresh']} strategies agree,")
            out.append(f"        anchor {SUPER_HC_ANCHOR} is among them!")
            out.append(f"     (HC + {SUPER_HC_ANCHOR} fires — past 10 yrs: ~82% win rate,")
            out.append(f"      2-yr window: 100% win rate; cheap-IV setups limit worst case)")
            out.append(f"     → Consider 2 contracts instead of 1 if portfolio supports it")
            out.append("  " + "─" * 68)
        elif r["is_high_conviction"]:
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

    # Strategies sorted best-first inside each (fired / not-fired) group.
    fired = sorted([f for f in r["fires"] if f["fired"]],
                   key=lambda f: -f["edge_10yr"])
    for f in fired:
        tag = "🟢" if f["is_fresh"] else "⏸️ "
        note = (" ✅ FRESH" if f["is_fresh"]
                else f" (within {DEBOUNCE_DAYS}d cooldown — already alerted)")
        out.append(f"  {tag} {f['key']:<18} ({f['freq']:.1f}/yr  •  "
                   f"win {f['win_rate']}%  •  10yr edge ${f['edge_10yr']:+,}){note}")
        out.append(f"     {f['layman']}")
        for c in f["conds"]:
            out.append(f"       {c}")
        out.append("")

    not_fired = sorted([f for f in r["fires"] if not f["fired"]],
                       key=lambda f: -f["edge_10yr"])
    if not_fired:
        out.append("  ── Not firing today (full condition breakdown) ──")
        out.append("")
        for f in not_fired:
            failed = [c for c in f["conds"] if c.startswith("❌")]
            out.append(f"  🔴 {f['key']:<18} ({f['freq']:.1f}/yr  •  "
                       f"win {f['win_rate']}%  •  10yr edge ${f['edge_10yr']:+,}) — "
                       f"{len(failed)} of {len(f['conds'])} conditions failed")
            out.append(f"     {f['layman']}")
            for c in f["conds"]:
                out.append(f"       {c}")
            out.append("")

    out.append("")
    out.append(format_strategy_ranking())
    out.append("")
    if r.get("history") is not None:
        out.append(format_history_scan(r["history"]))
        out.append("")
    out.append("═" * 72)
    return "\n".join(out)


def format_strategy_ranking() -> str:
    """Compact ranking table for the 10 strategies, biggest 10-yr $$ edge first."""
    lines = []
    lines.append("  ── STRATEGY RANKING (sorted by 10-yr after-tax edge — biggest $$ first) ──")
    lines.append("")
    lines.append(f"     {'#':>2}  {'Strategy':<17}  {'Win%':>4}  "
                 f"{'10yr edge':>12}  {'Fires/yr':>8}  Idea")
    lines.append("     " + "─" * 100)
    ranked = sorted(STRATEGIES, key=lambda s: -s.edge_10yr)
    for i, s in enumerate(ranked, 1):
        lines.append(f"     {i:>2}.  {s.key:<17}  {s.win_rate:>3}%  "
                     f"${s.edge_10yr:>+10,}  {s.freq_yr:>7.1f}  {s.layman}")
    return "\n".join(lines)


def format_history_scan(history: dict) -> str:
    """Format the past-N-day scan: list of high-conviction days + strategy
    contributions during that window."""
    lines = []
    lines.append(f"  ── HISTORICAL SCAN — past {history['days']} trading days "
                 f"(real data {history['start']} → {history['end']}) ──")
    lines.append("")

    hc_days = history["hc_days"]
    if not hc_days:
        lines.append(f"     ⚠️  No HIGH-CONVICTION days "
                     f"(≥{HIGH_CONVICTION_FRESH} strategies same day) in this window.")
    else:
        lines.append(f"     🔥 {len(hc_days)} HIGH-CONVICTION day"
                     f"{'s' if len(hc_days) != 1 else ''} "
                     f"(≥{HIGH_CONVICTION_FRESH} strategies firing same day):")
        lines.append("")
        lines.append(f"        {'Date':<11}  {'SPY':>7}  {'VIX':>5}  "
                     f"{'#':>2}  Strategies that agreed")
        lines.append("        " + "─" * 92)
        for d in hc_days:
            lines.append(f"        {d['date']:<11}  ${d['spy']:>6,.0f}  "
                         f"{d['vix']:>4.1f}  {d['n']:>2}  {', '.join(d['fired'])}")

    # Per-strategy fire counts across the scan window
    lines.append("")
    lines.append(f"     Fire counts (any-day, past {history['days']} trading days):")
    lines.append("")
    lines.append(f"        {'#':>2}  {'Strategy':<17}  {'Fires':>5}  {'HC contrib':>10}  Bar")
    lines.append("        " + "─" * 80)
    counts = history["fire_counts"]
    hc_credit = history["hc_credit"]
    ranked = sorted(STRATEGIES, key=lambda s: -counts.get(s.key, 0))
    max_fires = max(counts.values()) if counts else 1
    for i, s in enumerate(ranked, 1):
        n = counts.get(s.key, 0)
        hc = hc_credit.get(s.key, 0)
        bar = "█" * int(n / max(max_fires, 1) * 25)
        lines.append(f"        {i:>2}.  {s.key:<17}  {n:>5}  {hc:>10}  {bar}")
    return "\n".join(lines)


def build_history_scan(feats: pd.DataFrame, sigs: pd.DataFrame,
                       days: int) -> dict:
    """Replay all 10 strategies for the last `days` trading days and
    return a summary suitable for printing.

    Detects which days were HIGH CONVICTION (≥3 strategies firing same day)
    and tallies per-strategy fire counts + HC-contribution counts.

    This is what surfaced the April 8 buy (3 strategies agreed: F_VIX_CRUSH,
    L_A_OR_SQUEEZE, A_CURRENT).
    """
    window = feats.iloc[-days:]
    hc_days = []
    fire_counts: dict[str, int] = {s.key: 0 for s in STRATEGIES}
    hc_credit:   dict[str, int] = {s.key: 0 for s in STRATEGIES}

    for date, row in window.iterrows():
        sigs_row = sigs.loc[date] if date in sigs.index else pd.Series({"score": 0})
        fired_today = []
        for s in STRATEGIES:
            try:
                f, _ = explain_rule(s.key, row, sigs_row)
                if f:
                    fired_today.append(s.key)
                    fire_counts[s.key] += 1
            except (KeyError, TypeError):
                pass
        if len(fired_today) >= HIGH_CONVICTION_FRESH:
            hc_days.append({
                "date":  str(date.date()),
                "spy":   float(row["SPY"]),
                "vix":   float(row["VIX"]),
                "n":     len(fired_today),
                "fired": fired_today,
            })
            for k in fired_today:
                hc_credit[k] += 1

    return {
        "days":  len(window),
        "start": str(window.index[0].date()),
        "end":   str(window.index[-1].date()),
        "hc_days":     hc_days,
        "fire_counts": fire_counts,
        "hc_credit":   hc_credit,
    }


def format_email_report(r: dict) -> tuple[str, str]:
    """Returns (subject, body) for email — concise, action-first.

    Three tiers (per the two-tier thesis + standard buy):
      🔥🔥 SUPER HIGH CONVICTION  →  HC + the SUPER_HC_ANCHOR strategy fires
      🔥    HIGH CONVICTION         →  ≥HIGH_CONVICTION_FRESH strategies fire
      🟢    BUY                     →  ≥1 fresh strategy fires
      ⚪️    no signal               →  nothing fresh
    """
    if r["any_actionable"]:
        n = r["n_fresh"] if r["mode"] != "RAW" else r["n_fires"]
        fired_keys = ", ".join(r["fresh_fires"] if r["mode"] != "RAW"
                               else [f["key"] for f in r["fires"] if f["fired"]])
        if r.get("is_super_high_conviction"):
            subject = (f"🔥🔥 SUPER HIGH CONVICTION — {n} SPY LEAPS signals "
                       f"incl. {SUPER_HC_ANCHOR} ({fired_keys})")
        elif r["is_high_conviction"]:
            subject = f"🔥 HIGH CONVICTION — {n} SPY LEAPS signals agree ({fired_keys})"
        else:
            subject = f"🟢 SPY LEAPS — {n} signal{'s' if n != 1 else ''} firing ({fired_keys})"
    else:
        subject = f"⚪️ SPY LEAPS — no fresh signals today ({r['date']})"
    body = format_email_body(r)
    return subject, body


def format_email_body(r: dict) -> str:
    """Concise email body — only what's needed to act today.

    Layout:
      1. Verdict line + market snapshot (compact)
      2. Action block (contract + 5-step execution) if any_actionable
      3. Fired strategies with reasons (just the failed/passed conditions)
      4. Strategy ranking (10-line table)
      5. Footer with usage hints

    The 5-year HIGH-CONVICTION scan is still computed for the console
    report (`--force` to see it) but intentionally omitted from the email
    to keep the message tight.
    """
    out: list[str] = []
    out.append("═" * 72)
    out.append(f"  SPY LEAPS — DAILY SIGNAL  •  {r['date']}")
    out.append("═" * 72)
    out.append("")

    # ── 1. Verdict + market snapshot ─────────────────────────────────────────
    if r.get("is_super_high_conviction"):
        verdict = (f"🔥🔥 SUPER HIGH CONVICTION BUY — {r['n_fresh']} strategies "
                   f"agree (incl. anchor {SUPER_HC_ANCHOR})")
    elif r["is_high_conviction"]:
        verdict = f"🔥 HIGH CONVICTION BUY — {r['n_fresh']} strategies agree"
    elif r["any_actionable"]:
        verdict = f"🟢 BUY — {r['n_fresh']} fresh signal{'s' if r['n_fresh'] != 1 else ''} firing"
    else:
        verdict = "⚪️ NO ACTION — no fresh buy signals today"
    out.append(f"  {verdict}")
    out.append(f"  SPY ${r['spy']:.2f}  •  VIX {r['vix']:.1f}  •  "
               f"RSI {r['rsi14']:.0f}  •  50DMA ${r['sma50']:.0f}  •  "
               f"200DMA ${r['sma200']:.0f}  •  DD {r['drawdown']:+.1f}%")
    out.append("")

    # ── 2. Action block (only when actionable) ───────────────────────────────
    if r["any_actionable"]:
        c = r["contract"]
        spy_now = r["spy"]
        out.append("  " + "─" * 68)
        out.append(f"  🟢 SUGGESTED CONTRACT (+{c['otm_pct']*100:.0f}% OTM 2-yr LEAPS)")
        out.append(f"     Strike      : ${c['strike']:.0f}     "
                   f"Expiry: {c['expiry']}")
        out.append(f"     Mid premium : ${c['premium_mid']:.2f}/share     "
                   f"Cost: ${c['cost']:.0f}/contract  (limit ≤ ${c['premium_ask']:.2f})")
        out.append("")
        out.append(f"  📌 TOMORROW MORNING EXECUTION:")
        out.append(f"     1. At 9:30am ET, look up SPY current price")
        out.append(f"     2. Strike = SPY × 1.15, round to $5 "
                   f"(tonight ${spy_now:.0f} → "
                   f"${round(spy_now*1.15/5)*5:.0f})")
        out.append(f"     3. Wait until ~9:45am ET for spreads to tighten")
        out.append(f"     4. Sell ~{int(c['cost']/spy_now*1.18) + 1} shares VOO "
                   f"(specific-lot, prefer loss/long-term lots)")
        out.append(f"     5. BUY 1 SPY ${c['strike']:.0f} {c['expiry']} call, "
                   f"LIMIT ≤ mid + $0.10")
        out.append(f"     ⚠️  Skip if SPY gaps down >1.5%, FOMC day, or major data release")
        out.append("  " + "─" * 68)
        out.append("")

    # ── 3. Fired strategies with reasons ─────────────────────────────────────
    fired = sorted([f for f in r["fires"] if f["fired"]],
                   key=lambda f: -f["edge_10yr"])
    if fired:
        out.append(f"  ── WHY ({len(fired)} firing) ──")
        for f in fired:
            tag = "🟢" if f["is_fresh"] else "⏸️ "
            tail = " (FRESH)" if f["is_fresh"] else f" (within {DEBOUNCE_DAYS}d cooldown)"
            out.append(f"  {tag} {f['key']:<17} "
                       f"win {f['win_rate']}%, 10yr edge ${f['edge_10yr']:+,}{tail}")
            out.append(f"     {f['layman']}")
            for c in f["conds"]:
                out.append(f"       {c}")
        out.append("")

    # ── 4. Strategy ranking (always) ─────────────────────────────────────────
    out.append("  ── STRATEGY RANKING (10-yr after-tax edge, biggest $$ first) ──")
    ranked = sorted(STRATEGIES, key=lambda s: -s.edge_10yr)
    fired_keys = {f["key"] for f in r["fires"] if f["fired"]}
    for i, s in enumerate(ranked, 1):
        flag = "🟢" if s.key in fired_keys else "  "
        out.append(f"     {flag} {i:>2}. {s.key:<17}  win {s.win_rate}%  "
                   f"10yr ${s.edge_10yr:>+9,}  ({s.freq_yr:.1f}/yr)")
    out.append("")

    # ── 5. SELL-side block (only if there is ANY sell indication today) ──────
    # Skip entirely on quiet HOLD days so the email stays BUY-focused.
    sell = r.get("sell")
    if sell and _has_sell_indication(sell):
        out.append(format_sell_email_section(sell))
        out.append("")

    # ── 6. Footer ────────────────────────────────────────────────────────────
    out.append("─" * 72)
    out.append("  Run `python final_leaps/daily_signal_top10.py --force` for full")
    out.append("  diagnostics (all 10 strategies' condition breakdowns + 5-yr scan).")
    out.append("─" * 72)
    return "\n".join(out)


# ─── SELL-side email section (compact) ───────────────────────────────────────

def format_sell_email_section(sell: dict) -> str:
    """One concise block summarizing today's SELL-side verdict.

    Mirrors the BUY block's tight style — just the verdict + firing rules
    + reasoning.  Full per-rule condition breakdown stays on the console.
    """
    icon = {"SELL": "🔴", "WATCH": "🟡", "HOLD": "🟢"}.get(sell["verdict"], "•")
    lines = []
    lines.append(f"  ── SELL-SIDE — {icon} {sell['verdict']} "
                 f"({sell['n_fires']}/10 rules firing, "
                 f"{sell['n_recommended_fired']}/2 priority) ──")
    lines.append(f"     {sell['verdict_reason']}")
    if sell["recommended_firing"]:
        lines.append(f"     🔥 Priority firing: "
                     f"{', '.join(sell['recommended_firing'])}")
    firing = [f for f in sell["fires"] if f["fired"]]
    if firing:
        names = ", ".join(f["key"] for f in firing)
        lines.append(f"     All firing rules: {names}")
    elif sell["verdict"] == "HOLD":
        lines.append(f"     No sell rules firing — keep open LEAPS positions.")
    return "\n".join(lines)


def _has_sell_indication(sell: dict | None) -> bool:
    """True if the sell-side has anything worth surfacing in the email.

    HOLD with 0 rules firing -> not worth showing.  Anything else
    (WATCH, SELL, or even a single non-priority rule firing) -> show it.
    """
    if not sell:
        return False
    if sell.get("verdict") in ("SELL", "WATCH"):
        return True
    return sell.get("n_fires", 0) > 0


def build_sell_report(df: pd.DataFrame, positions: list[dict] | None = None) -> dict | None:
    """Build a sell-side report sharing the same SPY+VIX dataframe.

    Returns None if the sell-signals package isn't importable (lets the
    buy-side keep working stand-alone).
    """
    try:
        from sell_signals.daily_sell_check import build_report as _bs
    except Exception as e:
        print(f"  ⚠️  Skipping sell-side (could not import sell_signals): {e}")
        return None
    return _bs(df, positions=positions or [])


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
    parser.add_argument("--scan",  type=int, default=DEFAULT_SCAN_DAYS,
                        help=f"Scan past N trading days for HIGH-CONVICTION days "
                             f"(default {DEFAULT_SCAN_DAYS}; pass 0 to disable)")
    parser.add_argument("--no-sell", action="store_true",
                        help="Skip the SELL-side scanner (default: both BUY and SELL "
                             "are run + combined into a single email)")
    args = parser.parse_args()

    df = fetch_data(scan_days=args.scan)

    # ── BUY-side ─────────────────────────────────────────────────────────────
    report = build_report(df, otm_pct=args.otm, mode=args.mode,
                          scan_days=args.scan)
    append_log(report)

    # ── SELL-side (unless --no-sell) ─────────────────────────────────────────
    sell_report = None if args.no_sell else build_sell_report(df)
    if sell_report is not None:
        report["sell"] = sell_report
        # Also print sell-side console output for parity with the old workflow
        try:
            from sell_signals.daily_sell_check import format_text as _ft
            sell_console = _ft(sell_report)
        except Exception:
            sell_console = ""
    else:
        sell_console = ""

    # ── Console output (BUY first, then SELL if present) ─────────────────────
    print(format_text_report(report))
    if sell_console:
        print(sell_console)

    # ── Decide whether to email ──────────────────────────────────────────────
    buy_actionable  = report["any_actionable"]
    sell_actionable = (sell_report is not None
                       and sell_report["verdict"] in ("SELL", "WATCH"))
    should_send = (buy_actionable or sell_actionable or args.force) and not args.quiet
    if should_send and (args.force or should_notify_again(report)):
        subject = _combined_subject(report, sell_report)
        body    = format_email_body(report)
        short_msg = _combined_short_msg(report, sell_report)
        priority = 1 if (buy_actionable or sell_actionable) else 0
        notify_all(
            title=subject,
            message=short_msg,                          # for macOS/Pushover (short)
            body=body,                                  # full detailed email body
            subtitle=f"{report['date']}  •  {report['n_fresh']}/10 fresh",
            priority=priority,
        )
        if buy_actionable:
            remember_notification(report)
            if report["mode"] == "DEBOUNCED":
                state = load_debounce_state()
                for k in report["fresh_fires"]:
                    state[k] = report["date"]
                save_debounce_state(state)
    elif (buy_actionable or sell_actionable) and not args.force:
        print("  ℹ️  Already notified for this date.")
    elif args.quiet:
        print("  🔇 Quiet mode — no notification.")
    else:
        print("  ℹ️  No BUY or SELL signals firing — no notification sent.")

    print(f"  📝 Logged to {LOG_PATH}\n")


def _combined_subject(r: dict, sell: dict | None) -> str:
    """Build one subject line that summarizes both BUY and SELL state."""
    buy_part = ""
    if r["any_actionable"]:
        keys = ", ".join(r["fresh_fires"] if r["mode"] != "RAW"
                         else [f["key"] for f in r["fires"] if f["fired"]])
        n = r["n_fresh"] if r["mode"] != "RAW" else r["n_fires"]
        if r.get("is_super_high_conviction"):
            flame = "🔥🔥 SHC "
        elif r["is_high_conviction"]:
            flame = "🔥 HC "
        else:
            flame = "🟢 "
        buy_part = f"{flame}BUY × {n} ({keys})"
    else:
        buy_part = "⚪️ BUY none"

    if not _has_sell_indication(sell):
        # No sell indication today — keep the subject BUY-only.
        return f"SPY LEAPS — {buy_part}  •  {r['date']}"

    sell_icon = {"SELL": "🔴", "WATCH": "🟡", "HOLD": "🟢"}.get(sell["verdict"], "•")
    sell_part = f"{sell_icon} SELL {sell['verdict']}"
    if sell["recommended_firing"]:
        sell_part += f" ({', '.join(sell['recommended_firing'])})"
    return f"SPY LEAPS — {buy_part}  /  {sell_part}  •  {r['date']}"


def _combined_short_msg(r: dict, sell: dict | None) -> str:
    """Short-form summary used for macOS toasts / Pushover (length-limited)."""
    if r["any_actionable"]:
        if r.get("is_super_high_conviction"):
            tier = "🔥🔥 SHC"
        elif r["is_high_conviction"]:
            tier = "🔥 HC"
        else:
            tier = "🟢"
        buy = (f"{tier} {r['n_fresh']} fresh BUY  •  "
               f"SPY ${r['spy']:.0f}  VIX {r['vix']:.1f}\n"
               f"Buy: SPY ${r['contract']['strike']:.0f} "
               f"{r['contract']['expiry']} call @ "
               f"${r['contract']['premium_mid']:.2f}/sh "
               f"(~${r['contract']['cost']:.0f}/cntrct)")
    else:
        buy = (f"SPY ${r['spy']:.0f}  VIX {r['vix']:.1f}  — no fresh BUY signals.")
    if not _has_sell_indication(sell):
        return buy
    sell_line = (f"SELL: {sell['verdict']}  "
                 f"({sell['n_fires']}/10 rules, "
                 f"{sell['n_recommended_fired']}/2 priority)")
    if sell["recommended_firing"]:
        sell_line += f"  🔥 {', '.join(sell['recommended_firing'])}"
    return f"{buy}\n{sell_line}"


if __name__ == "__main__":
    main()
