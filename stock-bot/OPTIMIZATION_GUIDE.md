# Trading Bot Optimization Agents Guide

## Overview

Your trading bot now has sophisticated **optimization agents** that automatically improve profit margins by:

1. **Dynamic TP/SL Optimization** - Adjusts take-profit and stop-loss levels based on historical performance
2. **Risk Analysis** - Recommends position sizing based on volatility
3. **Trade Analysis** - Analyzes win rate, profit factor, and performance patterns
4. **Entry Quality Assessment** - Evaluates signal source reliability

## Architecture

### Optimization Agents (optimization_agents.py)

#### 1. TradeAnalysisAgent
Analyzes historical trades to extract performance metrics:
- **get_closed_trades()** - Retrieves trades from SQLite database
- **calculate_win_rate()** - Computes winning trade percentage
- **calculate_avg_win_loss()** - Calculates average winning and losing percentages
- **calculate_profit_factor()** - Computes gross profit / gross loss ratio
- **analyze_by_signal_source()** - Breaks down performance by signal type
- **get_summary()** - Generates overall trade statistics

#### 2. DynamicTPSLAgent
Provides intelligent take-profit and stop-loss recommendations:
- **recommend_tp_sl()** - Generates optimized TP/SL levels with confidence scores
- Uses average win/loss percentages to suggest exit levels
- Confidence increases as trade history grows (minimum 10 trades)

#### 3. RiskAnalysisAgent
Manages risk through volatility-adjusted position sizing:
- **get_ticker_volatility()** - Calculates annualized volatility via yfinance
- **recommend_position_size()** - Suggests notional amount based on volatility
- **get_correlation_with_spy()** - Measure diversification against SPY

#### 4. EntryQualityAgent
Evaluates reliability of different signal sources:
- **assess_signal_quality()** - Scores signal sources on win rate and profit factor
- Quality score formula: `(win_rate / 100) * (1 + profit_factor) * 100`
- Flags "Strong", "Neutral", or "Weak" signals

## Configuration

### Environment Variables

Add these to your `.env` file to customize optimization behavior:

```bash
# Optimization Settings
ENABLE_DYNAMIC_OPTIMIZATION=true          # Auto-update TP/SL (default: true)
OPTIMIZATION_LOOKBACK_DAYS=90             # Historical period to analyze (default: 90)
SHOW_OPTIMIZATION_REPORT=true             # Display report at startup (default: true)
TAKE_PROFIT_PCT=5.0                       # Initial take-profit (default: 5.0%)
STOP_LOSS_PCT=-3.0                        # Initial stop-loss (default: -3.0%)
DEFAULT_BUY_NOTIONAL=100.0                # Default position size (default: $100)

# Trading Settings
PAPER_TRADING=true                        # Use paper trading (default: true)
FORCE_TEST_BUY=false                      # Manual test buy (default: false)
FORCE_TEST_BUY_TICKER=AAPL                # Test ticker
FORCE_TEST_BUY_NOTIONAL=25                # Test position size
```

## Usage

### Running the Bot

```bash
# Activate virtual environment
.\.venv-1\Scripts\Activate.ps1

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the bot
python bot.py
```

### Interactive Commands

The bot now supports interactive commands:

```
📍 Enter command: run
    Executes bot scan immediately

📍 Enter command: report
    Shows comprehensive optimization report with:
    - Trade summary (win rate, profit factor, etc.)
    - TP/SL recommendations
    - Signal source quality assessment
    - Performance by ticker

📍 Enter command: metrics
    Shows current optimization settings and recent performance

📍 Enter command: start
    Schedules daily execution at 10:00 AM
    Press Ctrl+C to stop

📍 Enter command: exit
    Exit the program
```

### Example Output

```
⚙️  TP/SL Recommendations:
  TAKE_PROFIT
    Current: 5.0%
    Recommended: 4.85%
    Confidence: 78.0%
    Rationale: Based on avg win of 4.85% across 50 trades

  STOP_LOSS
    Current: -3.0%
    Recommended: -3.3%
    Confidence: 78.0%
    Rationale: Based on avg loss of -3.3% across 50 trades
```

## How It Works In-Depth

### Dynamic Parameter Updates

When the bot runs:

1. **Fetch Historical Trades**
   ```
   SELECT all trades from past 90 days
   ```

2. **Calculate Metrics**
   ```
   Win Rate = winning trades / total trades
   Avg Win = mean(pnl% for winning trades)
   Avg Loss = mean(pnl% for losing trades)
   Profit Factor = gross profit / gross loss
   ```

3. **Generate Recommendations**
   ```
   Recommended TP = Avg Win × 0.85
   Recommended SL = Avg Loss × 1.1
   Confidence = min(0.95, trades_count / 100)
   ```

4. **Apply if Confident**
   ```
   IF confidence > 0.5:
       Update TAKE_PROFIT_PCT
       Update STOP_LOSS_PCT
   ```

### Position Sizing Algorithm

For each buy signal:

```python
volatility = annualized_standard_deviation(ticker_returns)
adjustment = max(0.1, min(1.0, 0.5 / volatility))
position_size = base_size × adjustment

# Example:
# Base notional: $100
# Volatility: 40% (tech stock)
# Adjusted size: $100 × (0.5/0.4) = $125
```

## Performance Tracking

All trades are automatically logged to **trades.db**:

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    ticker TEXT,
    entry_price REAL,
    exit_price REAL,
    qty REAL,
    signal_source TEXT,
    pnl_percent REAL,
    failure_notes TEXT  -- Auto-researched for losses
);
```

The optimization agents query this database to learn and improve.

## Open-Source APIs Used

1. **Finnhub** (congressional trading data)
   - Free tier: 60 API calls/minute
   - https://finnhub.io

2. **yfinance** (historical price data)
   - 100% free, no API key required
   - https://github.com/ranaroussi/yfinance

3. **Alpaca** (trading execution)
   - Free paper trading account
   - https://alpaca.markets

## Implementation Tips

### For Optimization

1. **Warm-up Period**: Let the bot run for 20-30 trades before enabling dynamic optimization (minimum 10 trades recommended)
2. **Monitor Confidence**: Only act on recommendations with >50% confidence
3. **Gradual Changes**: TP/SL recommendations adjust by ~10-15% to avoid over-fitting
4. **Review Regularly**: Run the `report` command weekly to see signal source quality

### For Risk Management

- **Position Sizing**: Adjusts based on IV (implied volatility)
- **Correlation Check**: AAPL might be correlated with MSFT, reducing diversification
- **Win Rate Target**: Aim for 50%+ win rate before increasing position sizes

### For Trade Quality

Quality score formula encourages:
- High win rates (directly multiplied)
- Consistent profits (via profit factor)
- Skips sources with <5 trades

Example:
```
politician_bot: 60% WR, 1.8 PF = (0.60) × (2.8) × 100 = 168 ✅ Strong
momentum_only: 40% WR, 0.5 PF = (0.40) × (1.5) × 100 = 60 ❌ Weak
```

## Troubleshooting

### Issue: "Insufficient data for optimization"
**Solution**: Run the bot at least 10 more times to get historical trades

### Issue: Position sizes are too small
**Solution**: Check ticker volatility with:
```python
from optimization_agents import RiskAnalysisAgent
agent = RiskAnalysisAgent()
print(agent.get_ticker_volatility("AAPL"))  # Should be 0-1 range
```

### Issue: TP/SL not updating
**Solution**: Verify `ENABLE_DYNAMIC_OPTIMIZATION=true` in `.env` and confidence > 0.5

### Issue: ModuleNotFoundError: No module named 'numpy'
**Solution**: 
```bash
pip install numpy
# or
pip install -r requirements.txt
```

## Example Workflow

```bash
# Day 1: Initial setup
python bot.py
📍 Enter command: run
📍 Enter command: exit

# Day 2-10: Collect trade data (10+ trades needed)
python bot.py
📍 Enter command: start
# (Let run daily at 10 AM)

# Day 11+: Review optimization
python bot.py
📍 Enter command: report

# Output might show:
# TAKE_PROFIT: 5.0% → 4.6% (92% confidence)
# STOP_LOSS: -3.0% → -3.5% (87% confidence)

📍 Enter command: metrics
# Shows current performance: 73% win rate, 2.4 profit factor
```

## Advanced Customization

### Add custom signal sources

The bot tracks signal sources for quality assessment. Add new sources:

```python
place_buy_order(ticker, signal_source="my_custom_signal")
```

Then analyze with:
```python
agent = EntryQualityAgent()
quality = agent.assess_signal_quality("my_custom_signal")
```

### Modify optimization weights

In `optimization_agents.py`, DynamicTPSLAgent:

```python
# Current: uses 0.85x avg win
recommended_tp = avg_win * 0.85

# More aggressive: use 0.95x
recommended_tp = avg_win * 0.95

# More conservative: use 0.75x
recommended_tp = avg_win * 0.75
```

### Add volatility tiers

Customize position sizing:

```python
def get_optimized_position_size(ticker, base=100):
    vol = risk_agent.get_ticker_volatility(ticker)
    
    if vol < 0.20:  # Low vol
        return base * 1.5
    elif vol < 0.40:  # Medium vol
        return base * 1.0
    else:  # High vol
        return base * 0.5
```

## Resources

- **Optimization Agents Docs**: See docstrings in `optimization_agents.py`
- **Trade Logger**: See `trade_logger.py` for database schema
- **Bot Main**: See `bot.py` for integration points
- **Finnhub API**: https://finnhub.io/docs/api
- **Alpaca API**: https://docs.alpaca.markets

## Summary

The optimization agents provide:

✅ **Automated TP/SL tuning** based on historical wins/losses
✅ **Volatility-adjusted position sizing** for risk management
✅ **Signal quality scoring** to focus on best performs
✅ **Comprehensive reporting** for analysis and adjustment
✅ **Zero-cost integration** using only free open-source APIs

Start with default settings, let it trade for 2-4 weeks, then review the optimization report to see recommendations!
