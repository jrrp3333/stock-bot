# Quick Start: Using Optimization Agents

## TL;DR - Get Going in 5 Minutes

### 1. Update Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Bot
```bash
python bot.py
```

### 3. You'll see:
```
📊 CURRENT OPTIMIZATION SETTINGS
  Take Profit: 5.0%
  Stop Loss: -3.0%
  Default Position Size: $100.00

🔍 Scanning 24 tickers...
```

### 4. Try These Commands
```
📍 Enter command: run                 # Run immediately
📍 Enter command: report              # See optimization report
📍 Enter command: metrics             # Show current settings
📍 Enter command: exit                # Stop
```

---

## Use Cases & Solutions

### "I want to optimize my TP/SL levels"

```
✅ Done! The bot auto-updates them after each run
   - Analyzes last 90 days of trades
   - Recommends new levels (with confidence %)
   - Only applies if confidence > 50%

To see recommendations:
📍 Enter command: report
```

### "Position sizes are too small / too large"

Edit `.env`:
```bash
DEFAULT_BUY_NOTIONAL=150.0      # Increase from $100 to $150
ENABLE_DYNAMIC_OPTIMIZATION=true # Keep volatility adjustment on
```

To check a specific ticker's recommendation:
```python
# In Python terminal
from optimization_agents import RiskAnalysisAgent
agent = RiskAnalysisAgent()
print(agent.recommend_position_size("AAPL", account_size=10000))
```

### "How do I know if a signal source is good?"

```
📍 Enter command: report

Look for:
📍 Signal Source Quality:
  politician_bot:
    win_rate_pct: 65.4          ← Higher is better
    profit_factor: 2.3          ← Higher is better (>1.0 = profitable)
    recommendation: Strong      ← Quality assessment
```

### "I want to disable auto-optimization"

Edit `.env`:
```bash
ENABLE_DYNAMIC_OPTIMIZATION=false
```

Your TP/SL will stay fixed at:
```bash
TAKE_PROFIT_PCT=5.0
STOP_LOSS_PCT=-3.0
```

### "I want to change the lookback period"

Edit `.env`:
```bash
OPTIMIZATION_LOOKBACK_DAYS=60   # Analyze last 60 days (default: 90)
```

Shorter = recent performance only
Longer = broader historical average

### "How do I add a new signal source?"

In `bot.py`, wherever you place an order, specify the source:

```python
# Instead of generic source...
place_buy_order(ticker)  # Uses "politician_bot"

# ...use a custom source
place_buy_order(ticker, signal_source="news_sentiment")
```

Now the report will show quality separately:
```
📍 Signal Source Quality:
  politician_bot: 65% WR, Strong
  news_sentiment: 48% WR, Neutral
```

---

## Performance Metrics Explained

### Win Rate
```
Win Rate = (Winning Trades) / (Total Trades) × 100%

80% = 8 out of 10 trades profitable ✅
50% = Break even on number of wins
30% = Losing money overall ❌
```

### Profit Factor
```
PF = (Gross Profit from Wins) / (Gross Loss from Losses)

PF > 2.0 = Very strong (2x profit per loss)
PF > 1.0 = Profitable
PF < 1.0 = Losing money
PF = 1.5 = Good (1.5x profit per loss)
```

### Example Report
```
Total Trades: 50
Win Rate: 72%
Avg Win: +4.2%
Avg Loss: -2.1%
Profit Factor: 2.8 ✅ (Very strong!)

This means:
- 36 winners, 14 losers
- Average winning trade: +4.2%
- Average losing trade: -2.1%
- Every $1 lost, you make $2.80
```

---

## Default Settings & What They Mean

| Setting | Default | Meaning |
|---------|---------|---------|
| TAKE_PROFIT_PCT | 5.0% | Sell when up 5% |
| STOP_LOSS_PCT | -3.0% | Sell when down 3% |
| DEFAULT_BUY_NOTIONAL | $100 | Buy $100 per signal |
| ENABLE_DYNAMIC_OPTIMIZATION | true | Auto-adjust TP/SL |
| OPTIMIZATION_LOOKBACK_DAYS | 90 | Analyze last 90 days |
| PAPER_TRADING | true | No real money ✅ |

---

## Typical Workflow

### Week 1: Initial Run
```bash
python bot.py
📍 Enter command: run          # Execute once
📍 Enter command: exit
```

### Weeks 2-4: Collect Data
```bash
python bot.py
📍 Enter command: start         # Auto-run daily at 10 AM
# Press Ctrl+C to stop
```

### Week 5+: Analyze & Optimize
```bash
python bot.py
📍 Enter command: report        # See recommendations

Example output:
⚙️  TP/SL Recommendations:
  TAKE_PROFIT:    5.0% → 4.6% (confidence: 87%)
  STOP_LOSS:     -3.0% → -3.2% (confidence: 89%)

✅ Confidence high? Apply changes to .env
❌ Confidence low? Keep current settings
```

---

## Troubleshooting Quick Fixes

| Problem | Solution |
|---------|----------|
| "numpy not found" | `pip install numpy` |
| Report shows "No trades" | Run bot 5-10 more times |
| TP/SL not updating | Check `ENABLE_DYNAMIC_OPTIMIZATION=true` in .env |
| Slow execution | This is normal; API calls take 30-60 sec |
| Position sizes wrong | Set `DEFAULT_BUY_NOTIONAL` in .env |

---

## Key Files

| File | Purpose |
|------|---------|
| `bot.py` | Main trading bot (updated with optimization) |
| `optimization_agents.py` | ✨ NEW: Optimization engine |
| `trade_logger.py` | Tracks all trades (unchanged) |
| `requirements.txt` | Dependencies (numpy added) |
| `.env` | Configuration (add new vars) |
| `trades.db` | SQLite database of trades |

---

## Example `.env` For Optimization

```bash
# ────── Alpaca ──────
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
PAPER_TRADING=true

# ────── Finnhub ──────
FINNHUB_API_KEY=your_key

# ────── Optimization (NEW) ──────
ENABLE_DYNAMIC_OPTIMIZATION=true
OPTIMIZATION_LOOKBACK_DAYS=90
TAKE_PROFIT_PCT=5.0
STOP_LOSS_PCT=-3.0
DEFAULT_BUY_NOTIONAL=100.0

# ────── Optional ──────
FORCE_TEST_BUY=false
SHOW_OPTIMIZATION_REPORT=true
```

---

## Next Steps

1. ✅ Run bot with default settings for 2-4 weeks
2. ✅ Use `report` command to see recommendations
3. ✅ Adjust `.env` based on what report suggests
4. ✅ Monitor `profit_factor` and `win_rate`
5. ✅ Fine-tune individual signal sources

**Questions?** Check `OPTIMIZATION_GUIDE.md` for detailed documentation!
