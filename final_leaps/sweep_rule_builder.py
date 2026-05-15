"""Parametric BUY rule builder shared by `daily_signal_top10.py` and `heuristics_sweep.py`.

Kept in a tiny module so GitHub Actions can run the daily scanner without
committing the full sweep driver (matplotlib / tqdm / etc.).
"""

from __future__ import annotations

import pandas as pd


def make_rule(
    vix_max: float | None = None,
    vix_min: float | None = None,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    require_above_50dma: bool = False,
    require_above_200dma: bool = False,
    require_50_above_200: bool = False,
    require_macd_positive: bool = False,
    require_new_60d_high: bool = False,
    bb_width_max: float | None = None,
    require_bb_upper: bool = False,
    drawdown_min: float | None = None,
    drawdown_max: float | None = None,
    vix_crush_min: float | None = None,
    vix_30d_mean_max: float | None = None,
    move_max: float | None = None,
    move_min: float | None = None,
    move_pctile_max: float | None = None,
    move_crush_min: float | None = None,
    require_move_falling: bool = False,
    move_30d_mean_max: float | None = None,
):
    """Build a callable entry rule `(row, sigs_row) -> bool` from optional gates."""

    def rule(row, _sigs_row):
        if vix_max is not None and row["VIX"] >= vix_max:
            return False
        if vix_min is not None and row["VIX"] < vix_min:
            return False
        if rsi_min is not None and row["RSI14"] < rsi_min:
            return False
        if rsi_max is not None and row["RSI14"] > rsi_max:
            return False
        if require_above_50dma and not bool(row["spy_above_50"]):
            return False
        if require_above_200dma and not bool(row["spy_above_200"]):
            return False
        if require_50_above_200 and not (row["sma50"] > row["sma200"]):
            return False
        if require_macd_positive and not (row["macd"] > 0):
            return False
        if require_new_60d_high and not bool(row["is_new_high60"]):
            return False
        if bb_width_max is not None and not (row["bb_width_pct"] < bb_width_max):
            return False
        if require_bb_upper and not (row["SPY"] >= row["bb_upper"]):
            return False
        if drawdown_min is not None and row["drawdown"] < drawdown_min:
            return False
        if drawdown_max is not None and row["drawdown"] > drawdown_max:
            return False
        if vix_crush_min is not None and not (row["vix_crush"] >= vix_crush_min):
            return False
        if vix_30d_mean_max is not None and not (row["vix_30d_mean"] < vix_30d_mean_max):
            return False
        move_now = row.get("MOVE")
        if any(p is not None for p in
               (move_max, move_min, move_pctile_max, move_crush_min, move_30d_mean_max)
               ) or require_move_falling:
            if move_now is None or (isinstance(move_now, float) and pd.isna(move_now)):
                return False
        if move_max is not None and move_now >= move_max:
            return False
        if move_min is not None and move_now < move_min:
            return False
        if move_pctile_max is not None:
            pctile = row.get("move_pctile")
            if pctile is None or pd.isna(pctile) or pctile >= move_pctile_max:
                return False
        if move_crush_min is not None:
            crush = row.get("move_crush")
            if crush is None or pd.isna(crush) or crush < move_crush_min:
                return False
        if require_move_falling and not bool(row.get("move_falling", False)):
            return False
        if move_30d_mean_max is not None:
            mm = row.get("move_30d_mean")
            if mm is None or pd.isna(mm) or mm >= move_30d_mean_max:
                return False
        return True

    return rule
