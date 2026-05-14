"""
Top-10 SELL signals for SPY LEAPS — mirror of the buy-side top-10.

Each rule takes a single day's market features and returns:
    (fired: bool, conditions: list[str])

Conditions are human-readable so the daily scanner can show *why* a sell
fired (or how close it is to firing).

Design principles (mirror of buy side):
  1. Market-state only (no option-specific P&L) so they generalize.
  2. Mix of trend-break, vol-spike, momentum-loss, and regime-shift rules
     so they don't all fire on the same day.
  3. Each rule has a layman one-liner so the email can explain the *why*.

These rules are evaluated by sell_backtest.py against the historical
LEAPS positions from the buy-side strategies to pick the best ones.
"""

from __future__ import annotations
import pandas as pd


def cv(label: str, ok: bool, val: str) -> str:
    """Format a condition with check/cross + actual value."""
    return f"{'✅' if ok else '❌'} {label}  ({val})"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _rsi_max_5d(history: pd.Series, today_idx: int) -> float:
    """Max RSI over last 5 trading days (inclusive)."""
    if today_idx < 4:
        return float(history.iloc[today_idx])
    return float(history.iloc[today_idx - 4 : today_idx + 1].max())


def _macd_crossed_zero(macd_series: pd.Series, today_idx: int, look: int = 5) -> bool:
    """True if MACD was >0 within the last `look` days but is now <=0."""
    if today_idx < look:
        return False
    window = macd_series.iloc[today_idx - look : today_idx + 1]
    return bool((window.iloc[:-1] > 0).any() and window.iloc[-1] <= 0)


# ─── 10 SELL RULES ───────────────────────────────────────────────────────────
# Each returns (fired, conds_list).

def explain_sell(key: str, row, df: pd.DataFrame, today_idx: int) -> tuple[bool, list[str]]:
    spy = float(row["SPY"])
    vix = float(row["VIX"])

    if key == "S1_VIX_SPIKE":
        c1 = vix > 30
        return c1, [cv("VIX > 30 (fear regime)", c1, f"VIX {vix:.1f}")]

    if key == "S2_VIX_PANIC":
        slope = float(row["vix_slope5"])
        c1 = slope > 6
        return c1, [cv("VIX 5-day rise > +6 pts", c1, f"slope +{slope:.1f}")]

    if key == "S3_TREND_BREAK":
        sma50 = float(row["sma50"])
        threshold = sma50 * 0.97
        c1 = spy < threshold
        return c1, [
            cv("SPY < 50DMA × 0.97 (3% break)", c1,
               f"SPY ${spy:.0f} vs threshold ${threshold:.0f} (50DMA ${sma50:.0f})")
        ]

    if key == "S4_DEATH_CROSS":
        sma50 = float(row["sma50"])
        sma200 = float(row["sma200"])
        c1 = sma50 < sma200
        return c1, [
            cv("50DMA < 200DMA (death cross)", c1,
               f"50DMA ${sma50:.0f} vs 200DMA ${sma200:.0f}")
        ]

    if key == "S5_NEW_60D_LOW":
        low60 = float(df["SPY"].iloc[max(0, today_idx-59):today_idx+1].min())
        c1 = spy <= low60
        return c1, [
            cv("SPY at new 60-day low", c1,
               f"SPY ${spy:.0f} vs 60d low ${low60:.0f}")
        ]

    if key == "S6_MACD_BEAR":
        macd_val = float(row["macd"])
        crossed = _macd_crossed_zero(df["macd"], today_idx, look=5)
        c1 = macd_val < 0
        c2 = crossed
        return (c1 and c2), [
            cv("MACD < 0 (momentum negative)", c1, f"MACD {macd_val:+.2f}"),
            cv("MACD crossed below 0 in last 5d", c2,
               "yes" if c2 else "no recent cross"),
        ]

    if key == "S7_RSI_REVERSE":
        rsi_now = float(row["RSI14"])
        rsi_max5 = _rsi_max_5d(df["RSI14"], today_idx)
        c1 = rsi_max5 > 70
        c2 = rsi_now < 55
        return (c1 and c2), [
            cv("RSI hit > 70 within last 5d", c1, f"5d max RSI {rsi_max5:.1f}"),
            cv("RSI now < 55 (reversal)", c2, f"RSI {rsi_now:.1f}"),
        ]

    if key == "S8_DRAWDOWN_10":
        running_max = float(df["SPY"].iloc[:today_idx+1].max())
        dd = (spy / running_max - 1) * 100
        c1 = dd < -10
        return c1, [
            cv("SPY drawdown from ATH < -10%", c1,
               f"DD {dd:+.1f}% (peak ${running_max:.0f})")
        ]

    if key == "S9_BB_LOWER_BREAK":
        bb_lower = float(row["bb_lower"])
        c1 = spy < bb_lower
        return c1, [
            cv("SPY < lower Bollinger band", c1,
               f"SPY ${spy:.0f} vs lower band ${bb_lower:.0f}")
        ]

    if key == "S10_VIX_REGIME":
        vix_avg = float(row.get("vix_30d_mean", vix))
        threshold = vix_avg * 1.5
        c1 = vix > threshold and vix > 22  # gate so it doesn't fire in ultra-low vol
        return c1, [
            cv("VIX > 1.5× its 30-day average AND VIX > 22", c1,
               f"VIX {vix:.1f} vs 30d avg {vix_avg:.1f} × 1.5 = {threshold:.1f}")
        ]

    return False, [f"❌ Unknown sell rule: {key}"]


# ─── Rule directory ──────────────────────────────────────────────────────────

SELL_RULES = [
    ("S1_VIX_SPIKE",
     "Fear regime — VIX above 30 means big institutional repositioning"),
    ("S2_VIX_PANIC",
     "Vol accelerating — VIX jumped >6 points in 5 days (panic in progress)"),
    ("S3_TREND_BREAK",
     "Trend broken — SPY closed 3%+ below 50DMA (medium-term uptrend lost)"),
    ("S4_DEATH_CROSS",
     "Death cross — 50DMA fell below 200DMA (long-term trend reversal)"),
    ("S5_NEW_60D_LOW",
     "Bearish breakout — SPY just made a new 60-day low"),
    ("S6_MACD_BEAR",
     "Momentum lost — MACD crossed below zero (and was above recently)"),
    ("S7_RSI_REVERSE",
     "Overbought reversal — RSI hit 70+ recently then crashed below 55"),
    ("S8_DRAWDOWN_10",
     "Material correction — SPY now 10%+ below its all-time high"),
    ("S9_BB_LOWER_BREAK",
     "Volatility expansion — SPY broke below the lower Bollinger band"),
    ("S10_VIX_REGIME",
     "Vol regime shift — VIX 1.5× its 30-day average (and > 22)"),
]
