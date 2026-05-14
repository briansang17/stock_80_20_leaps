# SPY LEAPS Daily Signal — Setup & Usage

This is your **daily monitoring system**. After the one-time setup, your Mac will automatically check the market every weekday at 4:15pm and send you a notification ONLY when a buy signal fires.

---

## What's In This Project Now

```
stock_80_20_leaps/
├── strategy_backtest.py        # The reusable backtest (3 frequency profiles)
├── daily_signal.py             # Daily checker — pulls live data, runs signals
├── notify.py                   # macOS / email / Pushover notification helpers
├── com.briansang.leaps-signal.plist  # macOS scheduler file
├── data_cache/term_structure.csv     # 10-year historical data for backtests
└── results/                    # Logs, equity curves, trade history
```

---

## Choosing a Profile

| Profile | Trades/yr | 10-yr CAGR | Max DD | Win Rate | Best For |
|---|---|---|---|---|---|
| **STRICT** | 0.8/yr | +27.9% | -33% | 75% | Set-and-forget, fewest decisions |
| **BALANCED** | 1.3/yr | +14.3% | -62% | 69% | Middle ground (some whipsaw) |
| **AGGRESSIVE** | 2.3/yr | **+19.1%** | -42% | 61% | More action, more frequent buys ⭐ |
| SPY buy-hold | — | +15.4% | -34% | — | (baseline) |

**Recommendation:** Use **AGGRESSIVE** if you want frequent buys — it still beats SPY and gives you 2-3 entries per year.

To run a fresh backtest at any time:
```bash
python strategy_backtest.py --profile AGGRESSIVE
```

---

## One-Time Setup (5 Minutes)

### Step 1: Test the daily script manually

```bash
cd /Users/briansang/Desktop/stock_80_20_leaps
source .venv/bin/activate
python daily_signal.py --profile AGGRESSIVE --force
```

You should see the full signal report and a macOS notification pop up.

### Step 2: Install the scheduled job

```bash
# Copy the plist into LaunchAgents
cp ~/Desktop/stock_80_20_leaps/com.briansang.leaps-signal.plist ~/Library/LaunchAgents/

# Load it (this tells macOS to start running it)
launchctl load ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
```

### Step 3: Verify it's installed

```bash
launchctl list | grep leaps-signal
```

You should see one line containing `com.briansang.leaps-signal`. The job will now run every weekday at 4:15pm.

---

## (Optional) Email Notifications

Native macOS notifications work out of the box. If you want email **too** (e.g., to read on your phone when away from the Mac):

1. Generate a Gmail **App Password** at https://myaccount.google.com/apppasswords
2. Create the config file:

```bash
cat > ~/.leaps_signal_config.json <<'EOF'
{
  "smtp_user": "your.email@gmail.com",
  "smtp_pass": "your-16-char-app-password",
  "smtp_to":   "your.email@gmail.com"
}
EOF
chmod 600 ~/.leaps_signal_config.json
```

3. Test:
```bash
python notify.py
```

You should get both a macOS notification AND an email.

---

## (Optional) Phone Push Notifications via Pushover

If you want push notifications on your iPhone even when your Mac is asleep:

1. Sign up at https://pushover.net (one-time $5 for iOS app)
2. Get your User Key + create an Application Token
3. Add to `~/.leaps_signal_config.json`:

```json
{
  "smtp_user": "...",
  "pushover_user":  "YOUR_USER_KEY",
  "pushover_token": "YOUR_APP_TOKEN"
}
```

---

## Daily Usage

Once set up, you literally do nothing. The system will:

- Run automatically at 4:15pm ET Mon-Fri
- Stay silent on ~95% of days (no signal)
- **Send a notification ONLY when the BUY signal fires** with details like:
  ```
  🟢 SPY LEAPS BUY SIGNAL
  VIX 17.9 • RSI 59 • 2/3 signals
  SPY $676 (+2.4% vs 200DMA) • Profile: AGGRESSIVE
  BUY a 2-year ATM SPY call. Confirm on TradingView first.
  ```

When the notification arrives:
1. Open TradingView → confirm signals visually
2. Place your option order (2-year ATM SPY call, limit at mid)
3. Set a 90-day calendar reminder to review

---

## Manual Commands (Anytime)

```bash
# Check today right now (no notification, just print)
python daily_signal.py --profile AGGRESSIVE --quiet

# Test the notification (sends even with no signal)
python daily_signal.py --profile AGGRESSIVE --force

# Try a different profile
python daily_signal.py --profile STRICT

# View today's log
tail -1 results/daily_signal_log.csv

# View past 7 days
tail -7 results/daily_signal_log.csv

# See all trades for current profile
cat results/trades_aggressive.csv

# Re-run a full historical backtest
python strategy_backtest.py --profile AGGRESSIVE
```

---

## Disable / Re-enable Scheduling

```bash
# Pause daily checks
launchctl unload ~/Library/LaunchAgents/com.briansang.leaps-signal.plist

# Resume
launchctl load ~/Library/LaunchAgents/com.briansang.leaps-signal.plist

# Remove entirely
launchctl unload ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
rm ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
```

---

## Troubleshooting

**Notification didn't fire when I expected one**
- Check `results/launchd_stderr.log` for errors
- Run `python daily_signal.py --profile AGGRESSIVE --force` manually to see what's happening

**"Failed to download from Yahoo"**
- Yahoo sometimes rate-limits. The script will fail; just rerun a minute later.

**Mac was asleep at 4:15pm**
- launchd will run the job as soon as the Mac wakes (within a few minutes)
- For 100% reliability, consider running it on a Raspberry Pi or always-on server

**RSI/MACD values differ from TradingView**
- The script uses true 14-period and 12/26/9 EMAs — should match TradingView closely
- Small differences (1-2 RSI points, MACD within 0.1) are normal due to data feed differences

---

## Honest Caveats (Read This Before Real Trading)

This was reviewed by an independent auditor who flagged these concerns:

1. **8 trades over 10 years is a tiny statistical sample** — the +27.9% CAGR may be partly luck or overfitting
2. **Past performance ≠ future returns** — this strategy may underperform the next 10 years
3. **Taxes & commissions are NOT modeled** — figure -5 to -10% off pre-tax CAGR for taxes
4. **Real losses can be bigger than -1%** — the historical pattern of near-flat losses won't always hold
5. **Don't bet the farm** — keep 80% in passive VOO/SPY. Only use 20% for this tactical sleeve.

The system reduces decision fatigue and removes emotion. But it does NOT eliminate risk.

---

## Quick Reference Card

```
┌──────────────────────────────────────────────────────────┐
│      DAILY OPERATION ONCE INSTALLED                      │
├──────────────────────────────────────────────────────────┤
│  • Mac runs the check at 4:15pm ET Mon-Fri               │
│  • You get a notification ONLY on buy signal days        │
│  • All other days: silence (that's correct behavior)     │
│                                                          │
│  When notified:                                          │
│    1. Open TradingView, confirm MACD/RSI/MAs             │
│    2. Buy 1× 2-year ATM SPY call (limit @ mid price)     │
│    3. Set 90-day calendar reminder                       │
│    4. Forget about it until reminder or sell signal      │
│                                                          │
│  Hold ≥ 90 days (AGGRESSIVE) before selling              │
│  Sell when SPY drops 3% below 50DMA, VIX > 30, or         │
│  VIX jumps +6 in 5 days, or <4 months to expiry          │
└──────────────────────────────────────────────────────────┘
```
