"""
Hybrid VOO + Tactical LEAPS Backtester
======================================

Evaluates whether a $100k portfolio split as $80k passive VOO (SPY proxy)
and $20k tactical 2-year ATM SPY LEAPS calls (priced via Black-Scholes,
sized via a VIX-based volatility regime score) improves risk-adjusted
returns vs. a pure passive benchmark.

Run:
    python backtest.py --config config.yaml

Outputs (under ./results/ by default):
    - equity_curves.csv, daily_state.csv, leaps_trades.csv, metrics.json
    - equity_curves.png, vix_vs_allocation.png, vol_score.png, drawdown.png

This is a regime-based simulation. It uses VIX as an implied-volatility
proxy and re-prices a single ATM SPY call lot daily with Black-Scholes.
It is NOT a real options-chain backtest -- bid/ask, skew, and early
assignment are not modeled.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm
from tqdm import tqdm

try:
    import yfinance as yf
except ImportError:
    yf = None  # Handled at runtime in load_data()


# =============================================================================
# Config
# =============================================================================

def load_config(path: str) -> dict:
    """Load YAML config from disk and return a plain dict."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


# =============================================================================
# Data loading (with on-disk CSV cache to avoid repeated yfinance calls)
# =============================================================================

def _date_range(cfg: dict) -> tuple[str, str]:
    """Resolve start/end dates from config."""
    end = cfg["data"]["end_date"] or datetime.today().strftime("%Y-%m-%d")
    if cfg["data"]["start_date"]:
        start = cfg["data"]["start_date"]
    else:
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        start = (end_dt - timedelta(days=int(cfg["data"]["years"] * 365.25))).strftime("%Y-%m-%d")
    return start, end


def load_data(cfg: dict) -> pd.DataFrame:
    """
    Download SPY adjusted close + ^VIX close from yfinance and cache to CSV.

    Returns a single DataFrame indexed by trading day with columns:
        SPY, VIX
    """
    cache_dir = Path(cfg["data"]["cache_dir"])
    cache_dir.mkdir(exist_ok=True, parents=True)

    start, end = _date_range(cfg)
    cache_file = cache_dir / f"market_{start}_{end}.csv"

    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["Date"], index_col="Date")
        return df

    if yf is None:
        raise ImportError("yfinance is not installed. Run: pip install -r requirements.txt")

    print(f"Downloading {cfg['data']['spy_ticker']} and {cfg['data']['vix_ticker']} "
          f"from {start} to {end} ...")

    spy_raw = yf.download(
        cfg["data"]["spy_ticker"],
        start=start, end=end,
        auto_adjust=True,        # 'Close' becomes adjusted close
        progress=False,
    )
    vix_raw = yf.download(
        cfg["data"]["vix_ticker"],
        start=start, end=end,
        auto_adjust=False,
        progress=False,
    )

    if spy_raw.empty or vix_raw.empty:
        raise RuntimeError("yfinance returned no data. Check tickers/network.")

    # yfinance can return MultiIndex columns when downloading a single ticker
    spy_close = spy_raw["Close"]
    if isinstance(spy_close, pd.DataFrame):
        spy_close = spy_close.iloc[:, 0]

    vix_close = vix_raw["Close"]
    if isinstance(vix_close, pd.DataFrame):
        vix_close = vix_close.iloc[:, 0]

    df = pd.DataFrame({"SPY": spy_close, "VIX": vix_close}).dropna()
    df.index.name = "Date"
    df.to_csv(cache_file)
    return df


# =============================================================================
# Volatility model
# =============================================================================

def compute_vol_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Build the volatility-regime feature set:
        - vix_pct          : rolling percentile rank of VIX (0..1)
        - realized_vol     : 20-day annualized std dev of SPY log returns
        - realized_vol_pct : rolling percentile rank of realized_vol (0..1)
        - vix_slope        : N-day change in VIX (absolute points)
        - vix_regime       : VIX / 60-day VIX average
        - vol_score        : composite 0..100 score
    """
    out = df.copy()
    vm = cfg["vol_model"]

    # 1. VIX percentile (252-day rolling)
    out["vix_pct"] = (
        out["VIX"]
        .rolling(vm["vix_percentile_window"], min_periods=30)
        .apply(lambda x: (x.rank(pct=True).iloc[-1]), raw=False)
    )

    # 2. Realized vol of SPY (annualized)
    log_ret = np.log(out["SPY"] / out["SPY"].shift(1))
    out["spy_log_ret"] = log_ret
    out["realized_vol"] = (
        log_ret.rolling(vm["realized_vol_window"]).std() * math.sqrt(252)
    )
    out["realized_vol_pct"] = (
        out["realized_vol"]
        .rolling(vm["realized_vol_percentile_window"], min_periods=30)
        .apply(lambda x: (x.rank(pct=True).iloc[-1]), raw=False)
    )

    # 3. VIX slope (absolute point change over N days)
    out["vix_slope"] = out["VIX"] - out["VIX"].shift(vm["vix_slope_window"])

    # Slope component for VolScore: penalize rising vol, reward falling vol.
    # Map slope to 0..1 via a smooth squashing function (tanh).
    # Falling VIX (negative slope) -> slope_component ~ 1 (favorable -> high VolScore)
    # Rising VIX                   -> slope_component ~ 0
    out["slope_component"] = 1.0 - 0.5 * (1.0 + np.tanh(out["vix_slope"] / 5.0))

    # 4. Volatility regime ratio
    out["vix_regime"] = out["VIX"] / out["VIX"].rolling(vm["vix_regime_window"]).mean()

    # Composite VolScore (0..100)
    w = vm["weights"]
    out["vol_score"] = 100.0 * (
        w["vix_pct"] * out["vix_pct"]
        + w["realized_vol_pct"] * out["realized_vol_pct"]
        + w["slope_component"] * out["slope_component"]
    )

    return out


# =============================================================================
# LEAPS entry signal (0-10)
# =============================================================================

def compute_leaps_score(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Build a 0-10 LEAPS conviction score.

    Logic:
        +3  VIX percentile > threshold (high fear -> mean reversion edge)
        +3  VIX falling sharply (critical: vol crush is the LEAPS tailwind)
        +2  SPY drawdown in sweet spot [-25%, -10%]
        +2  SPY near or above 200DMA (stabilizing)
        -3  VIX rising sharply
        -2  SPY making a new N-day low
    Clamp to [0, 10].
    """
    out = df.copy()
    s = cfg["leaps_signal"]

    out["spy_sma"] = out["SPY"].rolling(s["sma_window"]).mean()
    out["spy_peak"] = out["SPY"].cummax()
    out["spy_drawdown"] = out["SPY"] / out["spy_peak"] - 1.0
    out["spy_new_low"] = out["SPY"] <= out["SPY"].rolling(s["new_low_window"]).min()

    score = pd.Series(0.0, index=out.index)

    # Bonuses
    score += np.where(out["vix_pct"] > s["vix_pct_threshold"] / 100.0, 3.0, 0.0)
    score += np.where(out["vix_slope"] <= s["vix_slope_falling"], 3.0, 0.0)
    in_dd_band = (out["spy_drawdown"] <= s["drawdown_max"]) & (
        out["spy_drawdown"] >= s["drawdown_min"]
    )
    score += np.where(in_dd_band, 2.0, 0.0)
    near_or_above_sma = (out["SPY"] >= out["spy_sma"] * (1 - s["sma_proximity"]))
    score += np.where(near_or_above_sma.fillna(False), 2.0, 0.0)

    # Penalties
    score -= np.where(out["vix_slope"] >= s["vix_slope_rising"], 3.0, 0.0)
    score -= np.where(out["spy_new_low"].fillna(False), 2.0, 0.0)

    out["leaps_score"] = score.clip(lower=0, upper=10)
    return out


def target_exposure(score: float, cfg: dict) -> float:
    """Map a 0-10 LEAPS score to a fraction (0..1) of the LEAPS sleeve to deploy."""
    r = cfg["allocation_rules"]
    if score >= r["high_score_min"]:
        return r["high_exposure"]
    if score >= r["mid_score_min"]:
        return r["mid_exposure"]
    return r["low_exposure"]


# =============================================================================
# Black-Scholes pricing
# =============================================================================

def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes price of a European call option.

    S     : underlying price
    K     : strike
    T     : time to expiration in years (must be > 0)
    r     : continuously compounded risk-free rate (annual)
    sigma : annualized volatility (decimal, e.g. 0.25)
    """
    if T <= 1e-9 or sigma <= 1e-9:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def implied_sigma_from_vix(vix: float, vix_slope: float, cfg: dict) -> float:
    """
    Convert VIX (in points) to a sigma input for Black-Scholes:
        sigma = (VIX/100) * (1 + 0.15 * slope_factor)

    slope_factor squashes vix_slope into ~[-1, 1] via tanh so the
    multiplier stays in a sensible range.
    """
    adj = cfg["options"]["vol_term_adjust"]
    slope_factor = math.tanh((vix_slope or 0.0) / 5.0)
    return max(0.05, (vix / 100.0) * (1.0 + adj * slope_factor))


# =============================================================================
# Portfolio simulation
# =============================================================================

@dataclass
class LeapsLot:
    """A single open LEAPS position (one strike / one expiry)."""
    entry_date: pd.Timestamp
    expiry_date: pd.Timestamp
    strike: float
    contracts: float                # Allowed fractional for clean accounting
    entry_premium: float            # $/contract at entry (BS price * 100)
    entry_underlying: float
    entry_sigma: float


@dataclass
class TradeRecord:
    """One full LEAPS cycle (entry to full exit) for diagnostics."""
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_underlying: float
    exit_underlying: float
    strike: float
    contracts: float
    entry_premium: float
    exit_premium: float
    pnl: float
    pct_return: float
    reason: str
    vol_score_at_entry: float
    leaps_score_at_entry: float


def simulate(df: pd.DataFrame, cfg: dict) -> dict:
    """
    Run the daily simulation.

    For each trading day:
        1. Mark the existing LEAPS lot to market with Black-Scholes.
        2. Force-roll if remaining time-to-expiry <= roll_remaining_years.
        3. (On rebalance days) compute target sleeve exposure from LEAPS score
           and resize the lot by buying/selling contracts at the BS mark.
        4. Accrue T-bill yield on un-deployed LEAPS cash.
        5. VOO sleeve compounds with daily SPY return.

    Returns a dict with:
        equity, trades, diagnostics
    """
    p = cfg["portfolio"]
    o = cfg["options"]
    rules = cfg["allocation_rules"]

    starting_cap = p["starting_capital"]
    voo_alloc = p["voo_allocation"]
    leaps_alloc = p["leaps_allocation"]
    r = p["risk_free_rate"]

    # ---- Initialize sleeves on the first valid bar ---------------------------
    df = df.dropna(subset=["vol_score", "leaps_score"]).copy()
    if df.empty:
        raise RuntimeError("No usable rows after computing features. Increase data window.")

    first_idx = df.index[0]
    first_spy = df.loc[first_idx, "SPY"]

    voo_shares = (starting_cap * voo_alloc) / first_spy
    leaps_cash = starting_cap * leaps_alloc

    lot: Optional[LeapsLot] = None
    open_trade: Optional[dict] = None  # accumulator for current cycle
    trades: list[TradeRecord] = []

    last_rebal_date: Optional[pd.Timestamp] = None
    last_exit_date: Optional[pd.Timestamp] = None

    # Determine rebalance day-of-week filter (default: every day)
    rebal_freq = o.get("rebalance_freq", "D").upper()

    daily_rows = []

    daily_cash_yield = (1.0 + rules["cash_yield"]) ** (1.0 / 252.0) - 1.0

    iterator = tqdm(df.iterrows(), total=len(df), desc="Simulating", ncols=80)
    for date, row in iterator:
        spy = float(row["SPY"])
        vix = float(row["VIX"])
        vix_slope = float(row["vix_slope"])
        score = float(row["leaps_score"])
        vol_score = float(row["vol_score"])

        # --- 1. Mark current lot to market -----------------------------------
        lot_value = 0.0
        sigma_today = implied_sigma_from_vix(vix, vix_slope, cfg)
        if lot is not None:
            T_remaining = max(
                (lot.expiry_date - date).days / 365.25, 1e-6
            )
            mark_price = bs_call_price(spy, lot.strike, T_remaining, r, sigma_today)
            lot_value = mark_price * o["contract_multiplier"] * lot.contracts

            # --- 2. Force-roll near expiry -----------------------------------
            if T_remaining <= o["roll_remaining_years"]:
                proceeds = lot_value
                leaps_cash += proceeds
                trades.append(TradeRecord(
                    entry_date=lot.entry_date,
                    exit_date=date,
                    entry_underlying=lot.entry_underlying,
                    exit_underlying=spy,
                    strike=lot.strike,
                    contracts=lot.contracts,
                    entry_premium=lot.entry_premium,
                    exit_premium=mark_price * o["contract_multiplier"],
                    pnl=proceeds - lot.contracts * lot.entry_premium,
                    pct_return=(proceeds / (lot.contracts * lot.entry_premium) - 1.0)
                               if lot.contracts > 0 and lot.entry_premium > 0 else 0.0,
                    reason="roll",
                    vol_score_at_entry=open_trade.get("vol_score", float("nan"))
                                       if open_trade else float("nan"),
                    leaps_score_at_entry=open_trade.get("leaps_score", float("nan"))
                                          if open_trade else float("nan"),
                ))
                lot = None
                open_trade = None
                lot_value = 0.0
                last_exit_date = date

        # --- 3. Accrue cash yield on un-deployed sleeve ----------------------
        leaps_cash *= (1.0 + daily_cash_yield)

        # --- 4. Decide whether to rebalance today ----------------------------
        do_rebal = True
        if rebal_freq == "W":
            # Once per ISO week
            if last_rebal_date is not None and date.isocalendar().week == last_rebal_date.isocalendar().week \
                    and date.year == last_rebal_date.year:
                do_rebal = False
        elif rebal_freq == "M":
            if last_rebal_date is not None and date.month == last_rebal_date.month \
                    and date.year == last_rebal_date.year:
                do_rebal = False

        if do_rebal:
            target_frac = target_exposure(score, cfg)
            sleeve_value = leaps_cash + lot_value
            target_dollars = sleeve_value * target_frac

            # Cooldown: if we just exited fully, don't re-enter immediately.
            in_cooldown = (
                last_exit_date is not None
                and lot is None
                and (date - last_exit_date).days < o["reentry_cooldown_days"]
            )

            # ---- A) Close out fully if target is zero -----------------------
            if target_frac == 0.0 and lot is not None:
                T_rem = max((lot.expiry_date - date).days / 365.25, 1e-6)
                mark = bs_call_price(spy, lot.strike, T_rem, r, sigma_today)
                proceeds = mark * o["contract_multiplier"] * lot.contracts
                leaps_cash += proceeds
                trades.append(TradeRecord(
                    entry_date=lot.entry_date,
                    exit_date=date,
                    entry_underlying=lot.entry_underlying,
                    exit_underlying=spy,
                    strike=lot.strike,
                    contracts=lot.contracts,
                    entry_premium=lot.entry_premium,
                    exit_premium=mark * o["contract_multiplier"],
                    pnl=proceeds - lot.contracts * lot.entry_premium,
                    pct_return=(proceeds / (lot.contracts * lot.entry_premium) - 1.0)
                               if lot.contracts > 0 and lot.entry_premium > 0 else 0.0,
                    reason="signal_exit",
                    vol_score_at_entry=open_trade.get("vol_score", float("nan"))
                                       if open_trade else float("nan"),
                    leaps_score_at_entry=open_trade.get("leaps_score", float("nan"))
                                          if open_trade else float("nan"),
                ))
                lot = None
                open_trade = None
                lot_value = 0.0
                last_exit_date = date

            # ---- B) Open a new lot (no existing lot) ------------------------
            elif target_frac > 0.0 and lot is None and not in_cooldown:
                strike = round(spy)            # ATM (rounded to nearest integer)
                T0 = o["expiry_years"]
                premium_per_share = bs_call_price(spy, strike, T0, r, sigma_today)
                premium_per_contract = premium_per_share * o["contract_multiplier"]
                if premium_per_contract > 0 and target_dollars > 0:
                    contracts = target_dollars / premium_per_contract
                    cost = contracts * premium_per_contract
                    leaps_cash -= cost
                    lot = LeapsLot(
                        entry_date=date,
                        expiry_date=date + pd.Timedelta(days=int(T0 * 365.25)),
                        strike=strike,
                        contracts=contracts,
                        entry_premium=premium_per_contract,
                        entry_underlying=spy,
                        entry_sigma=sigma_today,
                    )
                    open_trade = {"vol_score": vol_score, "leaps_score": score}
                    lot_value = cost

            # ---- C) Resize an existing lot ----------------------------------
            elif lot is not None and target_frac > 0.0:
                T_rem = max((lot.expiry_date - date).days / 365.25, 1e-6)
                mark_per_share = bs_call_price(spy, lot.strike, T_rem, r, sigma_today)
                mark_per_contract = mark_per_share * o["contract_multiplier"]
                current_dollars = lot.contracts * mark_per_contract
                drift = abs(current_dollars - target_dollars) / max(sleeve_value, 1.0)

                # Only act if meaningful drift (>5% of sleeve) and min hold met
                held_days = (date - lot.entry_date).days
                if drift > 0.05 and held_days >= o["min_holding_days"] and mark_per_contract > 0:
                    delta_dollars = target_dollars - current_dollars
                    delta_contracts = delta_dollars / mark_per_contract
                    # Buy more (delta>0) or sell some (delta<0) at current mark
                    new_contracts = lot.contracts + delta_contracts
                    if new_contracts <= 0:
                        # Effectively a full close
                        proceeds = lot.contracts * mark_per_contract
                        leaps_cash += proceeds
                        trades.append(TradeRecord(
                            entry_date=lot.entry_date,
                            exit_date=date,
                            entry_underlying=lot.entry_underlying,
                            exit_underlying=spy,
                            strike=lot.strike,
                            contracts=lot.contracts,
                            entry_premium=lot.entry_premium,
                            exit_premium=mark_per_contract,
                            pnl=proceeds - lot.contracts * lot.entry_premium,
                            pct_return=(proceeds / (lot.contracts * lot.entry_premium) - 1.0)
                                       if lot.entry_premium > 0 else 0.0,
                            reason="resize_to_zero",
                            vol_score_at_entry=open_trade.get("vol_score", float("nan"))
                                               if open_trade else float("nan"),
                            leaps_score_at_entry=open_trade.get("leaps_score", float("nan"))
                                                  if open_trade else float("nan"),
                        ))
                        lot = None
                        open_trade = None
                        last_exit_date = date
                        lot_value = 0.0
                    else:
                        # Update entry_premium as cost-weighted average so PnL stays consistent
                        if delta_contracts > 0:
                            new_cost_basis = (
                                lot.contracts * lot.entry_premium
                                + delta_contracts * mark_per_contract
                            ) / new_contracts
                            lot.entry_premium = new_cost_basis
                        # cash flow
                        leaps_cash -= delta_contracts * mark_per_contract
                        lot.contracts = new_contracts
                        lot_value = lot.contracts * mark_per_contract

            last_rebal_date = date

        # --- 5. VOO sleeve mark-to-market ------------------------------------
        voo_value = voo_shares * spy

        # --- 6. Record state -------------------------------------------------
        sleeve_value = leaps_cash + lot_value
        total_value = voo_value + sleeve_value

        daily_rows.append({
            "Date": date,
            "SPY": spy,
            "VIX": vix,
            "vol_score": vol_score,
            "leaps_score": score,
            "voo_value": voo_value,
            "leaps_cash": leaps_cash,
            "leaps_option_value": lot_value,
            "leaps_sleeve_value": sleeve_value,
            "total_value": total_value,
            "target_exposure": target_exposure(score, cfg),
            "in_position": lot is not None,
        })

    # --- Final mark / close-out for clean trade record -----------------------
    if lot is not None:
        last_date = df.index[-1]
        spy_last = float(df.loc[last_date, "SPY"])
        sigma_last = implied_sigma_from_vix(
            float(df.loc[last_date, "VIX"]),
            float(df.loc[last_date, "vix_slope"]),
            cfg,
        )
        T_rem = max((lot.expiry_date - last_date).days / 365.25, 1e-6)
        mark = bs_call_price(spy_last, lot.strike, T_rem, r, sigma_last)
        trades.append(TradeRecord(
            entry_date=lot.entry_date,
            exit_date=last_date,
            entry_underlying=lot.entry_underlying,
            exit_underlying=spy_last,
            strike=lot.strike,
            contracts=lot.contracts,
            entry_premium=lot.entry_premium,
            exit_premium=mark * 100,
            pnl=mark * 100 * lot.contracts - lot.contracts * lot.entry_premium,
            pct_return=(mark * 100 / lot.entry_premium - 1.0) if lot.entry_premium > 0 else 0.0,
            reason="end_of_data_open",
            vol_score_at_entry=open_trade.get("vol_score", float("nan"))
                               if open_trade else float("nan"),
            leaps_score_at_entry=open_trade.get("leaps_score", float("nan"))
                                  if open_trade else float("nan"),
        ))

    equity = pd.DataFrame(daily_rows).set_index("Date")
    trades_df = pd.DataFrame([t.__dict__ for t in trades])

    return {"equity": equity, "trades": trades_df}


# =============================================================================
# Performance metrics
# =============================================================================

def perf_metrics(equity_curve: pd.Series, freq: int = 252) -> dict:
    """
    CAGR, max drawdown, Sharpe (rf=0 approximation), volatility.
    """
    if len(equity_curve) < 2:
        return {}
    rets = equity_curve.pct_change().dropna()
    years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
    cagr = (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1.0 / max(years, 1e-9)) - 1.0
    vol = rets.std() * math.sqrt(freq)
    sharpe = (rets.mean() * freq) / (rets.std() * math.sqrt(freq)) if rets.std() > 0 else float("nan")
    peak = equity_curve.cummax()
    drawdown = equity_curve / peak - 1.0
    max_dd = drawdown.min()
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1.0
    return {
        "start": str(equity_curve.index[0].date()),
        "end": str(equity_curve.index[-1].date()),
        "years": round(years, 2),
        "total_return": round(float(total_return), 4),
        "cagr": round(float(cagr), 4),
        "annual_vol": round(float(vol), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(float(max_dd), 4),
        "final_value": round(float(equity_curve.iloc[-1]), 2),
    }


def regime_diagnostics(equity: pd.DataFrame) -> dict:
    """
    Best/worst regimes by quartile of vol_score, plus average daily LEAPS sleeve return per bucket.
    """
    eq = equity.copy()
    eq["leaps_ret"] = eq["leaps_sleeve_value"].pct_change()
    eq["regime_q"] = pd.qcut(eq["vol_score"].fillna(eq["vol_score"].median()),
                              q=4, labels=["Q1_low", "Q2", "Q3", "Q4_high"])
    grp = eq.groupby("regime_q", observed=True)["leaps_ret"].agg(["mean", "std", "count"])
    grp["annualized"] = grp["mean"] * 252
    return {
        "by_volscore_quartile": grp.round(6).to_dict(orient="index"),
        "best_regime": grp["annualized"].idxmax() if not grp["annualized"].empty else None,
        "worst_regime": grp["annualized"].idxmin() if not grp["annualized"].empty else None,
    }


# =============================================================================
# Plotting
# =============================================================================

def make_plots(equity: pd.DataFrame, cfg: dict, outdir: Path) -> None:
    """Persist diagnostic plots to disk (PNG)."""
    dpi = cfg["output"]["plot_dpi"]
    show = cfg["output"]["show_plots"]

    # Equity curves
    voo_curve = equity["voo_value"] / equity["voo_value"].iloc[0] * cfg["portfolio"]["starting_capital"]
    leaps_curve = equity["leaps_sleeve_value"] / equity["leaps_sleeve_value"].iloc[0] \
                   * cfg["portfolio"]["starting_capital"]
    combo_curve = equity["total_value"]

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(voo_curve.index, voo_curve, label="VOO only (rescaled to $100k)", lw=1.4)
    ax.plot(leaps_curve.index, leaps_curve, label="LEAPS sleeve only (rescaled to $100k)",
            lw=1.2, alpha=0.85)
    ax.plot(combo_curve.index, combo_curve, label="Combined Portfolio (80/20)", lw=1.8, color="black")
    ax.set_title("Equity Curves: VOO vs LEAPS sleeve vs Combined")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "equity_curves.png", dpi=dpi)
    if show: plt.show()
    plt.close(fig)

    # VIX vs allocation
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(equity.index, equity["VIX"], color="tab:red", lw=1.0, label="VIX")
    ax1.set_ylabel("VIX", color="tab:red")
    ax1.tick_params(axis="y", labelcolor="tab:red")
    ax2 = ax1.twinx()
    ax2.fill_between(equity.index, 0, equity["target_exposure"] * 100,
                     color="tab:blue", alpha=0.25, label="LEAPS target %")
    ax2.set_ylabel("LEAPS Sleeve Deployed (%)", color="tab:blue")
    ax2.tick_params(axis="y", labelcolor="tab:blue")
    ax2.set_ylim(0, 110)
    ax1.set_title("VIX vs Tactical LEAPS Allocation")
    fig.tight_layout()
    fig.savefig(outdir / "vix_vs_allocation.png", dpi=dpi)
    if show: plt.show()
    plt.close(fig)

    # Vol score
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(equity.index, equity["vol_score"], lw=1.0, color="tab:purple", label="VolScore (0-100)")
    ax.plot(equity.index, equity["leaps_score"] * 10, lw=0.9, color="tab:orange", alpha=0.75,
            label="LEAPS score x10")
    ax.set_title("Volatility / LEAPS Conviction Scores")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "vol_score.png", dpi=dpi)
    if show: plt.show()
    plt.close(fig)

    # Drawdown
    eq = equity["total_value"]
    peak = eq.cummax()
    dd = eq / peak - 1.0
    voo_eq = equity["voo_value"]
    voo_peak = voo_eq.cummax()
    voo_dd = voo_eq / voo_peak - 1.0
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.fill_between(dd.index, dd * 100, 0, color="tab:blue", alpha=0.4, label="Combined")
    ax.plot(voo_dd.index, voo_dd * 100, color="tab:gray", lw=1.0, label="VOO only")
    ax.set_title("Drawdown (%)")
    ax.set_ylabel("%")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "drawdown.png", dpi=dpi)
    if show: plt.show()
    plt.close(fig)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Hybrid VOO + LEAPS backtester")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    args = parser.parse_args()

    cfg = load_config(args.config)
    outdir = Path(cfg["output"]["results_dir"])
    outdir.mkdir(parents=True, exist_ok=True)

    print("Loading data ...")
    raw = load_data(cfg)
    print(f"  {len(raw):,} rows from {raw.index[0].date()} to {raw.index[-1].date()}")

    print("Computing volatility features and LEAPS scores ...")
    feat = compute_vol_features(raw, cfg)
    feat = compute_leaps_score(feat, cfg)

    print("Running simulation ...")
    res = simulate(feat, cfg)
    equity = res["equity"]
    trades = res["trades"]

    # ---- Build comparable benchmark series ---------------------------------
    benchmark = (raw["SPY"] / raw["SPY"].iloc[0]) * cfg["portfolio"]["starting_capital"]
    benchmark = benchmark.reindex(equity.index).ffill()

    metrics = {
        "VOO_only_full_capital": perf_metrics(benchmark),
        "VOO_sleeve_80pct": perf_metrics(equity["voo_value"]),
        "LEAPS_sleeve_only": perf_metrics(equity["leaps_sleeve_value"]),
        "Combined_Portfolio": perf_metrics(equity["total_value"]),
    }

    diagnostics = {
        "num_leaps_trades": int(len(trades)),
        "avg_pct_return_per_cycle": float(trades["pct_return"].mean()) if len(trades) else 0.0,
        "median_pct_return_per_cycle": float(trades["pct_return"].median()) if len(trades) else 0.0,
        "best_trade_pct": float(trades["pct_return"].max()) if len(trades) else 0.0,
        "worst_trade_pct": float(trades["pct_return"].min()) if len(trades) else 0.0,
        "avg_holding_days": float(
            (pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days.mean()
        ) if len(trades) else 0.0,
        "regimes": regime_diagnostics(equity),
    }

    # ---- Persist outputs ----------------------------------------------------
    if cfg["output"]["save_csv"]:
        equity.to_csv(outdir / "daily_state.csv")
        # Wider equity-curves CSV for spreadsheets
        ec = pd.DataFrame({
            "VOO_only_$100k": benchmark,
            "VOO_sleeve_80pct": equity["voo_value"],
            "LEAPS_sleeve": equity["leaps_sleeve_value"],
            "Combined": equity["total_value"],
        })
        ec.to_csv(outdir / "equity_curves.csv")
        if not trades.empty:
            trades.to_csv(outdir / "leaps_trades.csv", index=False)

    with open(outdir / "metrics.json", "w") as f:
        json.dump({"metrics": metrics, "diagnostics": diagnostics}, f, indent=2, default=str)

    if cfg["output"]["save_plots"]:
        # Add benchmark to equity df for plotting
        equity_for_plot = equity.copy()
        equity_for_plot["voo_full_capital_benchmark"] = benchmark
        make_plots(equity_for_plot, cfg, outdir)

    # ---- Console summary ----------------------------------------------------
    print("\n=== PERFORMANCE METRICS ===")
    for k, v in metrics.items():
        print(f"\n[{k}]")
        for kk, vv in v.items():
            print(f"  {kk:>15}: {vv}")
    print("\n=== STRATEGY DIAGNOSTICS ===")
    print(f"  num_leaps_trades        : {diagnostics['num_leaps_trades']}")
    print(f"  avg_pct_return_per_cycle: {diagnostics['avg_pct_return_per_cycle']:+.2%}")
    print(f"  best_trade_pct          : {diagnostics['best_trade_pct']:+.2%}")
    print(f"  worst_trade_pct         : {diagnostics['worst_trade_pct']:+.2%}")
    print(f"  avg_holding_days        : {diagnostics['avg_holding_days']:.1f}")
    print(f"\n  Best regime (by VolScore quartile) : {diagnostics['regimes']['best_regime']}")
    print(f"  Worst regime (by VolScore quartile): {diagnostics['regimes']['worst_regime']}")
    print(f"\nResults written to: {outdir.resolve()}")


if __name__ == "__main__":
    main()
