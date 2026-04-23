# Interactive Bot Commands: Examples & Output

## Running the Bot

### Command 1: View Current Optimization Settings

```
📍 Enter command: metrics

════════════════════════════════════════════════════════════════
📊 CURRENT OPTIMIZATION SETTINGS
════════════════════════════════════════════════════════════════
  Take Profit: 5.0%
  Stop Loss: -3.0%
  Default Position Size: $100.00

  Recent Performance (90 days):
    Total Trades: 45
    Win Rate: 72.0%
    Profit Factor: 2.3
    Avg Duration: 2.5 hours
════════════════════════════════════════════════════════════════
```

---

## Command 2: Run Bot Immediately

```
📍 Enter command: run

==================================================
🤖 Bot running at 2026-03-31 14:23:15
==================================================

🔄 TP updated: 5.0% → 4.6% (confidence: 87%)
🔄 SL updated: -3.0% → -3.2% (confidence: 89%)

════════════════════════════════════════════════════════════════
📊 CURRENT OPTIMIZATION SETTINGS
════════════════════════════════════════════════════════════════
  Take Profit: 4.6%
  Stop Loss: -3.2%
  Default Position Size: $100.00

  Recent Performance (90 days):
    Total Trades: 50
    Win Rate: 68.0%
    Profit Factor: 2.8
════════════════════════════════════════════════════════════════

📊 Evaluating 3 open position(s) for TP/SL exits...
  ⏳ AAPL pnl 2.15% (holding)
  ⏳ MSFT pnl 1.82% (holding)
  ⏳ NVDA pnl -1.23% (holding)

🔍 Scanning 24 tickers for politician activity...

--- Checking AAPL ---
  ⬜ No recent politician buys

--- Checking MSFT ---
  🏛️  2 politician buy(s) found!
      → Nancy Pelosi bought on 2026-03-25
      → Dianne Feinstein bought on 2026-03-28
  📈 MSFT momentum: 1.23%
  🟢 Positive momentum + politician backing → BUYING
  ✅ ORDER PLACED: Bought $103.50 of MSFT (volatility-adjusted)
  🧾 Logged entry #142 for MSFT

--- Checking GOOGL ---
  ⬜ No recent politician buys

--- Checking AMZN ---
  🏛️  1 politician buy(s) found!
      → Warren Buffett bought on 2026-03-29
  📈 AMZN momentum: 0.45%
  🟢 Positive momentum + politician backing → BUYING
  ✅ ORDER PLACED: Bought $98.25 of AMZN (volatility-adjusted)
  🧾 Logged entry #143 for AMZN

[... more tickers ...]

✅ Bot run complete.
```

---

## Command 3: View Comprehensive Optimization Report

```
📍 Enter command: report

======================================================================
📊 OPTIMIZATION REPORT
======================================================================

📈 Trade Summary:
  total_trades: 50
  win_rate_pct: 68.0
  avg_win_pct: 4.2
  avg_loss_pct: -2.1
  profit_factor: 2.8
  avg_duration_hours: 2.3

⚙️  TP/SL Recommendations:
  TAKE_PROFIT
    Current: 5.0%
    Recommended: 4.6%
    Confidence: 87.0%
    Rationale: Based on avg win of 4.2% across 50 trades

  STOP_LOSS
    Current: -3.0%
    Recommended: -3.2%
    Confidence: 89.0%
    Rationale: Based on avg loss of -2.1% across 50 trades

📍 Signal Source Quality:
  politician_bot:
    trade_count: 45
    win_rate_pct: 70.5
    avg_win_pct: 4.5
    avg_loss_pct: -2.0
    profit_factor: 2.9
    quality_score: 173.7
    recommendation: Strong

  forced_test_buy:
    trade_count: 5
    win_rate_pct: 40.0
    avg_win_pct: 3.2
    avg_loss_pct: -2.5
    profit_factor: 1.1
    quality_score: 52.0
    recommendation: Weak

🎯 Performance by Ticker:
  AAPL: 8 trades, 75.0% WR, 3.5% avg PnL
  MSFT: 7 trades, 71.4% WR, 4.2% avg PnL
  NVDA: 6 trades, 66.7% WR, 3.8% avg PnL
  AMZN: 5 trades, 60.0% WR, 2.9% avg PnL
  META: 4 trades, 75.0% WR, 4.5% avg PnL
  GOOGL: 3 trades, 66.7% WR, 3.2% avg PnL
  TSLA: 3 trades, 33.3% WR, 1.5% avg PnL
  [... more tickers ...]

======================================================================
```

---

## Command 4: Start Daily Scheduled Runs

```
📍 Enter command: start

⏰ Bot scheduled daily at 10:00 AM. Press Ctrl+C to stop.

[Bot waits until 10:00 AM, then executes like "run" command above]

[At 10:00 AM:]
==================================================
🤖 Bot running at 2026-03-31 10:00:00
==================================================
[... full bot execution ...]

[At 10:01 AM waiting for next day:]
⏰ Waiting for next scheduled run (tomorrow at 10:00 AM)

[User presses Ctrl+C]
⏹️  Bot scheduler stopped.
```

---

## Command 5: Exit Program

```
📍 Enter command: exit

👋 Goodbye!

[Program terminates]
```

---

## Real-World Example Scenario

### Week 1: Starting Out
```
Trades executed: 0
Optimization: No data

Bot output:
"📊 CURRENT OPTIMIZATION SETTINGS
  Recent Performance (90 days): No trades yet
  
⚠️  Insufficient data for optimization (< 10 trades)"
```

### Week 2: Collecting Data
```
Trades executed: 12
Optimization: Starting to work

📊 Trade Summary:
  win_rate_pct: 58.3
  avg_win_pct: 3.8
  avg_loss_pct: -2.5
  profit_factor: 1.4

⚙️  TP/SL Recommendations:
  TAKE_PROFIT: Confidence 40% (too low)
  STOP_LOSS: Confidence 42% (too low)
```

### Week 3-4: Getting Recommendations
```
Trades executed: 35+
Optimization: High confidence

⚙️  TP/SL Recommendations:
  TAKE_PROFIT: 5.0% → 4.4% (Confidence 82% ✅)
  STOP_LOSS: -3.0% → -2.8% (Confidence 85% ✅)

📍 Signal Source Quality:
  politician_bot: Strong (quality score: 156)
```

### Week 5+: Optimized & Profitable
```
Trades executed: 50+
Win Rate: 70%+
Profit Factor: 2.5+

Key insights from report:
✅ politician_bot is Strong (70% WR)
⚠️  forced_test_buy is Weak (40% WR)
📈 AAPL and MSFT are top performers
📉 TSLA has below 50% win rate

Recommendation: Focus more on tickers with >70% WR
```

---

## Position Sizing Examples

### Automatic Volatility Adjustment

When you run `run` command and bot tries to buy:

```python
# Bot calculates for AAPL (low volatility, 25%)
Risk Agent: "AAPL volatility: 25%, recommended: $125"
📍 ORDER: $125 of AAPL (up from $100 base)

# Bot calculates for NVDA (high volatility, 45%)
Risk Agent: "NVDA volatility: 45%, recommended: $75"
📍 ORDER: $75 of NVDA (down from $100 base)
```

Output in bot log:
```
--- Checking AAPL ---
  ✅ ORDER PLACED: Bought $125.00 of AAPL (volatility-adjusted)

--- Checking NVDA ---
  ✅ ORDER PLACED: Bought $75.00 of NVDA (volatility-adjusted)
```

---

## Interpretation Guide

### What to Look For in Report

**1. Win Rate**
```
< 40%: Your strategy needs work ❌
40-50%: Okay, but room for improvement ⚠️
50-60%: Good, acceptable ✅
60%+: Excellent, focus on this signal source 🌟
70%+: Outstanding, maximize this ⭐
```

**2. Profit Factor**
```
< 0.5: Losing money fast ❌
0.5-1.0: Not profitable ❌
1.0-1.5: Barely profitable ⚠️
1.5-2.0: Good (1.5-2.0$ profit per $1 loss) ✅
2.0+: Excellent (2+$ profit per $1 loss) ⭐
```

**3. Confidence Score in TP/SL Recs**
```
< 30%: Ignore recommendation⚠️ (need more data)
30-50%: Consider but don't change yet
50-70%: Worth considering
70%+: High confidence, consider applying ✅
85%+: Very high, recommended to apply ⭐
```

**4. Average Win vs Average Loss**
```
Avg Win: 4.2%
Avg Loss: -2.1%
Ratio: 2.0x more profit than loss ✅

If Avg Win < Avg Loss: Revisit entry signal quality ⚠️
```

---

## Troubleshooting Commands

### Issue: "Insufficient data"

```
📍 You see: "⚠️ Insufficient trade history (< 10 trades)"

Solution:
  Run `run` command 5-10 more times
  OR set OPTIMIZATION_LOOKBACK_DAYS=60 in .env for faster results
  Look for: "Recent Performance: XX trades"
```

### Issue: Position sizes too small

```
📍 Check current base:
$ python bot.py
📍 Enter command: metrics
  Default Position Size: $100.00

Solution:
  Update .env: DEFAULT_BUY_NOTIONAL=150.0
  Restart bot

Now bot will use $150 as base (with volatility adjustments)
```

### Issue: Want to see win rate by ticker

```
📍 Enter command: report

Look for section:
🎯 Performance by Ticker:
  AAPL: 8 trades, 75.0% WR ← AAPL is your best
  TSLA: 3 trades, 33.3% WR ← TSLA is worst
```

---

## Using Recommendations in Real Trading

### Scenario 1: High Confidence Recommendation

Report shows:
```
TAKE_PROFIT: 5.0% → 4.6% (Confidence: 87%)
STOP_LOSS: -3.0% → -3.2% (Confidence: 89%)
```

**Action:**
1. Edit `.env`:
   ```
   TAKE_PROFIT_PCT=4.6
   STOP_LOSS_PCT=-3.2
   ```
2. Restart bot
3. Monitor for 1-2 weeks
4. Check if win rate improves

---

### Scenario 2: Low Signal Quality

Report shows:
```
fake_signal_source: 35% WR, 0.6 PF
Recommendation: Weak
```

**Action:**
1. Stop placing trades with this signal
2. Focus on high-quality sources (>60% WR)
3. Re-evaluate later (200+ trades)

---

### Scenario 3: Ticker under-performing

Report shows:
```
TSLA: 3 trades, 33.3% WR, 0.6 PF ← Low WR
AAPL: 8 trades, 75.0% WR, 2.8 PF ← High WR
```

**Action:**
1. Remove TSLA from watchlist (or test in isolation)
2. Add more similar tickers to AAPL
3. Focus on what's working

---

## Interactive Loop Example

```
📍 Enter command: run
[Bot executes scan, shows results]
[You observe: "NVDA position up 4.5%, close to TP"]

📍 Enter command: metrics
[You see: "Take Profit: 4.6%"]
[You think: "NVDA will hit TP soon, this is working"]

📍 Enter command: report
[You see: "Win Rate: 72%, Profit Factor: 2.8"]
[You think: "System is profitable, keep running"]

📍 Enter command: start
[Bot auto-runs tomorrow at 10 AM]
[You check back in a week...]

📍 Enter command: report
[You see: "TAKE_PROFIT recommended: 4.6% → 4.3% (90% conf)"]
[You update .env and restart]
[New run hits more targets, better profits!]
```

---

## Keyboard Shortcuts & Tips

```
Up Arrow    - Shows last command you typed
Ctrl+C      - Stop current operation (exit, or interrupt scheduler)
Ctrl+L      - Clear terminal (some terminals)

Commands run instantly: run, report, metrics, exit
Command blocks scheduler: start (press Ctrl+C to exit)
```

---

## Summary

The bot now provides:
- ✅ Real-time metrics and reports
- ✅ Interactive commands for control
- ✅ Automated recommendations
- ✅ Detailed performance analysis
- ✅ Zero manual calculation needed

Just use these 5 commands and you're set! 🚀
