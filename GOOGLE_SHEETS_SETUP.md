# Google Sheets Dashboard — CORRECTED Version

> **Important update:** The original sheet checked *states* (e.g. "is RSI between 50-65?"). The actual backtest checks *cross events* (e.g. "did RSI cross above 50 today?"). This corrected version detects crosses properly so it matches the backtest. This is critical — without this fix, the sheet fires far more "buy" alerts than the strategy actually triggers.

---

## What the Sheet Will Do

When you open it, you'll see:
- **SPY price**, 50DMA, 200DMA (today AND yesterday)
- **VIX**, 5-day VIX change
- **RSI 14** (today + yesterday) — to detect *cross above 50*
- **MACD line + signal line** (today + yesterday) — to detect *cross up*
- A traffic-light cell showing 🟢 / 🟡 / 🔴

---

## TAB 1: "Prices" — Pull Historical Data

Click cell **A1** and paste:
```
=GOOGLEFINANCE("SPY","close",TODAY()-400,TODAY(),"DAILY")
```

This creates **Date** (column A) and **Close** (column B).

---

## TAB 2: "Dashboard" — Build the Logic

### Section 1 — Live Quotes

| Cell | Formula | What it shows |
|---|---|---|
| `A3` | `SPY Price (today)` | (label) |
| `B3` | `=GOOGLEFINANCE("SPY","price")` | Current SPY |
| `A4` | `SPY (yesterday)` | (label) |
| `B4` | `=INDEX(Prices!B:B,COUNTA(Prices!B:B))` | Yesterday's close |
| `A5` | `VIX` | (label) |
| `B5` | `=GOOGLEFINANCE("INDEXCBOE:VIX","price")` | Current VIX |
| `A6` | `VIX 5d ago` | (label) |
| `B6` | `=INDEX(GOOGLEFINANCE("INDEXCBOE:VIX","close",TODAY()-10,TODAY()),2,2)` | VIX ~5d ago |

If `B5` errors out, try one of these alternatives:
- `=GOOGLEFINANCE("CBOE:VIX","price")`
- `=INDEX(GOOGLEFINANCE("INDEXCBOE:VIX","price",TODAY()-3,TODAY()),2,2)`

### Section 2 — Moving Averages (Today + Yesterday)

| Cell | Formula |
|---|---|
| `A8` | `50-DMA today` |
| `B8` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-50,0,50,1))` |
| `A9` | `50-DMA yesterday` |
| `B9` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-51,0,50,1))` |
| `A10` | `200-DMA today` |
| `B10` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-200,0,200,1))` |

### Section 3 — RSI 14 Helper (columns F–J)

| Cell | Formula |
|---|---|
| `F1` | `Date` |
| `G1` | `Price` |
| `H1` | `Change` |
| `I1` | `Gain` |
| `J1` | `Loss` |
| `F2` | `=ARRAYFORMULA(IF(ROW(Prices!A2:A)<=COUNTA(Prices!A:A),Prices!A2:A,""))` |
| `G2` | `=ARRAYFORMULA(IF(ROW(Prices!B2:B)<=COUNTA(Prices!B:B),Prices!B2:B,""))` |
| `H3` | `=ARRAYFORMULA(IF(LEN(G3:G),G3:G-G2:G,""))` |
| `I3` | `=ARRAYFORMULA(IF(LEN(H3:H),IF(H3:H>0,H3:H,0),""))` |
| `J3` | `=ARRAYFORMULA(IF(LEN(H3:H),IF(H3:H<0,-H3:H,0),""))` |

| Cell | Formula | What it shows |
|---|---|---|
| `A12` | `RSI today` | (label) |
| `B12` | `=100-100/(1+(AVERAGE(OFFSET(I1,COUNTA(I:I)-14,0,14,1))/AVERAGE(OFFSET(J1,COUNTA(J:J)-14,0,14,1))))` | Today's RSI |
| `A13` | `RSI yesterday` | (label) |
| `B13` | `=100-100/(1+(AVERAGE(OFFSET(I1,COUNTA(I:I)-15,0,14,1))/AVERAGE(OFFSET(J1,COUNTA(J:J)-15,0,14,1))))` | Yesterday's RSI |

### Section 4 — MACD (Today + Yesterday)

| Cell | Formula |
|---|---|
| `A15` | `MACD today` |
| `B15` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-12,0,12,1))-AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-26,0,26,1))` |
| `A16` | `MACD signal today` |
| `B16` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-9,0,9,1))-AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-21,0,21,1))` |
| `A17` | `MACD yesterday` |
| `B17` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-13,0,12,1))-AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-27,0,26,1))` |
| `A18` | `MACD signal yesterday` |
| `B18` | `=AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-10,0,9,1))-AVERAGE(OFFSET(Prices!B1,COUNTA(Prices!B:B)-22,0,21,1))` |

### Section 5 — CROSS DETECTION (the fix!)

These check whether each signal **crossed today** by comparing today vs yesterday.

| Cell | Formula | What it detects |
|---|---|---|
| `A21` | `Signal 1: MACD crossed up today?` | (label) |
| `B21` | `=IF(AND(B15>B16, B17<=B18), 1, 0)` | MACD above signal today, below yesterday |
| `A22` | `Signal 2: RSI crossed >50 today?` | (label) |
| `B22` | `=IF(AND(B12>=50, B13<50), 1, 0)` | RSI ≥50 today, <50 yesterday |
| `A23` | `Signal 3: SPY reclaimed 50DMA today?` | (label) |
| `B23` | `=IF(AND(B3>=B8, B4<B9), 1, 0)` | SPY above 50DMA today, below yesterday |

### Section 6 — Gates

| Cell | Formula |
|---|---|
| `A25` | `Gate 1: SPY > 200DMA?` |
| `B25` | `=IF(B3>=B10, 1, 0)` |
| `A26` | `Gate 2: VIX < 28?` |
| `B26` | `=IF(B5<28, 1, 0)` |
| `A27` | `Filter: RSI < 65?` |
| `B27` | `=IF(B12<65, 1, 0)` |

### Section 7 — Final Signal Light

| Cell | Formula |
|---|---|
| `A29` | `SCORE (need 2+)` |
| `B29` | `=B21+B22+B23` |
| `A30` | `All gates passing?` |
| `B30` | `=IF(AND(B25=1, B26=1, B27=1), "✅ YES", "❌ NO")` |
| `A32` | `🚦 SIGNAL` |
| `B32` | `=IF(AND(B29>=2, B25=1, B26=1, B27=1), "🟢 BUY — confirm on TradingView before buying", IF(B29>=1, "🟡 1 of 3 signals — watch tomorrow", "🔴 NO SIGNAL"))` |

### Section 8 — Conditional Formatting

1. Click **B32**, set font size **18**, bold
2. **Format → Conditional formatting**
3. Add 3 rules:
   - Text contains `BUY` → **green background**
   - Text contains `1 of 3 signals` → **yellow background**
   - Text contains `NO SIGNAL` → **red background**

---

## How This Differs From the Old Version

| Signal | OLD formula (broken) | NEW formula (correct) |
|---|---|---|
| MACD | `B10 > 0` (state) | `B15>B16 AND B17<=B18` (cross today) |
| RSI | `50 ≤ B6 ≤ 65` (state) | `B12 >= 50 AND B13 < 50` (cross today) |
| 50DMA | `B2 >= B4` (state) | `B3>=B8 AND B4<B9` (cross today) |

The OLD version would fire 🟢 on dozens of days per year. The NEW version fires only on actual cross events — matching the backtest's expected frequency of ~1-2 per year.

---

## Quick Visual: The Layout

```
A3  SPY Price (today)         B3   $742.30      ← live
A4  SPY (yesterday)           B4   $738.20      ← from Prices
A5  VIX                       B5   17.87        ← live
A6  VIX 5d ago                B6   17.43

A8  50-DMA today              B8   $687.62
A9  50-DMA yesterday          B9   $685.20
A10 200-DMA today             B10  $674.98

A12 RSI today                 B12  81.9
A13 RSI yesterday             B13  76.4

A15 MACD today                B15  +14.97
A16 MACD signal today         B16  +13.79
A17 MACD yesterday            B17  +14.21
A18 MACD signal yesterday     B18  +13.45

A21 Sig 1: MACD cross up?     B21  0    (no cross today)
A22 Sig 2: RSI cross >50?     B22  0
A23 Sig 3: SPY reclaimed 50DMA?  B23  0

A25 Gate 1: SPY>200DMA?       B25  1    ✅
A26 Gate 2: VIX<28?           B26  1    ✅
A27 Filter: RSI<65?           B27  0    ❌ (RSI 82, overbought)

A29 SCORE                     B29  0
A30 All gates passing?        B30  ❌ NO

A32 🚦 SIGNAL                 B32  🔴 NO SIGNAL
```

---

## Notes on Accuracy

The MACD here uses **simple moving averages** instead of true EMAs (Sheets can't compute EMAs cleanly). This means:
- The cross **direction** is correct (above/below)
- The cross **timing** may differ by 1-2 days vs TradingView
- The cross **magnitude** is slightly different

So the sheet is best used as an **alert system**. When it flashes 🟢, you confirm on TradingView before pulling the trigger.

For a perfectly accurate signal, use the Python `daily_signal.py` script (uses true EMAs and matches the backtest exactly).
