"""
Daily Signal Monitor — baseline + sweep-winner strategies
=========================================================

Runs 14 BUY heuristics and emails when your `--mode` gate passes.  The body
includes a **win-% matrix** (1y–30y) and **tiers** when several heuristics
agree (long-grid anchor vs cheap-IV anchor — see `ANCHOR_LONG_GRID`).

**Frequent email:** `--mode FREQUENT` (alias of `RAW`, ~151/yr — any strategy
firing).  Add `--repeat-daily` for more than one email per calendar day.

**Tiers:** 🟢 1 strategy firing → 🟡 2 → 🔥 ≥3 (HC) → 🔥🔥 long-grid super /
cheap-IV super → 🔥🔥🔥 **Elite** when HC includes **both** anchors.

In addition, every email appends a "33-YR SWEEP TOP-5 PER WINDOW" block
showing the top-5 rules for the 1/2/5/10/30-year windows (from
`results/heuristics_sweep_filtered.csv`) and which of them fire today.
Win-% cells fall back to committed `sweep_win_rate_bundle.csv` when
`results/heuristics_sweep*.csv` are absent (CI / fresh clone).

Strategies are ordered by **10-year after-tax edge** from the sweep CSVs
(`results/heuristics_sweep*.csv`) when a matching `sweep_rule` row exists,
otherwise the static figures baked into `STRATEGIES`.

    Rank  Strategy           Win%   10yr edge $   Fires/yr  Idea
    ────  ─────────────────  ────   ───────────   ────────  ──────────────────
     1.   A_CHEAP_IV         82%    +$390,073      8.0      VIX<16 + RSI sweet spot
     …    (+ sweep-tagged rows N/L/K/M, etc.)

Historical scan default: 1,260 trading days ≈ 5 years.

This script also runs the SELL-side scanner from `sell_signals/` in the
same process so BOTH BUY and SELL verdicts are combined into ONE email.
Pass `--no-sell` to keep the email BUY-only.

Usage:
  python daily_signal_top10.py --mode FREQUENT     # frequent: email on any firing
  python daily_signal_top10.py --mode FREQUENT --repeat-daily   # intraday repeats
  python daily_signal_top10.py                # default DEBOUNCED (~150+ firing days/yr)
  python daily_signal_top10.py --no-sell      # BUY only
  python daily_signal_top10.py --force        # email even with no fires
  python daily_signal_top10.py --reset-notify-state   # clear date de-dupe, then run
  python daily_signal_top10.py --repeat-daily # allow a 2nd email same calendar day
  python daily_signal_top10.py --quiet        # print only, no email
  python daily_signal_top10.py --otm 0.15     # OTM target (default 15%)
  python daily_signal_top10.py --scan 252     # 1-year HC scan window
  python daily_signal_top10.py --scan 0      # disable historical scan
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
# Sweep-aligned rules (same `make_rule` gates as heuristics_sweep.build_rules).
from sweep_rule_builder import make_rule

try:
    from heuristics_sweep import build_rules as _build_sweep_rules
except ImportError:
    def _build_sweep_rules():
        """Fallback when `heuristics_sweep.py` is not in the checkout (e.g. CI)."""
        return []

rule_K_fear_unwind   = make_rule(move_crush_min=0.20, require_move_falling=True,
                                  require_above_200dma=True, vix_max=30)
rule_L_oversold_deep = make_rule(rsi_max=30, require_above_200dma=True,
                                  vix_max=28)
rule_M_bb_tight      = make_rule(bb_width_max=0.15, require_bb_upper=True,
                                  require_above_200dma=True, vix_max=18)
rule_N_a_grid_vix14_rsi65 = make_rule(
    vix_max=14, rsi_min=40, rsi_max=65,
    require_above_50dma=True, require_above_200dma=True,
)

PROJECT_DIR = Path(__file__).resolve().parent
# Repo root needs to be on sys.path so we can import the sell-side scanner
# (`sell_signals/daily_sell_check.py`) — it lives in a sibling folder.
if str(PROJECT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR.parent))
LOG_PATH = PROJECT_DIR / "results" / "daily_top10_log.csv"
LAST_NOTIFIED_PATH = PROJECT_DIR / ".last_notified_top10.json"
DEFAULT_OTM = 0.15
HIGH_CONVICTION_FRESH = 3    # ≥3 strategies firing same day = "🔥 HIGH CONVICTION"
# Tier anchors (see 33-yr sweep win % matrix in email):
#   N_A_GRID… = VIX<14 + RSI 40–65 — strongest *long* horizon stats in the sweep.
#   A_CHEAP_IV = classic cheap-VIV + RSI band — historic SHC anchor.
ANCHOR_LONG_GRID = "N_A_GRID_VIX14_RSI65"
SUPER_HC_ANCHOR = "A_CHEAP_IV"   # kept for backward-compatible constant name
DEFAULT_SCAN_DAYS = 1260     # past N trading days scanned for HC days (~5 years)
# Console history: list only the newest HC days (full counts unchanged below).
HC_HISTORY_DISPLAY_MAX = 25
# Email: show sweep top-5 only for these windows (shorter than 1/2/5/10/30).
EMAIL_SWEEP_WINDOWS = [10, 30]


@dataclass(frozen=True)
class StrategyDef:
    """One backtested entry strategy, with its historical performance figures."""
    key: str            # internal short name (D_BREAKOUT, C_CHEAP_IV, ...)
    rule: Callable      # row -> bool decision function (from strategy_alternatives)
    freq_yr: float      # average fires per year (10-yr backtest)
    win_rate: int       # historical win % at +15% OTM 2-yr LEAPS
    edge_10yr: int      # after-tax $$ edge over pure VOO DCA, 10-yr
    layman: str         # plain-English description for the email body
    sweep_rule: str | None = None   # CSV `rule` id for 33-yr sweep win% (every email strategy)


# Ordered by 10-year after-tax edge (biggest $$ first).
# Baseline figures from FINAL_STRATEGY.md; sweep rows use heuristics_sweep*.csv.
STRATEGIES: list[StrategyDef] = [
    StrategyDef("A_CHEAP_IV",       rule_C_cheap_iv,           8.0, 82, 390_073,
                "Options are cheap (VIX<16) and trend intact",
                sweep_rule="orig.C_CHEAP_IV"),
    StrategyDef("B_TREND_FOLLOW",   rule_H_trend_follow,      11.6, 74, 378_454,
                "Trending uptrend with MACD bullish",
                sweep_rule="orig.H_TREND_FOLLOW"),
    StrategyDef("C_BREAKOUT",       rule_D_breakout,           8.1, 81, 372_959,
                "SPY hit new 60-day high with VIX still low",
                sweep_rule="orig.D_BREAKOUT"),
    StrategyDef("D_QUAL_BREAKOUT",  rule_M_quality_breakout,   5.8, 79, 341_693,
                "Quality breakout: 60d high + very low VIX + clean uptrend",
                sweep_rule="orig.M_QUAL_BREAKOUT"),
    StrategyDef("E_A_OR_SQUEEZE",   rule_L_squeeze_or_current, 4.3, 77, 181_197,
                "Momentum entry (H_CURRENT) OR Bollinger squeeze breakout",
                sweep_rule="orig.L_A_OR_SQUEEZE"),
    StrategyDef("F_VIX_CRUSH",      rule_F_vix_crush,          2.8, 68, 132_260,
                "Fear collapsed: VIX dropped 30%+ in 10 days",
                sweep_rule="orig.F_VIX_CRUSH"),
    StrategyDef("G_BB_SQUEEZE",     rule_I_bb_squeeze,         2.3, 78, 114_301,
                "Bollinger Band squeeze + breakout",
                sweep_rule="orig.I_BB_SQUEEZE"),
    StrategyDef("L_OVERSOLD_DEEP",  rule_L_oversold_deep,      2.2, 68,  85_155,
                "Oversold RSI<30 + VIX<28 + SPY>200DMA (sweep oversold.RSI<30.VIX<28).",
                sweep_rule="oversold.RSI<30.VIX<28"),
    StrategyDef("M_BB_TIGHT",       rule_M_bb_tight,           2.0, 85,  69_504,
                "Volatility-squeeze breakout: BB-width <15%ile + SPY at upper "
                "band + VIX<18",
                sweep_rule="squeeze.BB<0.15.VIX<18"),
    StrategyDef("H_CURRENT",        rule_A_current,            2.3, 70,  64_727,
                "2-of-3 momentum signals fired + filters pass",
                sweep_rule="orig.A_CURRENT"),
    StrategyDef("I_FILTER_CURR",    rule_N_filter_current,     1.6, 75,  63_826,
                "Strict momentum entry (H_CURRENT + extra VIX/MACD filters)",
                sweep_rule="orig.N_FILTER_CURR"),
    StrategyDef("N_A_GRID_VIX14_RSI65", rule_N_a_grid_vix14_rsi65, 6.0, 80,  58_591,
                "Cheap-IV grid: VIX<14, RSI 40–65, SPY above 50 & 200DMA "
                "(sweep A_grid.VIX<14.RSI<65).",
                sweep_rule="A_grid.VIX<14.RSI<65"),
    StrategyDef("J_OVERSOLD",       rule_E_oversold_uptrend,   2.8, 71,  58_465,
                "Oversold dip in established uptrend (RSI<35)",
                sweep_rule="orig.E_OVERSOLD"),
    StrategyDef("K_FEAR_UNWIND",    rule_K_fear_unwind,        1.7, 82,  54_366,
                "MOVE fear unwind: ≥20% MOVE crush in 10d + falling + uptrend "
                "(sweep move.fear_unwind).",
                sweep_rule="move.fear_unwind"),
]


# ─── Sweep CSV → win% ladder (for email) + 10y metrics (for ranking) ─────────
SWEEP_CSV_RAW = PROJECT_DIR / "results" / "heuristics_sweep.csv"
# Shipped snapshot so CI / fresh clones still show win-% (gitignored `results/*.csv`).
SWEEP_WIN_RATE_BUNDLE = PROJECT_DIR / "sweep_win_rate_bundle.csv"
_SWEEP_WIN_LUT: dict[str, dict[int, int]] | None = None
# rule → {edge, win_rate?, freq_yr?} from window_yr == 10 (merged raw then filtered)
_SWEEP_10Y_METRICS: dict[str, dict[str, float | int]] | None = None
# True when only the bundle supplied win% (no local results/*.csv sweep files).
_SWEEP_WIN_BUNDLE_FALLBACK: bool = False
RANK_WINDOW_YR = 10


def sweep_win_by_window() -> dict[str, dict[int, int]]:
    """rule_name → {window_yr → win_rate %} from sweep CSVs.

    Load order (each overwrites the same ``rule`` / ``window_yr`` cell):

    1. ``sweep_win_rate_bundle.csv`` next to this script (committed snapshot for CI).
    2. ``results/heuristics_sweep.csv`` (full sweep).
    3. ``results/heuristics_sweep_filtered.csv`` (density-filtered leaderboard).

    Also fills `_SWEEP_10Y_METRICS` for ``window_yr == RANK_WINDOW_YR`` when those
    rows include ``edge_aftertax`` (bundle rows do not — ranking falls back to
    static ``StrategyDef`` fields until a real sweep CSV exists).
    """
    global _SWEEP_WIN_LUT, _SWEEP_10Y_METRICS, _SWEEP_WIN_BUNDLE_FALLBACK
    if _SWEEP_WIN_LUT is not None:
        return _SWEEP_WIN_LUT
    lut: dict[str, dict[int, int]] = {}
    m10: dict[str, dict[str, float | int]] = {}
    flt = PROJECT_DIR / "results" / "heuristics_sweep_filtered.csv"
    paths = [p for p in (SWEEP_WIN_RATE_BUNDLE, SWEEP_CSV_RAW, flt) if p.exists()]
    _SWEEP_WIN_BUNDLE_FALLBACK = (
        SWEEP_WIN_RATE_BUNDLE.exists()
        and not SWEEP_CSV_RAW.exists()
        and not flt.exists()
    )
    for path in paths:
        df = pd.read_csv(path)
        has_tpy = "trades_per_yr" in df.columns
        for _, r in df.iterrows():
            k = str(r["rule"])
            w = int(float(r["window_yr"]))
            wr = r.get("win_rate")
            if wr is None or (isinstance(wr, float) and pd.isna(wr)):
                pass
            else:
                lut.setdefault(k, {})[w] = int(round(float(wr)))
            if w == RANK_WINDOW_YR:
                rec = m10.setdefault(k, {})
                ea = r.get("edge_aftertax")
                if ea is not None and not (isinstance(ea, float) and pd.isna(ea)):
                    rec["edge"] = int(round(float(ea)))
                if wr is not None and not (isinstance(wr, float) and pd.isna(wr)):
                    rec["win_rate"] = int(round(float(wr)))
                if has_tpy:
                    tpy = r.get("trades_per_yr")
                    if tpy is not None and not (isinstance(tpy, float) and pd.isna(tpy)):
                        rec["freq_yr"] = float(tpy)
    _SWEEP_WIN_LUT = lut
    _SWEEP_10Y_METRICS = m10
    return _SWEEP_WIN_LUT


def strategy_display_metrics(s: StrategyDef) -> tuple[int, int, float]:
    """Return (10y edge $, win %, fires/yr) for ranking and copy.

    Prefer the ``RANK_WINDOW_YR`` row for ``s.sweep_rule`` in the merged sweep
    tables; fall back to static ``StrategyDef`` fields when the CSV has no row.
    """
    sweep_win_by_window()
    rule = s.sweep_rule or ""
    rec = (_SWEEP_10Y_METRICS or {}).get(rule, {})
    if rec.get("edge") is not None:
        edge = int(rec["edge"])
        wr = int(rec["win_rate"]) if rec.get("win_rate") is not None else s.win_rate
        fq = float(rec["freq_yr"]) if rec.get("freq_yr") is not None else s.freq_yr
        return edge, wr, fq
    return s.edge_10yr, s.win_rate, s.freq_yr


def strategies_sorted_by_sweep_10y() -> list[StrategyDef]:
    """All ``STRATEGIES`` ordered by current 10-yr sweep edge (largest first)."""
    return sorted(STRATEGIES, key=lambda s: -strategy_display_metrics(s)[0])


def _win_pct_cell(d: dict[int, int], w: int, width: int = 4) -> str:
    """Right-align a win% or em-dash for missing window."""
    if w not in d:
        return "—".rjust(width)
    return f"{d[w]}%".rjust(width)


def format_win_rate_matrix_lines(r: dict) -> list[str]:
    """Compact one-screen table: 1y–30y win % + today's fire markers + HC hint."""
    lut = sweep_win_by_window()
    by_key = {f["key"]: f for f in r["fires"]}
    ordered = strategies_sorted_by_sweep_10y()
    W = 22
    lines: list[str] = []
    lines.append("  ── WIN % AT A GLANCE (33-yr sweep vs VOO DCA) ──")
    lines.append("     Each cell = % of back-tested trades beating VOO in that look-back.")
    lines.append(f"     {'Strategy':<{W}}   T    1y   2y   5y  10y  30y")
    lines.append("     " + "─" * 62)
    for s in ordered:
        d = lut.get(s.sweep_rule or "", {})
        fk = by_key.get(s.key)
        if fk and fk.get("fired"):
            raw = "*"
        else:
            raw = "-"
        tc = f"{raw:^3}"
        c = [_win_pct_cell(d, w) for w in (1, 2, 5, 10, 30)]
        lines.append(f"     {s.key:<{W}}   {tc}  " + " ".join(c))
    lines.append("")
    if not lut:
        lines.append("     ⚠️  No sweep win-% data: add `sweep_win_rate_bundle.csv` (repo) or")
        lines.append("        `results/heuristics_sweep*.csv` from `python final_leaps/heuristics_sweep.py`.")
    elif _SWEEP_WIN_BUNDLE_FALLBACK:
        lines.append("     ℹ️  Win % uses committed `sweep_win_rate_bundle.csv` (snapshot).")
        lines.append("        Local `results/heuristics_sweep*.csv` overrides when present.")
    lines.append("     T   * = strategy conditions TRUE today (counts toward tiers / email)")
    lines.append("         - = not firing today")
    lines.append("     🔥 High conviction (HC) = ≥3 different strategies with * same day.")
    lines.append(f"     🔥🔥 Long-grid super = HC + {ANCHOR_LONG_GRID} (best 30y sweep row).")
    lines.append(f"     🔥🔥 Super (cheap IV) = HC + {SUPER_HC_ANCHOR} only (classic SHC).")
    lines.append(f"     🔥🔥🔥 Elite = HC + both {ANCHOR_LONG_GRID} + {SUPER_HC_ANCHOR}.")
    if r.get("is_elite_conviction"):
        lines.append(f"     ➜ TODAY: 🔥🔥🔥 ELITE HC ({r['n_fresh']} *)")
    elif r.get("is_long_grid_super"):
        lines.append(f"     ➜ TODAY: 🔥🔥 LONG-GRID SUPER HC — {r['n_fresh']} * incl. "
                     f"{ANCHOR_LONG_GRID}")
    elif r.get("is_super_high_conviction"):
        lines.append(f"     ➜ TODAY: 🔥🔥 SUPER HC (cheap IV) — {r['n_fresh']} * incl. "
                     f"{SUPER_HC_ANCHOR}")
    elif r.get("is_high_conviction"):
        lines.append(f"     ➜ TODAY: 🔥 HIGH CONVICTION — {r['n_fresh']} firing: "
                     f"{', '.join(r['fresh_fires'])}")
    elif r.get("is_strong_conviction"):
        lines.append(f"     ➜ TODAY: 🟡 STRONG (2 heuristics): {', '.join(r['fresh_fires'])}")
    elif r.get("n_fresh", 0) > 0:
        lines.append(f"     ➜ TODAY: {r['n_fresh']} heuristic(s) firing: {', '.join(r['fresh_fires'])}")
    return lines


# ─── Data ────────────────────────────────────────────────────────────────────

def fetch_data(scan_days: int = 0) -> pd.DataFrame:
    """Fetch SPY + VIX + MOVE from Yahoo Finance.

    Need ≥252 days for BB-percentile + an extra buffer for whatever
    --scan window the user requested.  MOVE (^MOVE = ICE BofA Treasury-bond
    vol) is optional — if Yahoo doesn't return it, we degrade gracefully
    (MOVE-based rules just won't fire).
    """
    period_days = max(500, 380 + int(scan_days * 1.5))
    print(f"  📡 Fetching SPY + VIX + MOVE from Yahoo Finance ({period_days}d)...")
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=period_days)
    spy  = yf.download("SPY",   start=start, end=end, progress=False, auto_adjust=False)
    vix  = yf.download("^VIX",  start=start, end=end, progress=False, auto_adjust=False)
    move = yf.download("^MOVE", start=start, end=end, progress=False, auto_adjust=False)
    if spy.empty or vix.empty:
        sys.exit("❌ Yahoo returned empty data for SPY/VIX")
    for d in (spy, vix, move):
        if not d.empty and isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
    cols = {
        "SPY": spy["Close"].astype(float),
        "VIX": vix["Close"].astype(float),
    }
    if not move.empty and "Close" in move.columns:
        cols["MOVE"] = move["Close"].astype(float)
    df = pd.DataFrame(cols).dropna(subset=["SPY", "VIX"])
    df.index = pd.to_datetime(df.index)
    move_str = (f"MOVE {df['MOVE'].iloc[-1]:.1f}"
                if "MOVE" in df.columns else "MOVE n/a")
    print(f"     Got {len(df)} trading days  •  last close: {df.index[-1].date()}  •  {move_str}")
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

    if key == "L_OVERSOLD_DEEP":
        c1 = row["RSI14"] < 30
        c2 = bool(row["spy_above_200"])
        c3 = vix < 28
        conds = [
            cv("RSI < 30 (deep oversold)", c1, f"RSI {row['RSI14']:.1f}"),
            cv("SPY > 200DMA",             c2, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 28",                 c3, f"VIX {vix:.1f}"),
        ]
        return all([c1, c2, c3]), conds

    if key == "M_BB_TIGHT":
        c1 = row["bb_width_pct"] < 0.15
        c2 = spy >= row["bb_upper"]
        c3 = bool(row["spy_above_200"])
        c4 = vix < 18
        conds = [
            cv("BB width <15th %ile",  c1, f"width %ile {row['bb_width_pct']*100:.0f}%"),
            cv("SPY ≥ upper band",     c2, f"SPY ${spy:.0f} vs upper ${row['bb_upper']:.0f}"),
            cv("SPY > 200DMA",         c3, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 18",             c4, f"VIX {vix:.1f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "N_A_GRID_VIX14_RSI65":
        c1 = vix < 14
        c2 = 40 <= row["RSI14"] <= 65
        c3 = bool(row["spy_above_50"])
        c4 = bool(row["spy_above_200"])
        conds = [
            cv("VIX < 14",             c1, f"VIX {vix:.1f}"),
            cv("RSI between 40 and 65", c2, f"RSI {row['RSI14']:.1f}"),
            cv("SPY > 50DMA",          c3, f"50DMA ${row['sma50']:.0f}"),
            cv("SPY > 200DMA",         c4, f"200DMA ${row['sma200']:.0f}"),
        ]
        return all([c1, c2, c3, c4]), conds

    if key == "K_FEAR_UNWIND":
        move    = row.get("MOVE", float("nan"))
        crush   = row.get("move_crush", float("nan"))
        falling = bool(row.get("move_falling", False))
        if pd.isna(move):
            return False, [cv("MOVE data available", False, "Yahoo did not return ^MOVE today")]
        c1 = crush >= 0.20 if pd.notna(crush) else False
        c2 = falling
        c3 = bool(row["spy_above_200"])
        c4 = vix < 30
        conds = [
            cv("MOVE crushed ≥20% in 10d", c1,
               f"crush {crush*100:.0f}%  (MOVE {move:.1f} vs 10d max {row.get('move_max10', float('nan')):.1f})"),
            cv("MOVE falling 5d (slope<-5)", c2,
               f"slope5 {row.get('move_slope5', float('nan')):+.1f}"),
            cv("SPY > 200DMA",             c3, f"200DMA ${row['sma200']:.0f}"),
            cv("VIX < 30",                 c4, f"VIX {vix:.1f}"),
        ]
        return all([c1, c2, c3, c4]), conds

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


def build_report(df: pd.DataFrame, otm_pct: float, mode: str = "DEBOUNCED",
                 scan_days: int = 0) -> dict:
    """Build the daily report dict.

    If `scan_days > 0`, also replay the past `scan_days` trading days and
    attach a `history` entry so HIGH-CONVICTION days are visible alongside
    today's fires.

    Modes
    -----
    DEBOUNCED / RAW / FREQUENT
                No per-strategy debounce: **every** strategy that passes its
                gates today counts toward `fresh_fires` and tiers.  DEBOUNCED
                emails when ≥1 strategy fires; RAW and FREQUENT behave the same
                (FREQUENT is an alias for clearer cron naming).
    HIGH_CONVICTION
                Email only when ≥3 strategies fire the same day.
    """
    eff_mode = "RAW" if mode == "FREQUENT" else mode
    feats = extend_features(df)
    sigs  = signals_in_window(feats, 1)
    today = feats.index[-1]
    row   = feats.loc[today]
    sigs_row = sigs.loc[today] if today in sigs.index else pd.Series({"score": 0})

    spy = float(row["SPY"])
    vix = float(row["VIX"])
    today_ts = today

    fires = []
    fresh_fires: list[str] = []   # strategies whose conditions are TRUE today
    for s in STRATEGIES:
        fired, conds = explain_rule(s.key, row, sigs_row)
        is_fresh = bool(fired)
        edge10, win_r, fq = strategy_display_metrics(s)
        fires.append({
            "key": s.key, "fired": fired, "freq": fq,
            "win_rate": win_r, "edge_10yr": edge10,
            "layman": s.layman, "conds": conds,
            "is_fresh": is_fresh,
            "sweep_rule": s.sweep_rule,
        })
        if fired:
            fresh_fires.append(s.key)

    contract = suggest_contract(spy, vix, otm_pct)

    n_fires = sum(1 for f in fires if f["fired"])
    n_fresh = len(fresh_fires)

    if eff_mode == "RAW":
        any_actionable = n_fires > 0
    elif eff_mode == "HIGH_CONVICTION":
        any_actionable = n_fresh >= HIGH_CONVICTION_FRESH
    else:  # DEBOUNCED — same gate as RAW (no debounce); name kept for CLI compat
        any_actionable = n_fresh > 0

    is_high_conviction = n_fresh >= HIGH_CONVICTION_FRESH
    cf_set = set(fresh_fires)

    # Tier ladder (highest wins in subject line / verdict):
    #   ELITE          = HC + long-grid + cheap-IV anchors both fire
    #   LONG-GRID SUPER = HC + VIX14/RSI grid only (best long-horizon heuristic)
    #   SUPER (cheap)  = HC + A_CHEAP_IV only
    #   HIGH CONVICTION = HC, no anchor exclusivity
    #   STRONG         = 2 firing (not HC)
    is_elite_conviction = is_high_conviction and (
        ANCHOR_LONG_GRID in cf_set and SUPER_HC_ANCHOR in cf_set)
    is_long_grid_super = (is_high_conviction and (ANCHOR_LONG_GRID in cf_set)
                          and not is_elite_conviction)
    is_super_high_conviction = (is_high_conviction and (SUPER_HC_ANCHOR in cf_set)
                                and not is_elite_conviction
                                and not is_long_grid_super)

    history = build_history_scan(feats, sigs, scan_days) if scan_days > 0 else None
    sweep_scan = evaluate_sweep_today(row, sigs_row)

    return {
        "date": str(today.date()),
        "today_ts": today_ts,
        "mode": mode,
        "spy": spy, "vix": vix,
        "move":         float(row["MOVE"])         if "MOVE" in row and pd.notna(row.get("MOVE")) else None,
        "move_crush":   float(row["move_crush"])   if "move_crush" in row and pd.notna(row.get("move_crush")) else None,
        "sma50": float(row["sma50"]), "sma200": float(row["sma200"]),
        "rsi14": float(row["RSI14"]), "macd": float(row["macd"]),
        "bb_width_pct": float(row["bb_width_pct"]) * 100,
        "drawdown": float(row["drawdown"]),
        "fires": fires,
        "fresh_fires": fresh_fires,
        "any_fired": n_fires > 0,
        "any_actionable": any_actionable,
        "is_high_conviction": is_high_conviction,
        "is_strong_conviction": any_actionable and (n_fresh >= 2) and (not is_high_conviction),
        "is_elite_conviction": is_elite_conviction,
        "is_long_grid_super": is_long_grid_super,
        "is_super_high_conviction": is_super_high_conviction,
        "n_fires": n_fires,
        "n_fresh": n_fresh,
        "contract": contract,
        "history": history,
        "sweep_scan": sweep_scan,
    }


def format_text_report(r: dict) -> str:
    out = []
    out.append("═" * 72)
    out.append(f"  SPY LEAPS — STRATEGY SCANNER  •  {r['date']}  •  Mode: {r['mode']}")
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

    if r["mode"] == "HIGH_CONVICTION":
        out.append(f"  🚦 {r['n_fires']} strategies firing  •  "
                   f"email only when ≥{HIGH_CONVICTION_FRESH} same day (today: {r['n_fresh']})")
    else:
        out.append(f"  🚦 {r['n_fires']} of {len(r['fires'])} strategies firing today "
                     f"(no debounce)")
    out.append("")

    if r["any_actionable"]:
        c = r["contract"]
        out.append("  " + "─" * 68)
        if r.get("is_elite_conviction"):
            out.append(f"  🔥🔥🔥 ELITE HC — {r['n_fresh']} strategies incl. "
                        f"{ANCHOR_LONG_GRID} + {SUPER_HC_ANCHOR} (best long-horizon grid + cheap IV)")
            out.append(f"     → Strongest tier: consider sizing up if risk budget allows")
            out.append("  " + "─" * 68)
        elif r.get("is_long_grid_super"):
            out.append(f"  🔥🔥 LONG-GRID SUPER HC — {r['n_fresh']} agree incl. {ANCHOR_LONG_GRID} "
                        f"(VIX<14 / RSI 40–65; best 30y sweep stats)")
            out.append("  " + "─" * 68)
        elif r.get("is_super_high_conviction"):
            out.append(f"  🔥🔥 SUPER HC (cheap IV) — {r['n_fresh']} strategies agree,")
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
        out.append(f"  🟢 {f['key']:<18} ({f['freq']:.1f}/yr  •  "
                   f"win {f['win_rate']}%  •  10yr edge ${f['edge_10yr']:+,})  firing today")
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
    """Compact ranking table for all strategies, biggest 10-yr sweep edge first."""
    lines = []
    lines.append("  ── STRATEGY RANKING (10-yr after-tax edge from sweep CSV when available) ──")
    lines.append("")
    lines.append(f"     {'#':>2}  {'Strategy':<17}  {'Win%':>4}  "
                 f"{'10yr edge':>12}  {'Fires/yr':>8}  Idea")
    lines.append("     " + "─" * 100)
    ranked = strategies_sorted_by_sweep_10y()
    for i, s in enumerate(ranked, 1):
        edge10, win_r, fq = strategy_display_metrics(s)
        lines.append(f"     {i:>2}.  {s.key:<17}  {win_r:>3}%  "
                     f"${edge10:>+10,}  {fq:>7.1f}  {s.layman}")
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
        n_all = len(hc_days)
        cap = HC_HISTORY_DISPLAY_MAX
        tail = hc_days[-cap:] if n_all > cap else hc_days
        omitted = n_all - len(tail)
        lines.append(f"     🔥 {n_all} HIGH-CONVICTION day"
                     f"{'s' if n_all != 1 else ''} "
                     f"(≥{HIGH_CONVICTION_FRESH} strategies firing same day):")
        if omitted > 0:
            lines.append(f"        (Showing newest {len(tail)}; {omitted} older "
                         f"day{'s' if omitted != 1 else ''} omitted — fire-count table "
                         f"below is still for the full {history['days']}d window.)")
        lines.append("")
        lines.append(f"        {'Date':<11}  {'SPY':>7}  {'VIX':>5}  "
                     f"{'#':>2}  Strategies that agreed")
        lines.append("        " + "─" * 92)
        for d in tail:
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

    BUY tier ladder (best subject wins):
      🔥🔥🔥 ELITE HC        →  ≥3 firing + VIX14/RSI grid + A_CHEAP_IV
      🔥🔥 LONG-GRID SUPER  →  ≥3 firing + long-grid row only (best 30y stats)
      🔥🔥 SUPER (cheap IV) →  ≥3 firing + A_CHEAP_IV only (classic SHC)
      🔥 HIGH CONVICTION    →  ≥3 firing, no exclusive anchor combo above
      🟡 STRONG BUY         →  2 firing (not HC)
      🟢 BUY                →  1 firing
      ⚪️ no signal          →  nothing actionable for this mode
    """
    if r["any_actionable"]:
        n = r["n_fresh"]
        fired_keys = ", ".join(r["fresh_fires"])
        if r.get("is_elite_conviction"):
            subject = (f"🔥🔥🔥 ELITE HC — {n} SPY LEAPS incl. "
                       f"{ANCHOR_LONG_GRID} + {SUPER_HC_ANCHOR} ({fired_keys})")
        elif r.get("is_long_grid_super"):
            subject = (f"🔥🔥 LONG-GRID SUPER HC — {n} incl. {ANCHOR_LONG_GRID} "
                       f"({fired_keys})")
        elif r.get("is_super_high_conviction"):
            subject = (f"🔥🔥 SUPER HC (cheap IV) — {n} incl. {SUPER_HC_ANCHOR} "
                       f"({fired_keys})")
        elif r["is_high_conviction"]:
            subject = f"🔥 HIGH CONVICTION — {n} SPY LEAPS signals agree ({fired_keys})"
        elif r.get("is_strong_conviction"):
            subject = f"🟡 STRONG BUY — 2 heuristics agree ({fired_keys})"
        else:
            subject = f"🟢 SPY LEAPS — {n} signal{'s' if n != 1 else ''} firing ({fired_keys})"
    else:
        subject = f"⚪️ SPY LEAPS — no BUY signals today ({r['date']})"
    body = format_email_body(r)
    return subject, body


# ─── 33-yr top-5 sweep scan (per email) ──────────────────────────────────────

SWEEP_CSV   = PROJECT_DIR / "results" / "heuristics_sweep_filtered.csv"
SWEEP_TOP_N = 5
SWEEP_WINDOWS = [1, 2, 5, 10, 30]   # what the user asked for in the email


def _sweep_rule_map() -> dict:
    """Build a {rule_name: callable} lookup from heuristics_sweep.build_rules.

    Cached on first call.  Returns an empty dict if heuristics_sweep is missing
    (script still runs, just without the sweep section).
    """
    if hasattr(_sweep_rule_map, "_cache"):
        return _sweep_rule_map._cache
    try:
        _sweep_rule_map._cache = {n: fn for n, fn, _g in _build_sweep_rules()}
    except Exception:
        _sweep_rule_map._cache = {}
    return _sweep_rule_map._cache


def evaluate_sweep_today(row, sigs_row) -> dict:
    """For each window in SWEEP_WINDOWS, evaluate the top-N rules from the
    filtered sweep CSV and report (rule, edge/yr, fired?).

    Returns a dict {window_yr: [{name, edge_yr, fired, trd, win_rate}, ...]}.
    """
    out: dict[int, list[dict]] = {}
    if not SWEEP_CSV.exists():
        return out
    try:
        df = pd.read_csv(SWEEP_CSV)
    except Exception:
        return out
    rule_map = _sweep_rule_map()
    for w in SWEEP_WINDOWS:
        sub = (df[df["window_yr"] == w]
               .sort_values("edge_aftertax", ascending=False)
               .head(SWEEP_TOP_N))
        rows = []
        for r in sub.itertuples():
            fn = rule_map.get(r.rule)
            try:
                fired = bool(fn(row, sigs_row)) if fn is not None else False
            except Exception:
                fired = False
            rows.append({
                "name":     r.rule,
                "edge_yr":  r.edge_aftertax / w,
                "trades":   int(r.trades),
                "trd_yr":   r.trades_per_yr,
                "win_rate": r.win_rate,
                "fired":    fired,
            })
        out[w] = rows
    return out


def format_sweep_scan_section(scan: dict,
                              windows: list[int] | None = None) -> list[str]:
    """Pretty email section listing top-5 rules per window with fire status.

    ``windows`` defaults to ``SWEEP_WINDOWS``; pass ``EMAIL_SWEEP_WINDOWS`` for
    a shorter email (long horizons only).
    """
    if not scan:
        return []
    use_w = windows if windows is not None else list(SWEEP_WINDOWS)
    lines = []
    lines.append("  ── 33-YR SWEEP TOP-5 (which fire today?) ──")
    if use_w == list(SWEEP_WINDOWS):
        lines.append("     Windows: 1 / 2 / 5 / 10 / 30 yr  •  after-tax edge $/yr vs VOO DCA")
    else:
        lines.append(f"     Windows: {' / '.join(str(w) for w in use_w)} yr only "
                       f"(full ladder in `results/heuristics_sweep_filtered.csv`)")
    lines.append("     (density-filtered ≤4 trades/yr in the CSV)")
    any_fired = []
    for w in use_w:
        rows = scan.get(w, [])
        if not rows:
            continue
        lines.append("")
        lines.append(f"     {w}-YEAR WINDOW")
        for i, rr in enumerate(rows, 1):
            tag = "🟢" if rr["fired"] else "  "
            lines.append(f"     {tag} #{i}  {rr['name']:<38}  "
                         f"{rr['trd_yr']:>3.1f}/yr  "
                         f"win {rr['win_rate']:>4.0f}%  "
                         f"edge ${rr['edge_yr']:>+8,.0f}/yr")
            if rr["fired"]:
                any_fired.append((w, rr["name"]))
    lines.append("")
    if any_fired:
        lines.append(f"     ⚡ {len(any_fired)} top-5 rule(s) FIRING today: "
                     + "; ".join(f"{w}y→{n}" for w, n in any_fired))
    else:
        lines.append("     ⏸️  None of the sweep top-5 fire today — patient regime.")
    return lines


def format_email_ranking_summary(r: dict) -> list[str]:
    """Short 10y-edge leaders for email (full sort order ≈ matrix row order)."""
    ranked = strategies_sorted_by_sweep_10y()
    fired_keys = {f["key"] for f in r["fires"] if f["fired"]}
    parts: list[str] = []
    for s in ranked[:5]:
        edge10, wr, _ = strategy_display_metrics(s)
        tag = " 🟢" if s.key in fired_keys else ""
        parts.append(f"{s.key} (${edge10:+,}, {wr}%){tag}")
    return [
        "  ── 10y sweep edge — top 5 (🟢 = firing today; matrix T column matches) ──",
        "     " + "  •  ".join(parts),
    ]


def format_email_body(r: dict) -> str:
    """Concise email body — only what's needed to act today.

    Layout:
      1. Verdict + market snapshot
      2. Win-% matrix (1y–30y) + T markers + HC / Super-HC hint
      3. Action block if any_actionable
      4. Fired strategies with condition breakdown (WHY)
      5. One-line 10y-edge top-5 (full table lives in the matrix above)
      6. Sweep top-5 for long windows only
      7. SELL block (if any indication) + footer
    """
    out: list[str] = []
    out.append("═" * 72)
    out.append(f"  SPY LEAPS — DAILY SIGNAL  •  {r['date']}")
    out.append("═" * 72)
    out.append("")

    # ── 1. Verdict + market snapshot ─────────────────────────────────────────
    if r.get("is_elite_conviction"):
        verdict = (f"🔥🔥🔥 ELITE HC BUY — {r['n_fresh']} agree "
                   f"({ANCHOR_LONG_GRID} + {SUPER_HC_ANCHOR})")
    elif r.get("is_long_grid_super"):
        verdict = (f"🔥🔥 LONG-GRID SUPER HC — {r['n_fresh']} agree "
                   f"(incl. {ANCHOR_LONG_GRID})")
    elif r.get("is_super_high_conviction"):
        verdict = (f"🔥🔥 SUPER HC (cheap IV) — {r['n_fresh']} incl. {SUPER_HC_ANCHOR}")
    elif r["is_high_conviction"]:
        verdict = f"🔥 HIGH CONVICTION BUY — {r['n_fresh']} strategies agree"
    elif r.get("is_strong_conviction"):
        verdict = (f"🟡 STRONG BUY — 2 heuristics agree "
                   f"({', '.join(r['fresh_fires'])})")
    elif r["any_actionable"]:
        verdict = f"🟢 BUY — {r['n_fresh']} heuristic{'s' if r['n_fresh'] != 1 else ''} firing"
    else:
        verdict = "⚪️ NO ACTION — no buy signals for this notification mode"
    out.append(f"  {verdict}")
    move_str = f"  •  MOVE {r['move']:.1f}" if r.get("move") is not None else ""
    out.append(f"  SPY ${r['spy']:.2f}  •  VIX {r['vix']:.1f}{move_str}  •  "
               f"RSI {r['rsi14']:.0f}  •  50DMA ${r['sma50']:.0f}  •  "
               f"200DMA ${r['sma200']:.0f}  •  DD {r['drawdown']:+.1f}%")
    out.append("")
    out.extend(format_win_rate_matrix_lines(r))
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
            out.append(f"  🟢 {f['key']:<22} "
                       f"win {f['win_rate']}%, 10yr edge ${f['edge_10yr']:+,}  (firing)")
            out.append(f"     {f['layman']}")
            for c in f["conds"]:
                out.append(f"       {c}")
        out.append("")

    # ── 4. 10y leaders (one line; full ranking is implicit in matrix row order) ─
    out.extend(format_email_ranking_summary(r))
    out.append("")

    # ── 5. 33-yr sweep top-5 — long windows only in email ────────────────────
    sweep_scan = r.get("sweep_scan") or {}
    if sweep_scan:
        out.extend(format_sweep_scan_section(sweep_scan, windows=EMAIL_SWEEP_WINDOWS))
        out.append("")

    # ── 6. SELL-side block (only if there is ANY sell indication today) ──────
    # Skip entirely on quiet HOLD days so the email stays BUY-focused.
    sell = r.get("sell")
    if sell and _has_sell_indication(sell):
        out.append(format_sell_email_section(sell))
        out.append("")

    # ── 7. Footer ────────────────────────────────────────────────────────────
    out.append("─" * 72)
    out.append("  Run `python final_leaps/daily_signal_top10.py --force` for full")
    out.append("  diagnostics (all strategies' condition breakdowns + 5-yr scan).")
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
    parser.add_argument("--mode",  choices=["RAW", "FREQUENT", "DEBOUNCED", "HIGH_CONVICTION"],
                        default="DEBOUNCED",
                        help="BUY gate: DEBOUNCED or RAW/FREQUENT = email when ≥1 strategy fires "
                             "(no debounce; ~150+ sessions/yr). HIGH_CONVICTION = ≥3 firing (~24/yr).")
    parser.add_argument("--repeat-daily", action="store_true",
                        help="Allow more than one notification per calendar day "
                             "(skips .last_notified date de-dupe; use with FREQUENT for intraday)")
    parser.add_argument("--reset-notify-state", action="store_true",
                        help="Delete .last_notified_top10.json before running so a normal run "
                             "can email again (same as removing that file by hand).")
    parser.add_argument("--scan",  type=int, default=DEFAULT_SCAN_DAYS,
                        help=f"Scan past N trading days for HIGH-CONVICTION days "
                             f"(default {DEFAULT_SCAN_DAYS}; pass 0 to disable)")
    parser.add_argument("--no-sell", action="store_true",
                        help="Skip the SELL-side scanner (default: both BUY and SELL "
                             "are run + combined into a single email)")
    args = parser.parse_args()

    if args.reset_notify_state and LAST_NOTIFIED_PATH.exists():
        LAST_NOTIFIED_PATH.unlink()
        print(f"  🗑  Cleared notify state ({LAST_NOTIFIED_PATH.name}) — next send is not "
              f"blocked by prior same-day delivery.\n")

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
            sell_console = _ft(sell_report, compact=True)
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
    if should_send and (args.force or args.repeat_daily or should_notify_again(report)):
        subject = _combined_subject(report, sell_report)
        body    = format_email_body(report)
        short_msg = _combined_short_msg(report, sell_report)
        priority = 0
        if buy_actionable or sell_actionable:
            priority = 1
        if buy_actionable and (report.get("is_elite_conviction")
                               or report.get("is_long_grid_super")):
            priority = 2
        n_strat = len(STRATEGIES)
        sent = notify_all(
            title=subject,
            message=short_msg,                          # for macOS/Pushover (short)
            body=body,                                  # full detailed email body
            subtitle=f"{report['date']}  •  {report['n_fresh']}/{n_strat} firing",
            priority=priority,
        )
        delivery_ok = any(sent.values())
        if not delivery_ok:
            print("  ⚠️  No notification channel succeeded (check SMTP_USER/SMTP_PASS "
                  "in env or ~/.leaps_signal_config.json, or Pushover/macOS). "
                  "State not saved — next run will retry.")
            sys.exit(1)
        elif buy_actionable:
            remember_notification(report)
    elif args.quiet:
        print("  🔇 Quiet mode — no notification.")
    elif (buy_actionable or sell_actionable) and not args.force:
        print("  ℹ️  Already notified for this date.")
    else:
        print("  ℹ️  No BUY or SELL signals firing — no notification sent.")

    print(f"  📝 Logged to {LOG_PATH}\n")


def _combined_subject(r: dict, sell: dict | None) -> str:
    """Build one subject line that summarizes both BUY and SELL state."""
    buy_part = ""
    if r["any_actionable"]:
        keys = ", ".join(r["fresh_fires"])
        n = r["n_fresh"]
        if r.get("is_elite_conviction"):
            flame = "🔥🔥🔥 ELITE "
        elif r.get("is_long_grid_super"):
            flame = "🔥🔥 LG "
        elif r.get("is_super_high_conviction"):
            flame = "🔥🔥 SHC "
        elif r["is_high_conviction"]:
            flame = "🔥 HC "
        elif r.get("is_strong_conviction"):
            flame = "🟡 STRONG "
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
        if r.get("is_elite_conviction"):
            tier = "🔥🔥🔥 ELITE"
        elif r.get("is_long_grid_super"):
            tier = "🔥🔥 LG-SUPER"
        elif r.get("is_super_high_conviction"):
            tier = "🔥🔥 SHC-IV"
        elif r["is_high_conviction"]:
            tier = "🔥 HC"
        elif r.get("is_strong_conviction"):
            tier = "🟡 STRONG"
        else:
            tier = "🟢"
        nf = r["n_fresh"]
        buy = (f"{tier} {nf} firing  •  "
               f"SPY ${r['spy']:.0f}  VIX {r['vix']:.1f}\n"
               f"Buy: SPY ${r['contract']['strike']:.0f} "
               f"{r['contract']['expiry']} call @ "
               f"${r['contract']['premium_mid']:.2f}/sh "
               f"(~${r['contract']['cost']:.0f}/cntrct)")
    else:
        buy = (f"SPY ${r['spy']:.0f}  VIX {r['vix']:.1f}  — no BUY signals for this mode.")
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
