"""
Top-10 ROLLOVER signals for SPY LEAPS — mirror of the buy & sell-side top-10.

A "rollover" = close the currently-held LEAPS contract and immediately
open a new further-dated / re-struck one.  Unlike sell rules (which only
look at market state) rollover rules also need to know about the
*position* (DTE, current delta, current P&L).

Rule signature
    explain_roll(key, pos, row, df, today_idx) -> (fired, conds_list)

Where `pos` is a dict produced by `pos_state(...)` containing:
    entry_date, expiry, strike, entry_premium, contracts,
    mark_now, pct_pnl, delta_now, dte_days

Each rule returns a `(fired, [human-readable conditions])` tuple so the
daily roll-check email/console can show *why* a roll fired (or how close
it is to firing).

Design principles (mirror of buy & sell sides):
  1. Mix of profit-lock, time-decay, delta-stretch, and vol-regime rules
     so they don't all fire at once.
  2. Each rule has a layman one-liner.
  3. Backtester (roll_backtest.py) evaluates every rule and the recommended
     composites against a "never roll" baseline.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
import pandas as pd
from scipy.stats import norm


# ─── Black-Scholes helpers (call price + delta) ──────────────────────────────

def bs_call_px(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Plain Black-Scholes call price (kept local so this module is
    self-contained; matches `strategy_backtest.bs_call`)."""
    if T <= 1e-9 or sigma <= 1e-9:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """∂C/∂S for a vanilla call — N(d1).  Used by DELTA_HIGH / DELTA_LOW rules."""
    if T <= 1e-9 or sigma <= 1e-9:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return float(norm.cdf(d1))


# ─── Position snapshot helper ────────────────────────────────────────────────

@dataclass
class PositionSnap:
    """One open LEAPS lot, refreshed for `today`.

    `mark_now` and `delta_now` are computed from today's SPY + IV; `pct_pnl`
    is the % return vs entry premium.
    """
    entry_date: pd.Timestamp
    expiry:     pd.Timestamp
    strike:     float
    entry_premium: float        # per share
    contracts:  int
    mark_now:   float           # per share, today's MTM
    pct_pnl:    float           # decimal, e.g. +0.50
    delta_now:  float           # 0..1
    dte_days:   int             # days to expiry remaining


def snap_position(pos: dict, row, r: float = 0.045) -> PositionSnap:
    """Build a PositionSnap from a stored position dict + today's market row.

    The stored `pos` dict must contain: entry_date, expiry, strike,
    entry_premium, contracts.  Today's row needs SPY + (IV1Y_cal or VIX).
    """
    spy   = float(row["SPY"])
    sigma = (float(row["IV1Y_cal"])
             if "IV1Y_cal" in row and pd.notna(row["IV1Y_cal"])
             else float(row["VIX"]) / 100.0)
    expiry = pd.Timestamp(pos["expiry"])
    today  = pd.Timestamp(row.name) if hasattr(row, "name") else pd.Timestamp.today()
    T_rem  = max((expiry - today).days / 365.25, 1e-6)
    K      = float(pos["strike"])
    mark   = bs_call_px(spy, K, T_rem, r, sigma)
    delta  = bs_call_delta(spy, K, T_rem, r, sigma)
    entry_premium = float(pos["entry_premium"])
    pct = (mark - entry_premium) / entry_premium if entry_premium > 0 else 0.0
    return PositionSnap(
        entry_date    = pd.Timestamp(pos["entry_date"]),
        expiry        = expiry,
        strike        = K,
        entry_premium = entry_premium,
        contracts     = int(pos.get("contracts", 1)),
        mark_now      = mark,
        pct_pnl       = pct,
        delta_now     = delta,
        dte_days      = int((expiry - today).days),
    )


def cv(label: str, ok: bool, val: str) -> str:
    """Format a condition with check/cross + actual value."""
    return f"{'✅' if ok else '❌'} {label}  ({val})"


# ─── 10 ROLLOVER RULES ───────────────────────────────────────────────────────

def explain_roll(key: str, pos: PositionSnap, row, df: pd.DataFrame,
                 today_idx: int) -> tuple[bool, list[str]]:
    """Evaluate ONE rollover rule against today's position + market state."""
    vix = float(row["VIX"])

    # R1 — Lock-in modest profit (+50%)
    if key == "R1_PROFIT_50":
        c1 = pos.pct_pnl >= 0.50
        return c1, [cv("Position P&L ≥ +50%", c1, f"P&L {pos.pct_pnl*100:+.1f}%")]

    # R2 — Lock-in big profit (+100%, doubled)
    if key == "R2_PROFIT_100":
        c1 = pos.pct_pnl >= 1.00
        return c1, [cv("Position P&L ≥ +100% (doubled)", c1,
                       f"P&L {pos.pct_pnl*100:+.1f}%")]

    # R3 — Calendar: 1y left.  2-year LEAPS theta starts to bite below 365 DTE.
    if key == "R3_CAL_365":
        c1 = pos.dte_days < 365
        return c1, [cv("DTE < 365 days", c1, f"DTE {pos.dte_days}d")]

    # R4 — Calendar: 180d left (theta hot zone).
    if key == "R4_CAL_180":
        c1 = pos.dte_days < 180
        return c1, [cv("DTE < 180 days (theta hot zone)", c1,
                       f"DTE {pos.dte_days}d")]

    # R5 — Delta too high (deep ITM, paying mostly intrinsic).
    if key == "R5_DELTA_HIGH":
        c1 = pos.delta_now > 0.85
        return c1, [cv("Delta > 0.85 (deep ITM, leverage gone)", c1,
                       f"Δ {pos.delta_now:.2f}")]

    # R6 — Delta too low (deep OTM, mostly hope premium).
    if key == "R6_DELTA_LOW":
        c1 = pos.delta_now < 0.30
        return c1, [cv("Delta < 0.30 (deep OTM, mostly hope)", c1,
                       f"Δ {pos.delta_now:.2f}")]

    # R7 — Profit + calendar (capture gain AND extend duration in one move).
    if key == "R7_PROFIT_AND_CAL":
        c1 = pos.pct_pnl >= 0.30
        c2 = pos.dte_days < 365
        return (c1 and c2), [
            cv("Position P&L ≥ +30%", c1, f"P&L {pos.pct_pnl*100:+.1f}%"),
            cv("DTE < 365 days",       c2, f"DTE {pos.dte_days}d"),
        ]

    # R8 — Roll into cheap IV only (VIX < 16 so the new leg is cheaper).
    if key == "R8_VIX_CHEAP":
        c1 = vix < 16
        c2 = pos.pct_pnl >= 0.20
        return (c1 and c2), [
            cv("VIX < 16 (new LEAPS will be cheap)", c1, f"VIX {vix:.1f}"),
            cv("Existing position up ≥ +20%",        c2, f"P&L {pos.pct_pnl*100:+.1f}%"),
        ]

    # R9 — "Either / or" composite: PROFIT_50 OR CAL_365 (whichever first).
    if key == "R9_PROFIT_OR_CAL":
        c1 = pos.pct_pnl >= 0.50
        c2 = pos.dte_days < 365
        return (c1 or c2), [
            cv("P&L ≥ +50% OR DTE < 365", (c1 or c2),
               f"P&L {pos.pct_pnl*100:+.1f}%  •  DTE {pos.dte_days}d"),
        ]

    # R10 — Quarterly cadence: roll every ~180d regardless (duration ladder).
    if key == "R10_QUARTERLY":
        held = (pos.entry_date - pd.Timestamp(row.name)).days
        c1 = abs(held) >= 180
        return c1, [cv("Position held ≥ 180d (quarterly cadence)", c1,
                       f"held {abs(held)}d")]

    return False, [f"❌ Unknown roll rule: {key}"]


# ─── Rule directory ──────────────────────────────────────────────────────────

ROLL_RULES = [
    ("R1_PROFIT_50",
     "Lock-in +50%: roll up & out to reset delta after a solid gain"),
    ("R2_PROFIT_100",
     "Lock-in +100%: position doubled, take the win and redeploy"),
    ("R3_CAL_365",
     "1 year left: 2-year LEAPS theta starts to bite below 365 DTE"),
    ("R4_CAL_180",
     "6 months left: theta hot zone — pay up for time, don't get stuck"),
    ("R5_DELTA_HIGH",
     "Delta > 0.85: deep ITM, you're paying mostly intrinsic — leverage gone"),
    ("R6_DELTA_LOW",
     "Delta < 0.30: deep OTM, you're paying for hope — recycle into ATM/15%"),
    ("R7_PROFIT_AND_CAL",
     "Up +30% AND <1y left: kill two birds — capture gain + extend duration"),
    ("R8_VIX_CHEAP",
     "VIX < 16 + position up +20%: replace expensive old leg with cheap new one"),
    ("R9_PROFIT_OR_CAL",
     "Either trigger fires first: +50% gain OR <365 DTE → roll"),
    ("R10_QUARTERLY",
     "Calendar cadence: roll every 180 days regardless (smooth duration ladder)"),
]


# ─── Composites used by the daily scanner ────────────────────────────────────
# Picked by roll_backtest.py — the daily scanner highlights any of these as
# 🔥 PRIORITY firing.  Default = the two most efficient rules from the
# back-test (kept here so daily_roll_check has something to recommend before
# the user runs the back-test).
RECOMMENDED_KEYS_DEFAULT = ["R7_PROFIT_AND_CAL", "R3_CAL_365"]
