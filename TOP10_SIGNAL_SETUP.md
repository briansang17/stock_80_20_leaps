# Top 10 Daily Signal Monitor — Setup Guide

Two ways to monitor all top strategies and get emailed when any fires. Pick one (or use both).

| Approach | Free | Always On | Strategies Covered | Setup Time |
|---|---|---|---|---|
| **🐍 Python + cron/launchd** | ✅ Free | ⚠️ Only when computer is on | **All 10** | 15 min |
| **📊 Google Sheets + Apps Script** | ✅ Free | ✅ Yes (Google servers) | **4 simple ones** | 20 min |

**Recommendation:** Use Python on your laptop AND Google Sheets as a 24/7 backup. They're independent and won't conflict.

---

## OPTION 1 — Python Script (recommended primary)

### Step 1. Set up email credentials

Create `~/.leaps_signal_config.json` with your Gmail details:

```json
{
  "smtp_user": "your.email@gmail.com",
  "smtp_pass": "your-16-char-app-password",
  "smtp_to":   "your.email@gmail.com",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465
}
```

To get your Gmail App Password:
1. Go to https://myaccount.google.com/apppasswords (must have 2FA enabled)
2. App name: "LEAPS Signal"
3. Copy the 16-character password (no spaces) into `smtp_pass`

### Step 2. Test it manually

```bash
cd /Users/briansang/Desktop/stock_80_20_leaps
source .venv/bin/activate
python daily_signal_top10.py --force        # test email even if no signal
```

You should receive an email titled like:
- `🟢 SPY LEAPS — 2 signals firing (D_BREAKOUT, M_QUAL_BREAKOUT)`
- or `⚪️ SPY LEAPS — no signals today (2026-05-13)`

### Step 3. Schedule it daily

Replace the existing launchd plist:

```bash
# Edit ~/Library/LaunchAgents/com.briansang.leaps-signal.plist:
#   Change the python file from daily_signal.py to daily_signal_top10.py

launchctl unload ~/Library/LaunchAgents/com.briansang.leaps-signal.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.briansang.leaps-signal.plist
```

It will run automatically every weekday at the time configured in the plist (typically 4:30pm ET after market close).

### What the email looks like

```
═══════════════════════════════════════════════════════════
  SPY LEAPS — TOP 10 STRATEGY SCANNER  •  2026-05-13
═══════════════════════════════════════════════════════════

  Market state:
    SPY $742.31    VIX 17.87    RSI 81.9
    50DMA $687.62  200DMA $674.98  BB-width %ile 79%

  🚦 2 of 10 strategies firing today

  ─────────────────────────────────────────────────────────
  🟢 SUGGESTED CONTRACT (+15% OTM 2-yr LEAPS):
     Strike     : $855
     Expiry     : Dec 2028
     Cost/cntrct: $7,649  (limit ≤ $76.48)
  ─────────────────────────────────────────────────────────

  🟢 D_BREAKOUT         ✅ FIRED
     SPY hit new 60-day high with VIX still low
       ✅ SPY at new 60-day high  (SPY $742 vs 60d high $742)
       ✅ SPY > 200DMA  (200DMA $675)
       ✅ VIX < 20  (VIX 17.9)

  🟢 M_QUAL_BREAKOUT    ✅ FIRED
     Quality breakout: 60d high + very low VIX + uptrend
       ✅ New 60-day high
       ✅ VIX < 18
       ✅ SPY > 50DMA  ✅ SPY > 200DMA

  ── Not firing today ──
  🔴 F_VIX_CRUSH        — 1 of 2 conditions miss
  🔴 C_CHEAP_IV         — 2 of 3 conditions miss
  ...
```

---

## OPTION 2 — Google Sheets + Apps Script (free 24/7)

**Covers 4 of the 10 strategies** (the simple ones):
- D_BREAKOUT (top-3 in both periods)
- F_VIX_CRUSH (top-1 in past 2 years)
- M_QUAL_BREAKOUT
- G_GOLDEN_CROSS

The other 6 require complex calculations (RSI, MACD, BB percentile, cross detection) that are painful in Sheets.

### Step 1. Create a new Google Sheet

Name it "SPY LEAPS Top 10 Monitor".

### Step 2. Tab "Prices" — pull historical data

In cell **A1** paste:
```
=GOOGLEFINANCE("SPY","close",TODAY()-400,TODAY(),"DAILY")
```

In cell **D1** paste:
```
=GOOGLEFINANCE("^VIX","close",TODAY()-400,TODAY(),"DAILY")
```

This creates two date-price tables side by side.

### Step 3. Tab "Dashboard" — build the logic

Paste these into the indicated cells:

| Cell | Formula |
|------|---------|
| **A1** | `Metric` |
| **B1** | `Value` |
| **A2** | `SPY today` |
| **B2** | `=INDEX(Prices!B:B, COUNTA(Prices!B:B))` |
| **A3** | `SPY yesterday` |
| **B3** | `=INDEX(Prices!B:B, COUNTA(Prices!B:B)-1)` |
| **A4** | `50-day MA` |
| **B4** | `=AVERAGE(OFFSET(INDEX(Prices!B:B, COUNTA(Prices!B:B)), -49, 0, 50, 1))` |
| **A5** | `200-day MA` |
| **B5** | `=AVERAGE(OFFSET(INDEX(Prices!B:B, COUNTA(Prices!B:B)), -199, 0, 200, 1))` |
| **A6** | `60-day high` |
| **B6** | `=MAX(OFFSET(INDEX(Prices!B:B, COUNTA(Prices!B:B)), -59, 0, 60, 1))` |
| **A7** | `VIX today` |
| **B7** | `=INDEX(Prices!E:E, COUNTA(Prices!E:E))` |
| **A8** | `VIX 10 days ago` |
| **B8** | `=INDEX(Prices!E:E, COUNTA(Prices!E:E)-10)` |
| **A9** | `VIX 10d max` |
| **B9** | `=MAX(OFFSET(INDEX(Prices!E:E, COUNTA(Prices!E:E)), -9, 0, 10, 1))` |
| **A10** | `200DMA yesterday` |
| **B10** | `=AVERAGE(OFFSET(INDEX(Prices!B:B, COUNTA(Prices!B:B))-1, -199, 0, 200, 1))` |

### Step 4. The 4 signal cells

| Cell | Formula |
|------|---------|
| **A12** | `🟢 D_BREAKOUT` |
| **B12** | `=IF(AND(B2>=B6, B2>B5, B7<20), "✅ BUY", "❌ wait")` |
| **A13** | `🟢 F_VIX_CRUSH` |
| **B13** | `=IF(AND(B7/B9<=0.70, B2>B5), "✅ BUY", "❌ wait")` |
| **A14** | `🟢 M_QUAL_BREAKOUT` |
| **B14** | `=IF(AND(B2>=B6, B7<18, B2>B4, B2>B5), "✅ BUY", "❌ wait")` |
| **A15** | `🟢 G_GOLDEN_CROSS` |
| **B15** | `=IF(AND(B2>B5, B3<=B10), "✅ BUY", "❌ wait")` |
| **A17** | `🚦 ANY STRATEGY FIRING?` |
| **B17** | `=IF(COUNTIF(B12:B15, "✅ BUY")>0, "🟢 YES — check details above!", "🔴 No signals today")` |
| **A18** | `Suggested strike (+15% OTM)` |
| **B18** | `=ROUND(B2*1.15/5,0)*5` |

### Step 5. Add the Apps Script to send email

In the menu: **Extensions → Apps Script**. Replace the default code with:

```javascript
function checkSignalsAndEmail() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const dash = ss.getSheetByName("Dashboard");
  if (!dash) return;

  // Read signal cells
  const sigs = {
    "D_BREAKOUT":      dash.getRange("B12").getValue(),
    "F_VIX_CRUSH":     dash.getRange("B13").getValue(),
    "M_QUAL_BREAKOUT": dash.getRange("B14").getValue(),
    "G_GOLDEN_CROSS":  dash.getRange("B15").getValue(),
  };
  const fired = Object.keys(sigs).filter(k => sigs[k] === "✅ BUY");
  if (fired.length === 0) {
    Logger.log("No signals firing");
    return;
  }

  // Don't re-email same day
  const today = Utilities.formatDate(new Date(), "America/New_York", "yyyy-MM-dd");
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty("last_sent") === today) {
    Logger.log("Already sent today");
    return;
  }

  // Read market state
  const spy = dash.getRange("B2").getValue();
  const vix = dash.getRange("B7").getValue();
  const strike = dash.getRange("B18").getValue();
  const sma50 = dash.getRange("B4").getValue();
  const sma200 = dash.getRange("B5").getValue();
  const high60 = dash.getRange("B6").getValue();
  const vixCrush = vix / dash.getRange("B9").getValue();

  // Build email
  const subject = `🟢 SPY LEAPS — ${fired.length} signal${fired.length>1?'s':''} firing (${fired.join(", ")})`;
  let body = `SPY LEAPS TOP STRATEGY SIGNALS — ${today}\n\n`;
  body += `Market state:\n`;
  body += `  SPY $${spy.toFixed(2)}   VIX ${vix.toFixed(2)}\n`;
  body += `  50DMA $${sma50.toFixed(2)}   200DMA $${sma200.toFixed(2)}   60d high $${high60.toFixed(2)}\n\n`;
  body += `Suggested contract: SPY $${strike} 2-yr LEAPS (+15% OTM)\n\n`;
  body += `Firing today:\n`;

  if (sigs["D_BREAKOUT"] === "✅ BUY") {
    body += `\n✅ D_BREAKOUT — SPY broke to new 60-day high\n`;
    body += `   • SPY $${spy.toFixed(2)} ≥ 60d high $${high60.toFixed(2)}\n`;
    body += `   • SPY > 200DMA ($${sma200.toFixed(2)})  • VIX ${vix.toFixed(1)} < 20\n`;
  }
  if (sigs["F_VIX_CRUSH"] === "✅ BUY") {
    body += `\n✅ F_VIX_CRUSH — VIX collapsed 30%+ in 10 days\n`;
    body += `   • VIX ${vix.toFixed(1)}, down ${((1-vixCrush)*100).toFixed(0)}% from 10d max\n`;
    body += `   • SPY > 200DMA — uptrend intact\n`;
  }
  if (sigs["M_QUAL_BREAKOUT"] === "✅ BUY") {
    body += `\n✅ M_QUAL_BREAKOUT — Quality breakout setup\n`;
    body += `   • SPY at new 60-day high with VIX ${vix.toFixed(1)} < 18\n`;
    body += `   • Clean uptrend (SPY > 50DMA > 200DMA)\n`;
  }
  if (sigs["G_GOLDEN_CROSS"] === "✅ BUY") {
    body += `\n✅ G_GOLDEN_CROSS — SPY crossed above 200DMA\n`;
    body += `   • Yesterday: SPY at or below 200DMA  • Today: above\n`;
  }

  body += `\n\nAction:\n`;
  body += `  1. Verify on TradingView or your broker\n`;
  body += `  2. Sell ~${Math.ceil(8000/spy*1.18)} shares VOO (~$8,000)\n`;
  body += `  3. Buy 1 SPY $${strike} Dec-2028 call at limit\n`;
  body += `  4. Set reminder for 8-14 month exit window\n`;

  // Send
  const me = Session.getActiveUser().getEmail();
  MailApp.sendEmail({
    to: me,
    subject: subject,
    body: body,
  });
  props.setProperty("last_sent", today);
  Logger.log(`Email sent to ${me}`);
}
```

### Step 6. Schedule the daily check

In Apps Script: click the **clock icon** (Triggers) → **+ Add Trigger**:
- Function: `checkSignalsAndEmail`
- Event source: `Time-driven`
- Type: `Day timer`
- Time: `5pm to 6pm` (after market close)

That's it. Google will run this every day for free, forever, even when your computer is off.

---

## Which Should You Use?

| Question | Answer |
|---|---|
| Want all 10 strategies? | **Python** (Google Sheets only supports 4) |
| Want exact backtest match? | **Python** (Google Sheets uses simplified formulas) |
| Want it to work when laptop is off? | **Google Sheets** (or run Python on a cloud VM) |
| Want zero ongoing maintenance? | **Google Sheets** |
| Want to see condition-by-condition detail? | **Python** (richer report format) |

**Best of both worlds:** Set up both. Python sends the detailed daily email when your laptop is on. Google Sheets is the 24/7 backup that catches signals on weekends/vacations.

---

## Troubleshooting

**Python: emails not sending**
- Check `~/.leaps_signal_config.json` exists and has valid Gmail app password
- Test with `python notify.py` to send a test
- Check spam folder

**Google Sheets: emails not sending**
- Apps Script → Executions tab to see error logs
- Authorize the script (Google will ask the first time)
- Check `Session.getActiveUser().getEmail()` returns your email (it requires the script owner to be authenticated)

**Both: false signals or no signals**
- Compare current values in your sheet/log to my backtest output
- The backtest uses cached IV from `data_cache/term_structure.csv` — these scripts approximate

**Multiple signals same day**
- This is normal and means high-conviction setup
- Buy 1 contract per signal-day (don't pyramid same day)
- Track which strategies fired so you can validate the win in retrospect
