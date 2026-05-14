"""
Daily SPY LEAPS Signal Check
============================

Run this once daily after market close. It will:
  1. Download fresh SPY + VIX data (last ~1 year)
  2. Compute all signals (Gates + 3 momentum triggers)
  3. Print a status report to stdout
  4. Send a notification ONLY if the BUY signal fires
  5. Append today's status to a CSV log

Usage:
    python daily_signal.py                       # uses BALANCED profile
    python daily_signal.py --profile STRICT
    python daily_signal.py --profile AGGRESSIVE
    python daily_signal.py --force               # always notify (test mode)
    python daily_signal.py --quiet               # no notification, just log
"""

from __future__ import annotations
import argparse, sys, math, json
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌ Missing yfinance. Run: pip install yfinance")

from notify import notify_all
from strategy_backtest import PROFILES, add_features, signals_in_window

PROJECT_DIR = Path(__file__).resolve().parent
LOG_PATH    = PROJECT_DIR / "results" / "daily_signal_log.csv"
LAST_NOTIFIED_PATH = PROJECT_DIR / ".last_notified.json"

def fetch_data(period_days: int = 380) -> pd.DataFrame:
    """Download SPY and VIX from Yahoo for the last ~year."""
    print(f"  📡 Fetching latest SPY + VIX from Yahoo Finance...")
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=period_days)

    try:
        spy = yf.download("SPY",  start=start, end=end, progress=False, auto_adjust=False)
        vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)
    except Exception as e:
        sys.exit(f"❌ Yahoo Finance download failed: {e}")

    if spy.empty or vix.empty:
        sys.exit("❌ Yahoo returned empty data — try again in a few minutes")

    # Handle multi-index columns from newer yfinance versions
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = vix.columns.get_level_values(0)

    df = pd.DataFrame({
        "SPY": spy["Close"].astype(float),
        "VIX": vix["Close"].astype(float),
    }).dropna()
    df.index = pd.to_datetime(df.index)
    print(f"     Got {len(df)} trading days  (last close: {df.index[-1].date()})")
    return df

def check_signals(df: pd.DataFrame, profile: str) -> dict:
    """Compute every signal for the most recent trading day."""
    cfg   = PROFILES[profile]
    feats = add_features(df)
    sigs  = signals_in_window(feats, cfg["cross_window"])
    today = feats.index[-1]
    row   = feats.loc[today]
    sig   = sigs.loc[today]

    state = {
        "date":         str(today.date()),
        "profile":      profile,
        "spy":          float(row["SPY"]),
        "sma50":        float(row["sma50"]),
        "sma200":       float(row["sma200"]),
        "vix":          float(row["VIX"]),
        "vix_slope5":   float(row["vix_slope5"]),
        "rsi14":        float(row["RSI14"]),
        "macd":         float(row["macd"]),
        "macd_sig":     float(row["macd_sig"]),
        "drawdown":     float(row["drawdown"]),

        "gate1_above_200dma": bool(row["spy_above_200"]),
        "gate2_vix_ok":       bool(row["VIX"] < cfg["vix_max_entry"]),
        "filter_rsi_ok":      bool(row["RSI14"] < cfg["rsi_max_entry"]),

        "sig_macd":   bool(sig["sig_macd"]),
        "sig_rsi":    bool(sig["sig_rsi"]),
        "sig_50dma":  bool(sig["sig_50dma"]),
        "score":      int(sig["score"]),

        "cross_window_days": cfg["cross_window"],
        "vix_max_entry":     cfg["vix_max_entry"],
    }
    state["buy_signal"] = (
        state["score"] >= 2 and
        state["gate1_above_200dma"] and
        state["gate2_vix_ok"] and
        state["filter_rsi_ok"]
    )
    return state

def format_report(s: dict) -> str:
    def yn(b): return "✅" if b else "❌"
    spy_vs_50  = (s["spy"] / s["sma50"]  - 1) * 100
    spy_vs_200 = (s["spy"] / s["sma200"] - 1) * 100

    return f"""
════════════════════════════════════════════════════════════════════════
  SPY LEAPS DAILY SIGNAL CHECK  •  {s['date']}  •  Profile: {s['profile']}
════════════════════════════════════════════════════════════════════════

  PRICES & STATE
  ──────────────
  SPY                : ${s['spy']:.2f}
  50-day MA          : ${s['sma50']:.2f}   ({spy_vs_50:+.1f}% vs SPY)
  200-day MA         : ${s['sma200']:.2f}  ({spy_vs_200:+.1f}% vs SPY)
  VIX                : {s['vix']:.2f}
  VIX 5-day change   : {s['vix_slope5']:+.2f}
  RSI 14             : {s['rsi14']:.1f}
  MACD line          : {s['macd']:+.3f}
  MACD signal line   : {s['macd_sig']:+.3f}
  SPY drawdown       : {s['drawdown']:.1f}%

  GATES (both must be ✅)
  ─────────────────────
  Gate 1 — SPY above 200-DMA      : {yn(s['gate1_above_200dma'])}
  Gate 2 — VIX below {s['vix_max_entry']}            : {yn(s['gate2_vix_ok'])}
  Filter — RSI below 65            : {yn(s['filter_rsi_ok'])}

  SIGNALS (need 2 of 3, fired in last {s['cross_window_days']} day(s))
  ────────────────────────────────────────────────────
  Signal 1 — MACD cross up         : {yn(s['sig_macd'])}
  Signal 2 — RSI crossed > 50      : {yn(s['sig_rsi'])}
  Signal 3 — SPY reclaimed 50-DMA  : {yn(s['sig_50dma'])}
  TOTAL SCORE                      : {s['score']}/3

  ════════════════════════════════
  🚦 {"🟢 BUY SIGNAL — confirm on TradingView & buy 2-yr ATM SPY call" if s['buy_signal'] else "🔴 NO SIGNAL — do nothing"}
  ════════════════════════════════
"""

def append_log(state: dict):
    LOG_PATH.parent.mkdir(exist_ok=True)
    df = pd.DataFrame([state])
    if LOG_PATH.exists():
        df.to_csv(LOG_PATH, mode="a", header=False, index=False)
    else:
        df.to_csv(LOG_PATH, index=False)

def should_notify_again(state: dict) -> bool:
    """Don't spam: only notify if we haven't already notified for this date."""
    if not LAST_NOTIFIED_PATH.exists():
        return True
    try:
        last = json.loads(LAST_NOTIFIED_PATH.read_text())
        if last.get("date") == state["date"] and last.get("profile") == state["profile"]:
            return False
    except Exception:
        pass
    return True

def remember_notification(state: dict):
    LAST_NOTIFIED_PATH.write_text(json.dumps({
        "date": state["date"], "profile": state["profile"],
        "sent_at": datetime.now().isoformat(),
    }))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="BALANCED")
    parser.add_argument("--force",  action="store_true", help="Send notification even if no signal")
    parser.add_argument("--quiet",  action="store_true", help="Don't send notification, just log")
    args = parser.parse_args()

    df    = fetch_data()
    state = check_signals(df, args.profile)

    report = format_report(state)
    print(report)
    append_log(state)

    should_send = (state["buy_signal"] or args.force) and not args.quiet
    if should_send and (args.force or should_notify_again(state)):
        spy_vs_200 = (state['spy']/state['sma200'] - 1) * 100
        title    = "🟢 SPY LEAPS BUY SIGNAL" if state["buy_signal"] else "ℹ️ SPY LEAPS — Daily Check"
        subtitle = f"VIX {state['vix']:.1f} • RSI {state['rsi14']:.0f} • {state['score']}/3 signals"
        message  = (
            f"SPY ${state['spy']:.0f}  ({spy_vs_200:+.0f}% vs 200DMA)  •  "
            f"Profile: {state['profile']}\n"
            f"MACD:{'✓' if state['sig_macd'] else '×'}  "
            f"RSI:{'✓' if state['sig_rsi'] else '×'}  "
            f"50DMA:{'✓' if state['sig_50dma'] else '×'}\n"
            f"{'BUY a 2-year ATM SPY call. Confirm on TradingView first.' if state['buy_signal'] else 'No signal today.'}"
        )
        notify_all(title=title, message=message, subtitle=subtitle, priority=1 if state["buy_signal"] else 0)
        if state["buy_signal"]:
            remember_notification(state)

    elif state["buy_signal"] and not args.force:
        print("  ℹ️  Buy signal active but already notified for this date — skipping.")
    elif args.quiet:
        print("  🔇 Quiet mode — notification skipped.")
    else:
        print("  ℹ️  No buy signal — no notification sent.")

    print(f"  📝 Logged to {LOG_PATH}\n")

if __name__ == "__main__":
    main()
