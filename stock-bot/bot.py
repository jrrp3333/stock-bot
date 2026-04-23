import os
import sys
import requests
import yfinance as yf
import schedule
import time
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, UTC
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from trade_logger import initialize_schema, log_entry, log_trade, DB_PATH
from optimization_agents import (
    TradeAnalysisAgent,
    DynamicTPSLAgent,
    RiskAnalysisAgent,
    EntryQualityAgent,
    EnsembleTradingAgent,
    PaperBacktestHarness,
    generate_optimization_report,
    get_optimization_snapshot,
    get_ensemble_weight_state,
)
from pattern_signals import detect_ohlc_patterns

# Load .env before reading top-level runtime settings.
load_dotenv()

HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
AUTO_START_MODE = os.getenv("AUTO_START_MODE", "interactive").strip().lower()

REQUESTS_SESSION = requests.Session()
REQUESTS_SESSION.headers.update({"User-Agent": "trade-bot/1.0"})

# ── Load keys ─────────────────────────────────────────────────────────────────
API_KEY      = os.getenv("ALPACA_API_KEY")
SECRET_KEY   = os.getenv("ALPACA_SECRET_KEY")
FINNHUB_KEY  = os.getenv("FINNHUB_API_KEY")
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
FORCE_TEST_BUY = os.getenv("FORCE_TEST_BUY", "false").lower() == "true"
FORCE_TEST_BUY_TICKER = os.getenv("FORCE_TEST_BUY_TICKER", "AAPL")
FORCE_TEST_BUY_NOTIONAL = float(os.getenv("FORCE_TEST_BUY_NOTIONAL", "25"))

# ── Connect to Alpaca (paper trading — no real money) ─────────────────────────
api = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)

# ── Watchlist of tickers to check for politician activity ─────────────────────
# Finnhub requires us to check ticker by ticker, so we watch these popular ones
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM",
    "BAC", "WMT"
]

_UNIVERSE_ENV = os.getenv("TICKER_UNIVERSE", "").strip()
WATCHLIST = [
    t.strip().upper() for t in _UNIVERSE_ENV.split(",") if t.strip()
] if _UNIVERSE_ENV else list(DEFAULT_WATCHLIST)

# Minimal sector map for portfolio concentration limits.
TICKER_SECTOR_MAP = {
    "AAPL": "technology", "MSFT": "technology", "GOOGL": "technology", "AMZN": "consumer_discretionary",
    "NVDA": "technology", "META": "communication_services", "TSLA": "consumer_discretionary",
    "JPM": "financials", "BAC": "financials", "WMT": "consumer_staples", "UNH": "healthcare",
    "PFE": "healthcare", "XOM": "energy", "CVX": "energy", "LMT": "industrials", "RTX": "industrials",
    "BA": "industrials", "GS": "financials", "V": "financials", "AMD": "technology", "INTC": "technology",
    "DIS": "communication_services", "NFLX": "communication_services", "CRM": "technology", "PYPL": "financials",
}

# ── Optimization Settings ────────────────────────────────────────────────────
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "5.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "-3.0"))
DEFAULT_BUY_NOTIONAL = float(os.getenv("DEFAULT_BUY_NOTIONAL", "100.0"))
ENABLE_DYNAMIC_OPTIMIZATION = os.getenv("ENABLE_DYNAMIC_OPTIMIZATION", "true").lower() == "true"
OPTIMIZATION_LOOKBACK_DAYS = int(os.getenv("OPTIMIZATION_LOOKBACK_DAYS", "90"))
SHOW_OPTIMIZATION_REPORT = os.getenv("SHOW_OPTIMIZATION_REPORT", "true").lower() == "true"
MIN_ENSEMBLE_CONFIDENCE = float(os.getenv("MIN_ENSEMBLE_CONFIDENCE", "0.55"))
EXTERNAL_POLITICIAN_BIAS = float(os.getenv("EXTERNAL_POLITICIAN_BIAS", "0.15"))
ENABLE_WEEKLY_AUTO_REWEIGHT = os.getenv("ENABLE_WEEKLY_AUTO_REWEIGHT", "true").lower() == "true"
BACKTEST_LOOKBACK_WEEKS = int(os.getenv("BACKTEST_LOOKBACK_WEEKS", "26"))
BACKTEST_OOS_WEEKS = int(os.getenv("BACKTEST_OOS_WEEKS", "8"))
AUTONOMOUS_INTERVAL_MINUTES = int(os.getenv("AUTONOMOUS_INTERVAL_MINUTES", "30"))

# Confirmed entry model (ported from backtest.py)
ENABLE_CONFIRMED_ENTRY_MODEL = os.getenv("ENABLE_CONFIRMED_ENTRY_MODEL", "true").lower() == "true"
ENTRY_CONFIRMATION_MODE = os.getenv("ENTRY_CONFIRMATION_MODE", "boolean").strip().lower()
ENTRY_MIN_CONFIRMATIONS = int(os.getenv("ENTRY_MIN_CONFIRMATIONS", "2"))
ENTRY_CONFIRMATION_WEIGHT_THRESHOLD = float(os.getenv("ENTRY_CONFIRMATION_WEIGHT_THRESHOLD", "0.60"))
ENABLE_PATTERN_OVERLAY = os.getenv("ENABLE_PATTERN_OVERLAY", "false").lower() == "true"
PATTERN_OVERLAY_MODE = os.getenv("PATTERN_OVERLAY_MODE", "weighted").strip().lower()
PATTERN_WEIGHT = float(os.getenv("PATTERN_WEIGHT", "0.08"))

# Keep indicator parameters minimal to reduce overfitting risk.
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
TREND_MA_PERIOD = 50

# ── Hard Risk Rails (balance-aware) ─────────────────────────────────────────
MAX_POSITION_PCT_OF_EQUITY = float(os.getenv("MAX_POSITION_PCT_OF_EQUITY", "0.20"))
MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY", "0.75"))
MIN_CASH_RESERVE_PCT = float(os.getenv("MIN_CASH_RESERVE_PCT", "0.10"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.02"))   # 2% daily drawdown halt
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "10"))
REAL_MONEY_START_BUDGET = float(os.getenv("REAL_MONEY_START_BUDGET", "25.0"))
HARD_MIN_ORDER_NOTIONAL = float(os.getenv("HARD_MIN_ORDER_NOTIONAL", "1.0"))

# Risk per trade: notional sized so a full stop-out = risk_pct × equity
# position_notional = equity × risk_pct / |stop_loss_pct|
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.01"))   # 1% of equity max
MIN_RISK_PER_TRADE_PCT = float(os.getenv("MIN_RISK_PER_TRADE_PCT", "0.005"))  # 0.5% of equity min
POSITION_SIZE_PCT_OF_EQUITY = float(os.getenv("POSITION_SIZE_PCT_OF_EQUITY", "0.03"))
MAX_SECTOR_CONCENTRATION_PCT = float(os.getenv("MAX_SECTOR_CONCENTRATION_PCT", "0.40"))
MAX_TOTAL_OPEN_RISK_PCT = float(os.getenv("MAX_TOTAL_OPEN_RISK_PCT", "0.02"))

# Hard cap on open positions at any one time
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))

# Allowed tickers for new entries (comma-separated). Empty = no restriction beyond WATCHLIST.
_ALLOWED_TICKERS_ENV = os.getenv("ALLOWED_TICKERS", "")
ALLOWED_TICKERS: set[str] = (
    {t.strip().upper() for t in _ALLOWED_TICKERS_ENV.split(",") if t.strip()}
    if _ALLOWED_TICKERS_ENV.strip()
    else set()
)

# Trading hours (US/Eastern, "HH:MM" 24-h). New entries blocked outside this window.
TRADING_START_TIME = os.getenv("TRADING_START_TIME", "09:30")   # inclusive
TRADING_END_TIME   = os.getenv("TRADING_END_TIME",   "15:30")   # exclusive (30 min before close)

# High-volatility blackout windows: comma-separated "HH:MM-HH:MM" in US/Eastern.
# Default: first 15 min after open + last 30 min before close.
NO_TRADE_WINDOWS_RAW = os.getenv("NO_TRADE_WINDOWS", "09:30-09:45,15:30-16:00")
_NO_TRADE_WINDOWS: list[tuple[str, str]] = [
    (p.split("-", 1)[0].strip(), p.split("-", 1)[1].strip())
    for p in NO_TRADE_WINDOWS_RAW.split(",")
    if "-" in p.strip()
]

FORCED_BUY_RAN = False

# ── Optimization Agents ──────────────────────────────────────────────────────
tpsl_agent = DynamicTPSLAgent(default_tp=TAKE_PROFIT_PCT, default_sl=STOP_LOSS_PCT)
risk_agent = RiskAnalysisAgent()
entry_quality_agent = EntryQualityAgent()
analysis_agent = TradeAnalysisAgent()
ensemble_agent = EnsembleTradingAgent()


def get_account_snapshot() -> dict:
    """Fetch account-level balance fields used by risk rails."""
    try:
        account = api.get_account()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        cash = float(getattr(account, "cash", 0.0) or 0.0)
        buying_power = float(getattr(account, "buying_power", 0.0) or 0.0)
        return {
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "paper": PAPER_TRADING,
        }
    except Exception as e:
        print(f"⚠️  Could not fetch account snapshot: {e}")
        return {
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "paper": PAPER_TRADING,
        }


def validate_runtime_config() -> tuple[bool, list[str]]:
    """Validate required keys and critical config ranges for production safety."""
    issues = []

    key_fields = {
        "ALPACA_API_KEY": API_KEY,
        "ALPACA_SECRET_KEY": SECRET_KEY,
        "FINNHUB_API_KEY": FINNHUB_KEY,
    }
    for name, value in key_fields.items():
        if not value or "your_" in value.lower() or value.lower() in {"changeme", "replace_me"}:
            issues.append(f"Missing or placeholder value for {name}.")

    if not (0 < MAX_POSITION_PCT_OF_EQUITY <= 1):
        issues.append("MAX_POSITION_PCT_OF_EQUITY must be in (0, 1].")
    if not (0 < MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY <= 1):
        issues.append("MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY must be in (0, 1].")
    if MAX_POSITION_PCT_OF_EQUITY > MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY:
        issues.append("MAX_POSITION_PCT_OF_EQUITY cannot exceed MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY.")
    if not (0 <= MIN_CASH_RESERVE_PCT < 1):
        issues.append("MIN_CASH_RESERVE_PCT must be in [0, 1).")
    if not (0 < DAILY_LOSS_LIMIT_PCT <= 0.5):
        issues.append("DAILY_LOSS_LIMIT_PCT must be in (0, 0.5].")
    if MAX_TRADES_PER_DAY < 1:
        issues.append("MAX_TRADES_PER_DAY must be >= 1.")
    if HARD_MIN_ORDER_NOTIONAL <= 0:
        issues.append("HARD_MIN_ORDER_NOTIONAL must be > 0.")
    if REAL_MONEY_START_BUDGET <= 0:
        issues.append("REAL_MONEY_START_BUDGET must be > 0.")
    if AUTO_START_MODE not in {"interactive", "autonomous", "run_once"}:
        issues.append("AUTO_START_MODE must be one of: interactive, autonomous, run_once.")
    if ENTRY_CONFIRMATION_MODE not in {"boolean", "weighted"}:
        issues.append("ENTRY_CONFIRMATION_MODE must be one of: boolean, weighted.")
    if not (1 <= ENTRY_MIN_CONFIRMATIONS <= 3):
        issues.append("ENTRY_MIN_CONFIRMATIONS must be in [1, 3].")
    if not (0 < ENTRY_CONFIRMATION_WEIGHT_THRESHOLD <= 1):
        issues.append("ENTRY_CONFIRMATION_WEIGHT_THRESHOLD must be in (0, 1].")
    if PATTERN_OVERLAY_MODE not in {"weighted", "confirm"}:
        issues.append("PATTERN_OVERLAY_MODE must be one of: weighted, confirm.")
    if not (0 < PATTERN_WEIGHT <= 1):
        issues.append("PATTERN_WEIGHT must be in (0, 1].")
    if not (0 < MAX_RISK_PER_TRADE_PCT <= 0.05):
        issues.append("MAX_RISK_PER_TRADE_PCT must be in (0, 0.05].")
    if not (0 < MIN_RISK_PER_TRADE_PCT <= MAX_RISK_PER_TRADE_PCT):
        issues.append("MIN_RISK_PER_TRADE_PCT must be in (0, MAX_RISK_PER_TRADE_PCT].")
    if not (0 < POSITION_SIZE_PCT_OF_EQUITY <= 1):
        issues.append("POSITION_SIZE_PCT_OF_EQUITY must be in (0, 1].")
    if not (0 < MAX_SECTOR_CONCENTRATION_PCT <= 1):
        issues.append("MAX_SECTOR_CONCENTRATION_PCT must be in (0, 1].")
    if not (0 < MAX_TOTAL_OPEN_RISK_PCT <= 1):
        issues.append("MAX_TOTAL_OPEN_RISK_PCT must be in (0, 1].")
    if MAX_CONCURRENT_POSITIONS < 1:
        issues.append("MAX_CONCURRENT_POSITIONS must be >= 1.")
    if not (5 <= len(WATCHLIST) <= 20):
        issues.append("Ticker universe should contain 5-20 symbols for Week 4 diversification.")

    return len(issues) == 0, issues


def connectivity_preflight() -> tuple[bool, list[str]]:
    """Check core external dependencies before autonomous runtime."""
    issues = []

    try:
        api.get_account()
        api.get_clock()
    except Exception as e:
        issues.append(f"Alpaca connectivity failed: {e}")

    try:
        url = f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={FINNHUB_KEY}"
        resp = REQUESTS_SESSION.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            issues.append(f"Finnhub HTTP status {resp.status_code}")
        else:
            payload = resp.json()
            if not isinstance(payload, dict):
                issues.append("Finnhub returned non-JSON payload")
    except Exception as e:
        issues.append(f"Finnhub connectivity failed: {e}")

    return len(issues) == 0, issues


def get_effective_budget(account_snapshot: dict) -> tuple[float, float]:
    """Compute effective equity/cash budget with real-money startup cap."""
    equity = float(account_snapshot.get("equity", 0.0) or 0.0)
    cash = float(account_snapshot.get("cash", 0.0) or 0.0)

    if PAPER_TRADING:
        return equity, cash

    # For early live rollout, cap usable capital to the small startup budget.
    capped_equity = min(equity, REAL_MONEY_START_BUDGET)
    capped_cash = min(cash, REAL_MONEY_START_BUDGET)
    return capped_equity, capped_cash


def get_open_exposure_value() -> float:
    """Total market value of open positions."""
    try:
        positions = api.get_all_positions()
        return float(sum(float(p.market_value) for p in positions))
    except Exception:
        return 0.0


def get_open_position_count() -> int:
    """Return the number of currently open positions."""
    try:
        return len(api.get_all_positions())
    except Exception:
        return 0


def is_within_trading_hours() -> tuple[bool, str]:
    """Return (allowed, reason). Blocks new entries outside hours or in no-trade windows."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return True, "ok"  # zoneinfo unavailable — allow and rely on Alpaca clock
    hhmm = now_et.strftime("%H:%M")
    if hhmm < TRADING_START_TIME or hhmm >= TRADING_END_TIME:
        return False, (
            f"Outside trading hours ({hhmm} ET; "
            f"window {TRADING_START_TIME}\u2013{TRADING_END_TIME})"
        )
    for w_start, w_end in _NO_TRADE_WINDOWS:
        if w_start <= hhmm < w_end:
            return False, f"No-trade window active ({hhmm} ET; blocked {w_start}\u2013{w_end})"
    return True, "ok"


def get_today_trade_count() -> int:
    """Count entry trades logged today (UTC)."""
    today = datetime.now(UTC).date().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM trade_entries
            WHERE substr(timestamp, 1, 10) = ?
            """,
            (today,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_today_realized_pnl_pct(effective_equity: float) -> float:
    """Compute today's total drawdown % (realized closed trades + unrealized open positions)."""
    if effective_equity <= 0:
        return 0.0

    today = datetime.now(UTC).date()

    # Realized PnL from trades closed today
    realized_usd = 0.0
    try:
        trades = analysis_agent.get_closed_trades(lookback_days=3)
        for t in trades:
            try:
                ts = datetime.fromisoformat(t.timestamp)
            except Exception:
                continue
            if ts.date() != today:
                continue
            realized_usd += (float(t.exit_price) - float(t.entry_price)) * float(t.qty)
    except Exception:
        pass

    # Unrealized PnL from currently open positions
    unrealized_usd = 0.0
    try:
        for p in api.get_all_positions():
            unrealized_usd += float(getattr(p, "unrealized_pl", 0.0) or 0.0)
    except Exception:
        pass

    return ((realized_usd + unrealized_usd) / effective_equity) * 100.0


def get_open_portfolio_risk_snapshot(effective_equity: float) -> dict:
    """Compute sector concentration and estimated open stop-out risk of current positions."""
    if effective_equity <= 0:
        return {
            "total_open_risk_pct": 0.0,
            "sector_exposure_pct": {},
        }

    total_open_risk_pct = 0.0
    sector_exposure: dict[str, float] = {}

    try:
        positions = api.get_all_positions()
    except Exception:
        positions = []

    for p in positions:
        symbol = getattr(p, "symbol", "").upper()
        market_value = abs(float(getattr(p, "market_value", 0.0) or 0.0))
        if market_value <= 0:
            continue

        sector = TICKER_SECTOR_MAP.get(symbol, "unknown")
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + (market_value / effective_equity)

        plan = get_latest_entry_risk_plan(symbol)
        sl_pct = abs(float(plan.get("sl_pct", STOP_LOSS_PCT))) / 100.0
        total_open_risk_pct += (market_value / effective_equity) * sl_pct

    return {
        "total_open_risk_pct": total_open_risk_pct,
        "sector_exposure_pct": sector_exposure,
    }


def evaluate_risk_rails_for_buy(ticker: str, proposed_notional: float) -> dict:
    """Hard-gate buy orders based on account balance and budget limits."""
    account_snapshot = get_account_snapshot()
    effective_equity, effective_cash = get_effective_budget(account_snapshot)

    if effective_equity <= 0 or effective_cash <= 0:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": "No effective equity/cash available.",
        }

    # ── Allowed tickers gate ─────────────────────────────────────────────────
    if ALLOWED_TICKERS and ticker.upper() not in ALLOWED_TICKERS:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": f"{ticker} is not in the ALLOWED_TICKERS list.",
        }

    # ── Trading hours + no-trade window gate ─────────────────────────────────
    hours_ok, hours_reason = is_within_trading_hours()
    if not hours_ok:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": hours_reason,
        }

    # ── Max concurrent positions ─────────────────────────────────────────────
    open_count = get_open_position_count()
    if open_count >= MAX_CONCURRENT_POSITIONS:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": f"Max concurrent positions reached ({open_count}/{MAX_CONCURRENT_POSITIONS}).",
        }

    today_trade_count = get_today_trade_count()
    if today_trade_count >= MAX_TRADES_PER_DAY:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": f"Max trades/day reached ({MAX_TRADES_PER_DAY}).",
        }

    today_realized_pnl_pct = get_today_realized_pnl_pct(effective_equity)
    if today_realized_pnl_pct <= -(DAILY_LOSS_LIMIT_PCT * 100):
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": (
                f"Daily drawdown limit hit \u2014 bot halted for the day "
                f"({today_realized_pnl_pct:.2f}% <= -{DAILY_LOSS_LIMIT_PCT * 100:.2f}%)."
            ),
        }

    open_exposure = get_open_exposure_value()
    max_total_exposure = effective_equity * MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY
    if open_exposure >= max_total_exposure:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": "Total exposure cap reached.",
        }

    portfolio_risk = get_open_portfolio_risk_snapshot(effective_equity)
    sector = TICKER_SECTOR_MAP.get(ticker.upper(), "unknown")
    sector_exposure = float((portfolio_risk.get("sector_exposure_pct") or {}).get(sector, 0.0))
    projected_sector_exposure = sector_exposure + (float(proposed_notional) / effective_equity)
    if projected_sector_exposure > MAX_SECTOR_CONCENTRATION_PCT:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": (
                f"Sector concentration limit hit for {sector} "
                f"({projected_sector_exposure * 100:.2f}% > {MAX_SECTOR_CONCENTRATION_PCT * 100:.2f}%)."
            ),
        }

    per_trade_risk_pct = (abs(STOP_LOSS_PCT) / 100.0) if abs(STOP_LOSS_PCT) > 0 else 0.0
    projected_total_open_risk_pct = float(portfolio_risk.get("total_open_risk_pct", 0.0))
    projected_total_open_risk_pct += (float(proposed_notional) / effective_equity) * per_trade_risk_pct
    if projected_total_open_risk_pct > MAX_TOTAL_OPEN_RISK_PCT:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": (
                f"Portfolio open-risk cap reached "
                f"({projected_total_open_risk_pct * 100:.2f}% > {MAX_TOTAL_OPEN_RISK_PCT * 100:.2f}%)."
            ),
        }

    max_position_size = effective_equity * MAX_POSITION_PCT_OF_EQUITY
    remaining_exposure = max_total_exposure - open_exposure
    cash_reserve = effective_equity * MIN_CASH_RESERVE_PCT
    spendable_cash = max(0.0, effective_cash - cash_reserve)

    # Risk-per-trade cap: a full stop-out must not exceed MAX_RISK_PER_TRADE_PCT of equity
    if abs(STOP_LOSS_PCT) > 0:
        risk_cap = (effective_equity * MAX_RISK_PER_TRADE_PCT) / (abs(STOP_LOSS_PCT) / 100.0)
        proposed_notional = min(float(proposed_notional), risk_cap)

    approved = min(float(proposed_notional), max_position_size, remaining_exposure, spendable_cash)
    if approved < HARD_MIN_ORDER_NOTIONAL:
        return {
            "allowed": False,
            "approved_notional": 0.0,
            "reason": "Approved notional below minimum order threshold.",
        }

    return {
        "allowed": True,
        "approved_notional": round(approved, 2),
        "reason": "approved",
        "effective_equity": round(effective_equity, 2),
        "effective_cash": round(effective_cash, 2),
        "open_exposure": round(open_exposure, 2),
        "sector": sector,
        "projected_sector_exposure_pct": round(projected_sector_exposure * 100, 2),
        "projected_total_open_risk_pct": round(projected_total_open_risk_pct * 100, 2),
    }


def maybe_run_weekly_reweight(force: bool = False) -> bool:
    """Run walk-forward out-of-sample reweighting weekly (or when forced)."""
    if not ENABLE_WEEKLY_AUTO_REWEIGHT and not force:
        return False

    state = get_ensemble_weight_state()
    last_date = state.get("last_reweight_date")
    is_due = force

    if not is_due:
        if not last_date:
            is_due = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_date)
                is_due = (datetime.now(UTC) - last_dt).days >= 7
            except Exception:
                is_due = True

    if not is_due:
        return False

    print("🧠 Running weekly walk-forward backtest for ensemble reweighting...")
    harness = PaperBacktestHarness(WATCHLIST)
    result = harness.reweight_and_persist(
        lookback_weeks=BACKTEST_LOOKBACK_WEEKS,
        oos_weeks=BACKTEST_OOS_WEEKS,
    )
    ensemble_agent.reload_weights()
    print(f"✅ Ensemble weights updated: {result.get('weights', {})}")
    return True

# ──────────────────────────────────────────────────────────────────────────────
# STEP A: Check Finnhub for recent politician trades on a given ticker
# ──────────────────────────────────────────────────────────────────────────────
def get_politician_buys_for_ticker(ticker):
    try:
        date_from = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        date_to   = datetime.now().strftime("%Y-%m-%d")

        url = (
            f"https://finnhub.io/api/v1/stock/congressional-trading"
            f"?symbol={ticker}&from={date_from}&to={date_to}&token={FINNHUB_KEY}"
        )
        response = REQUESTS_SESSION.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        data = response.json()

        trades = data.get("data", [])
        buys = [t for t in trades if t.get("transactionType", "").lower() == "buy"]
        return buys

    except Exception as e:
        print(f"  ⚠️  Could not fetch trades for {ticker}: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# STEP B: Check if a stock has positive momentum (price higher than 5 days ago)
# ──────────────────────────────────────────────────────────────────────────────
def check_momentum(ticker):
    try:
        data = yf.download(ticker, period="10d", interval="1d", progress=False)
        if data.empty or len(data) < 5:
            return False

        close_col = data["Close"]
        if hasattr(close_col, "ndim") and close_col.ndim == 2:
            close_col = close_col.iloc[:, 0]
        price_today  = float(close_col.iloc[-1])
        price_5d_ago = float(close_col.iloc[-5])
        momentum = (price_today - price_5d_ago) / price_5d_ago * 100

        print(f"  📈 {ticker} momentum: {momentum:.2f}%")
        return momentum > 0
    except Exception as e:
        print(f"  ⚠️  Could not get price data for {ticker}: {e}")
        return False


def _ema(series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close, period: int = RSI_PERIOD):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close):
    macd_line = _ema(close, MACD_FAST) - _ema(close, MACD_SLOW)
    signal = _ema(macd_line, MACD_SIGNAL)
    hist = macd_line - signal
    return macd_line, signal, hist


def _bollinger(close):
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    upper = mid + BB_STD * std
    lower = mid - BB_STD * std
    return lower, mid, upper


def evaluate_entry_confirmations(ticker: str) -> dict:
    """Evaluate trend + MACD + RSI/BB confirmations used by confirmed backtest model."""
    if not ENABLE_CONFIRMED_ENTRY_MODEL:
        return {
            "passed": True,
            "mode": "disabled",
            "confirmations": 0,
            "weighted_score": 0.0,
            "trend": False,
            "macd": False,
            "mean_reversion": False,
            "reason": "confirmed model disabled",
        }

    try:
        data = yf.download(ticker, period="18mo", interval="1d", progress=False)
        min_bars = max(TREND_MA_PERIOD + 6, BB_PERIOD + 2)
        if data.empty or len(data) < min_bars:
            return {
                "passed": False,
                "mode": ENTRY_CONFIRMATION_MODE,
                "confirmations": 0,
                "weighted_score": 0.0,
                "trend": False,
                "macd": False,
                "mean_reversion": False,
                "reason": "insufficient history",
            }

        close_col = data["Close"]
        if hasattr(close_col, "ndim") and close_col.ndim == 2:
            close_col = close_col.iloc[:, 0]
        close = close_col.astype(float).dropna()
        if len(close) < min_bars:
            return {
                "passed": False,
                "mode": ENTRY_CONFIRMATION_MODE,
                "confirmations": 0,
                "weighted_score": 0.0,
                "trend": False,
                "macd": False,
                "mean_reversion": False,
                "reason": "insufficient close bars",
            }

        trend_ma = close.rolling(TREND_MA_PERIOD).mean()
        trend_ok = bool((trend_ma.iloc[-1] > trend_ma.iloc[-6]) and (close.iloc[-1] > trend_ma.iloc[-1]))

        macd_line, macd_signal, macd_hist = _macd(close)
        macd_ok = bool((macd_line.iloc[-1] > macd_signal.iloc[-1]) and (macd_hist.iloc[-1] > 0))

        rsi = _rsi(close, RSI_PERIOD)
        bb_lower, bb_mid, _ = _bollinger(close)
        mr_ok = bool(
            ((rsi.iloc[-1] < 45) or ((close.iloc[-1] <= bb_mid.iloc[-1]) and (rsi.iloc[-1] < 55)))
            and (close.iloc[-1] >= bb_lower.iloc[-1] * 0.98)
        )

        confirmations = int(trend_ok) + int(macd_ok) + int(mr_ok)
        weighted_score = (float(trend_ok) * 0.40) + (float(macd_ok) * 0.35) + (float(mr_ok) * 0.25)

        if ENTRY_CONFIRMATION_MODE == "weighted":
            passed = weighted_score >= ENTRY_CONFIRMATION_WEIGHT_THRESHOLD
            reason = f"weighted={weighted_score:.2f} (min {ENTRY_CONFIRMATION_WEIGHT_THRESHOLD:.2f})"
        else:
            passed = confirmations >= ENTRY_MIN_CONFIRMATIONS
            reason = f"confirmations={confirmations}/3 (min {ENTRY_MIN_CONFIRMATIONS})"

        return {
            "passed": bool(passed),
            "mode": ENTRY_CONFIRMATION_MODE,
            "confirmations": confirmations,
            "weighted_score": weighted_score,
            "trend": trend_ok,
            "macd": macd_ok,
            "mean_reversion": mr_ok,
            "reason": reason,
        }
    except Exception as e:
        return {
            "passed": False,
            "mode": ENTRY_CONFIRMATION_MODE,
            "confirmations": 0,
            "weighted_score": 0.0,
            "trend": False,
            "macd": False,
            "mean_reversion": False,
            "reason": f"confirmation error: {e}",
        }


def evaluate_pattern_overlay(ticker: str) -> dict:
    """Evaluate OHLC chart patterns for optional live entry overlay."""
    if not ENABLE_PATTERN_OVERLAY:
        return {
            "enabled": False,
            "passed": True,
            "bull": False,
            "bear": False,
            "bull_score": 0.0,
            "bear_score": 0.0,
            "reason": "disabled",
        }

    try:
        data = yf.download(ticker, period="18mo", interval="1d", progress=False)
        if data.empty or len(data) < 80:
            return {
                "enabled": True,
                "passed": False,
                "bull": False,
                "bear": False,
                "bull_score": 0.0,
                "bear_score": 0.0,
                "reason": "insufficient history",
            }

        if isinstance(data.columns, np.ndarray):
            pass
        close_block = data[["Open", "High", "Low", "Close"]]
        if hasattr(close_block["Close"], "ndim") and close_block["Close"].ndim == 2:
            close_block = pd.DataFrame(
                {
                    "Open": close_block["Open"].iloc[:, 0],
                    "High": close_block["High"].iloc[:, 0],
                    "Low": close_block["Low"].iloc[:, 0],
                    "Close": close_block["Close"].iloc[:, 0],
                },
                index=close_block.index,
            )

        pat = detect_ohlc_patterns(close_block)
        bull = bool(pat["pattern_bull"].iloc[-1] > 0)
        bear = bool(pat["pattern_bear"].iloc[-1] > 0)
        bull_score = float(pat["pattern_bull_score"].iloc[-1])
        bear_score = float(pat["pattern_bear_score"].iloc[-1])

        if PATTERN_OVERLAY_MODE == "confirm":
            passed = bull and not bear
            reason = f"confirm bull={bull} bear={bear}"
        else:
            passed = True
            reason = f"weighted bull={bull_score:.2f} bear={bear_score:.2f}"

        return {
            "enabled": True,
            "passed": passed,
            "bull": bull,
            "bear": bear,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "reason": reason,
        }
    except Exception as e:
        return {
            "enabled": True,
            "passed": False,
            "bull": False,
            "bear": False,
            "bull_score": 0.0,
            "bear_score": 0.0,
            "reason": f"pattern error: {e}",
        }


def apply_pattern_overlay_to_decision(decision: dict, pattern: dict) -> dict:
    """Apply weighted pattern score to ensemble decision when enabled."""
    out = dict(decision)
    out["base_buy_score"] = float(decision.get("buy_score", 0.0))
    out["base_sell_score"] = float(decision.get("sell_score", 0.0))

    if not ENABLE_PATTERN_OVERLAY:
        return out

    if PATTERN_OVERLAY_MODE == "weighted":
        buy_score = out["base_buy_score"] + float(pattern.get("bull_score", 0.0)) * PATTERN_WEIGHT
        sell_score = out["base_sell_score"] + float(pattern.get("bear_score", 0.0)) * PATTERN_WEIGHT
        out["buy_score"] = round(buy_score, 3)
        out["sell_score"] = round(sell_score, 3)
        if buy_score >= sell_score and buy_score >= 0.55:
            out["action"] = "buy"
        elif sell_score > buy_score and sell_score >= 0.55:
            out["action"] = "sell"
        else:
            out["action"] = "hold"
    return out


def evaluate_entry_decision(ticker: str, has_politician_buy: bool) -> dict:
    """Blend external event signals with ensemble strategy outputs."""
    external_bias = EXTERNAL_POLITICIAN_BIAS if has_politician_buy else 0.0
    decision = ensemble_agent.evaluate_ticker(ticker, external_buy_bias=external_bias)
    return decision


# ──────────────────────────────────────────────────────────────────────────────
# STEP C: Place a buy order on Alpaca
# ──────────────────────────────────────────────────────────────────────────────
def place_buy_order(ticker, notional=None, signal_source="politician_bot"):
    # Use optimized position size if not specified
    if notional is None:
        notional = get_optimized_position_size(ticker, DEFAULT_BUY_NOTIONAL)
    requested_notional = float(notional)

    risk_gate = evaluate_risk_rails_for_buy(ticker, notional)
    if not risk_gate.get("allowed"):
        print(f"  🛑 Risk rails blocked buy for {ticker}: {risk_gate.get('reason')}")
        return

    approved_notional = float(risk_gate.get("approved_notional", requested_notional))
    if approved_notional < notional:
        print(f"  🧷 Notional adjusted by risk rails: ${notional:.2f} -> ${approved_notional:.2f}")
    notional = approved_notional

    try:
        positions = api.get_all_positions()
        owned = [p.symbol for p in positions]
        if ticker in owned:
            print(f"  ⏭️  Already own {ticker}, skipping")
            return

        order = MarketOrderRequest(
            symbol=ticker,
            notional=notional,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        placed = api.submit_order(order)
        print(f"  ✅ ORDER PLACED: Bought ${notional:.2f} of {ticker}")

        entry_price = None
        qty = None

        if getattr(placed, "filled_avg_price", None):
            entry_price = float(placed.filled_avg_price)
        if getattr(placed, "filled_qty", None):
            qty = float(placed.filled_qty)
        if qty is None and getattr(placed, "qty", None):
            qty = float(placed.qty)

        if entry_price is None:
            try:
                data = yf.download(ticker, period="1d", interval="1m", progress=False)
                if not data.empty:
                    close_col = data["Close"]
                    if hasattr(close_col, "ndim") and close_col.ndim == 2:
                        close_col = close_col.iloc[:, 0]
                    entry_price = float(close_col.iloc[-1])
            except Exception:
                entry_price = None

        if entry_price is None:
            entry_price = 0.0
        if qty is None:
            qty = (float(notional) / entry_price) if entry_price > 0 else 0.0

        trade_tp_pct = float(TAKE_PROFIT_PCT)
        trade_sl_pct = float(STOP_LOSS_PCT)
        tp_price = None
        sl_price = None
        if entry_price > 0:
            tp_price = entry_price * (1.0 + trade_tp_pct / 100.0)
            sl_price = entry_price * (1.0 + trade_sl_pct / 100.0)

        entry_reason = (
            f"signal={signal_source}; requested=${requested_notional:.2f}; "
            f"approved=${notional:.2f}; tp={trade_tp_pct:.2f}%; sl={trade_sl_pct:.2f}%"
        )

        row_id = log_entry(
            ticker=ticker,
            entry_price=entry_price,
            qty=qty,
            tp_pct=trade_tp_pct,
            sl_pct=trade_sl_pct,
            tp_price=tp_price,
            sl_price=sl_price,
            requested_notional=requested_notional,
            approved_notional=notional,
            entry_reason=entry_reason,
            signal_source=signal_source,
            order_id=str(getattr(placed, "id", "")) or None,
            timestamp=datetime.now(UTC),
        )
        if tp_price is not None and sl_price is not None:
            print(
                f"  🧾 Logged entry #{row_id} for {ticker} "
                f"(size {qty:.4f}, TP {tp_price:.2f}, SL {sl_price:.2f})"
            )
        else:
            print(f"  🧾 Logged entry #{row_id} for {ticker} (size {qty:.4f})")

    except Exception as e:
        print(f"  ❌ Order failed for {ticker}: {e}")


def place_sell_order(ticker: str, qty: float, signal_source: str = "ensemble_exit") -> bool:
    """Submit a sell order and return True on successful submit."""
    try:
        order = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        api.submit_order(order)
        print(f"  💸 SELL ORDER: {ticker} qty {qty} via {signal_source}")
        return True
    except Exception as e:
        print(f"  ❌ SELL failed for {ticker}: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# STEP D: Close positions on TP/SL and log closed trades
# ──────────────────────────────────────────────────────────────────────────────
def maybe_close_and_log_positions():
    try:
        positions = api.get_all_positions()
    except Exception as e:
        print(f"⚠️  Could not fetch open positions: {e}")
        return

    if not positions:
        print("📂 No open positions to evaluate for exits.")
        return

    print(f"📊 Evaluating {len(positions)} open position(s) for TP/SL exits...")
    for pos in positions:
        try:
            ticker = pos.symbol
            entry_price = float(pos.avg_entry_price)
            current_price = float(pos.current_price)
            qty = float(pos.qty)
            pnl_percent = ((current_price - entry_price) / entry_price) * 100

            trade_plan = get_latest_entry_risk_plan(ticker)
            tp_pct = float(trade_plan.get("tp_pct", TAKE_PROFIT_PCT))
            sl_pct = float(trade_plan.get("sl_pct", STOP_LOSS_PCT))

            ensemble_decision = ensemble_agent.evaluate_ticker(ticker)
            ensemble_sell = (
                ensemble_decision["action"] == "sell"
                and ensemble_decision["confidence"] >= MIN_ENSEMBLE_CONFIDENCE
            )

            should_close = pnl_percent >= tp_pct or pnl_percent <= sl_pct
            should_close = should_close or ensemble_sell
            if not should_close:
                print(
                    f"  ⏳ {ticker} pnl {pnl_percent:.2f}% "
                    f"(holding; TP {tp_pct:.2f}% / SL {sl_pct:.2f}%)"
                )
                continue

            exit_source = "tp_sl_exit"
            if ensemble_sell:
                exit_source = "ensemble_exit"

            sold = place_sell_order(ticker, qty=qty, signal_source=exit_source)
            if not sold:
                continue

            print(f"  💸 EXIT EXECUTED: {ticker} at ~{current_price:.2f} ({pnl_percent:.2f}%)")

            row_id = log_trade(
                ticker=ticker,
                entry_price=entry_price,
                exit_price=current_price,
                qty=qty,
                signal_source=exit_source,
                timestamp=datetime.now(UTC),
            )
            print(f"  🧾 Logged closed trade #{row_id} for {ticker}")
        except Exception as e:
            print(f"  ⚠️  Failed to close/log {getattr(pos, 'symbol', '?')}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# OPTIMIZATION FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────
def update_dynamic_parameters():
    """Update TP/SL levels based on historical performance"""
    global TAKE_PROFIT_PCT, STOP_LOSS_PCT

    if not ENABLE_DYNAMIC_OPTIMIZATION:
        return

    try:
        recommendations = tpsl_agent.recommend_tp_sl(OPTIMIZATION_LOOKBACK_DAYS)

        tp_rec = recommendations["take_profit"]
        sl_rec = recommendations["stop_loss"]

        if tp_rec.confidence > 0.5:
            old_tp = TAKE_PROFIT_PCT
            TAKE_PROFIT_PCT = tp_rec.recommended_value
            print(f"🔄 TP updated: {old_tp}% → {TAKE_PROFIT_PCT}% (confidence: {tp_rec.confidence * 100:.0f}%)")

        if sl_rec.confidence > 0.5:
            old_sl = STOP_LOSS_PCT
            STOP_LOSS_PCT = sl_rec.recommended_value
            print(f"🔄 SL updated: {old_sl}% → {STOP_LOSS_PCT}% (confidence: {sl_rec.confidence * 100:.0f}%)")

    except Exception as e:
        print(f"⚠️  Could not update dynamic parameters: {e}")


def get_optimized_position_size(ticker: str, base_notional: float = 100.0) -> float:
    """Position size via % equity, bounded by configured risk-per-trade stop-out limits."""
    account_snapshot = get_account_snapshot()
    effective_equity, _ = get_effective_budget(account_snapshot)

    if effective_equity <= 0:
        return base_notional

    pct_based = effective_equity * POSITION_SIZE_PCT_OF_EQUITY
    if abs(STOP_LOSS_PCT) <= 0:
        return pct_based

    risk_floor = (effective_equity * MIN_RISK_PER_TRADE_PCT) / (abs(STOP_LOSS_PCT) / 100.0)
    risk_ceiling = (effective_equity * MAX_RISK_PER_TRADE_PCT) / (abs(STOP_LOSS_PCT) / 100.0)
    return max(risk_floor, min(pct_based, risk_ceiling))


def get_latest_entry_risk_plan(ticker: str) -> dict:
    """Fetch latest logged SL/TP plan for a ticker; fall back to current globals."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tp_pct, sl_pct, tp_price, sl_price, approved_notional, entry_reason
            FROM trade_entries
            WHERE ticker = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (ticker.upper(),),
        )
        row = cur.fetchone()
        if not row:
            return {
                "tp_pct": TAKE_PROFIT_PCT,
                "sl_pct": STOP_LOSS_PCT,
                "tp_price": None,
                "sl_price": None,
                "approved_notional": None,
                "entry_reason": "fallback-defaults",
            }

        return {
            "tp_pct": float(row[0]) if row[0] is not None else TAKE_PROFIT_PCT,
            "sl_pct": float(row[1]) if row[1] is not None else STOP_LOSS_PCT,
            "tp_price": float(row[2]) if row[2] is not None else None,
            "sl_price": float(row[3]) if row[3] is not None else None,
            "approved_notional": float(row[4]) if row[4] is not None else None,
            "entry_reason": row[5] if row[5] is not None else "",
        }
    except Exception:
        return {
            "tp_pct": TAKE_PROFIT_PCT,
            "sl_pct": STOP_LOSS_PCT,
            "tp_price": None,
            "sl_price": None,
            "approved_notional": None,
            "entry_reason": "fallback-defaults",
        }
    finally:
        conn.close()


def print_optimization_metrics():
    """Print current optimization metrics"""
    print("\n" + "=" * 60)
    print("📊 CURRENT OPTIMIZATION SETTINGS")
    print("=" * 60)
    print(f"  Take Profit: {TAKE_PROFIT_PCT}%")
    print(f"  Stop Loss: {STOP_LOSS_PCT}%")
    print(f"  Default Position Size: ${DEFAULT_BUY_NOTIONAL}")
    print(f"  Weekly Auto-Reweight: {ENABLE_WEEKLY_AUTO_REWEIGHT}")
    print(f"  Max Position % Equity: {MAX_POSITION_PCT_OF_EQUITY * 100:.1f}%")
    print(f"  Max Total Exposure % Equity: {MAX_TOTAL_EXPOSURE_PCT_OF_EQUITY * 100:.1f}%")
    print(f"  Daily Drawdown Halt: {DAILY_LOSS_LIMIT_PCT * 100:.1f}%")
    print(f"  Max Trades/Day: {MAX_TRADES_PER_DAY}")
    print(f"  Position Size % Equity: {POSITION_SIZE_PCT_OF_EQUITY * 100:.2f}%")
    print(f"  Max Risk/Trade: {MIN_RISK_PER_TRADE_PCT * 100:.2f}%\u2013{MAX_RISK_PER_TRADE_PCT * 100:.2f}% of equity")
    print(f"  Max Concurrent Positions: {MAX_CONCURRENT_POSITIONS}")
    print(f"  Max Sector Concentration: {MAX_SECTOR_CONCENTRATION_PCT * 100:.1f}%")
    print(f"  Max Total Open Risk: {MAX_TOTAL_OPEN_RISK_PCT * 100:.1f}%")
    print(f"  Universe Size: {len(WATCHLIST)} tickers")
    allowed_str = ", ".join(sorted(ALLOWED_TICKERS)) if ALLOWED_TICKERS else "all (unrestricted)"
    print(f"  Allowed Tickers: {allowed_str}")
    print(f"  Trading Window: {TRADING_START_TIME}\u2013{TRADING_END_TIME} ET")
    no_trade_str = ", ".join(f"{s}\u2013{e}" for s, e in _NO_TRADE_WINDOWS) or "none"
    print(f"  No-Trade Windows: {no_trade_str} ET")
    print(f"  Confirmed Entry Model: {ENABLE_CONFIRMED_ENTRY_MODEL}")
    print(
        f"  Entry Confirmation Mode: {ENTRY_CONFIRMATION_MODE} "
        f"(min={ENTRY_MIN_CONFIRMATIONS}, weight_thr={ENTRY_CONFIRMATION_WEIGHT_THRESHOLD:.2f})"
    )
    print(
        "  Entry Indicators: "
        f"RSI({RSI_PERIOD}), MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}), "
        f"BB({BB_PERIOD},{BB_STD}), MA slope({TREND_MA_PERIOD})"
    )
    print(
        f"  Pattern Overlay: {ENABLE_PATTERN_OVERLAY} "
        f"(mode={PATTERN_OVERLAY_MODE}, weight={PATTERN_WEIGHT:.2f})"
    )

    try:
        weight_state = get_ensemble_weight_state()
        print(f"  Ensemble Weights: {weight_state.get('weights', {})}")
        print(f"  Last Reweight: {weight_state.get('last_reweight_date')}")
    except Exception as e:
        print(f"  ⚠️  Could not fetch ensemble weights: {e}")

    account_snapshot = get_account_snapshot()
    effective_equity, effective_cash = get_effective_budget(account_snapshot)
    print(f"  Account Equity: ${account_snapshot.get('equity', 0.0):.2f}")
    print(f"  Account Cash: ${account_snapshot.get('cash', 0.0):.2f}")
    print(f"  Effective Equity Budget: ${effective_equity:.2f}")
    print(f"  Effective Cash Budget: ${effective_cash:.2f}")

    try:
        trades = analysis_agent.get_closed_trades(OPTIMIZATION_LOOKBACK_DAYS)
        if trades:
            summary = analysis_agent.get_summary(trades)
            print(f"\n  Recent Performance ({OPTIMIZATION_LOOKBACK_DAYS} days):")
            print(f"    Total Trades: {summary['total_trades']}")
            print(f"    Win Rate: {summary['win_rate_pct']}%")
            print(f"    Profit Factor: {summary['profit_factor']}")
    except Exception as e:
        print(f"  ⚠️  Could not fetch trade metrics: {e}")

    print("=" * 60 + "\n")


def run_optimization_debug():
    """Print optimization-oriented diagnostics for tuning and post-run review."""
    print("\n" + "=" * 70)
    print("🧪 OPTIMIZATION DEBUG")
    print("=" * 70)

    snapshot = get_optimization_snapshot(OPTIMIZATION_LOOKBACK_DAYS)
    summary = snapshot.get("summary", {})
    recs = snapshot.get("recommendations", {})

    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    tp = recs.get("take_profit", {})
    sl = recs.get("stop_loss", {})
    print("\nRecommendations:")
    print(
        f"  TP current {tp.get('current')} -> {tp.get('recommended')} "
        f"(conf {tp.get('confidence')})"
    )
    print(
        f"  SL current {sl.get('current')} -> {sl.get('recommended')} "
        f"(conf {sl.get('confidence')})"
    )

    print("\nSignal Source Ranking:")
    for row in snapshot.get("signal_sources", [])[:5]:
        print(
            f"  {row['signal_source']}: count={row['count']} "
            f"win_rate={row['win_rate']} avg_pnl={row['avg_pnl']}"
        )

    try:
        weight_state = get_ensemble_weight_state()
        print("\nEnsemble Weight State:")
        print(f"  updated_at: {weight_state.get('updated_at')}")
        print(f"  last_reweight_date: {weight_state.get('last_reweight_date')}")
        print(f"  weights: {weight_state.get('weights')}")
        backtest = weight_state.get("backtest") or {}
        if backtest:
            print("\nLatest Backtest Agent Scores:")
            for name, score in (backtest.get("agent_scores") or {}).items():
                print(f"  {name}: {score}")
    except Exception as e:
        print(f"  ⚠️  Could not print ensemble weight state: {e}")

    print("=" * 70 + "\n")


def start_autonomous_mode():
    """Run bot as a continuous autonomous scheduler for passive operation."""
    print(
        f"\n🤖 Autonomous mode enabled: scanning every {AUTONOMOUS_INTERVAL_MINUTES} minutes "
        "with weekly walk-forward reweighting."
    )

    # Keep at least one run now so startup is productive.
    run_bot()

    schedule.clear()
    schedule.every(AUTONOMOUS_INTERVAL_MINUTES).minutes.do(run_bot)
    schedule.every().monday.at("08:05").do(lambda: maybe_run_weekly_reweight(force=True))

    try:
        while True:
            schedule.run_pending()
            time.sleep(20)
    except KeyboardInterrupt:
        print("\n⏹️  Autonomous mode stopped.")


# ──────────────────────────────────────────────────────────────────────────────
# STEP E: Main loop
# ──────────────────────────────────────────────────────────────────────────────
def run_bot():
    global FORCED_BUY_RAN

    print(f"\n{'='*50}")
    print(f"🤖 Bot running at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    # Update optimization parameters based on historical performance
    update_dynamic_parameters()

    # Auto-update ensemble weights from walk-forward OOS analysis (weekly cadence)
    maybe_run_weekly_reweight(force=False)

    # Print current optimization metrics
    print_optimization_metrics()

    # Check if market is open
    clock = api.get_clock()
    if not clock.is_open:
        print("🔒 Market is closed right now. Bot will wait.")
        return

    maybe_close_and_log_positions()

    if FORCE_TEST_BUY and not FORCED_BUY_RAN:
        print("🧪 FORCE_TEST_BUY enabled. Placing one controlled test buy...")
        place_buy_order(
            FORCE_TEST_BUY_TICKER,
            notional=FORCE_TEST_BUY_NOTIONAL,
            signal_source="forced_test_buy",
        )
        FORCED_BUY_RAN = True

    print(f"🔍 Scanning {len(WATCHLIST)} tickers for politician activity...\n")

    for ticker in WATCHLIST:
        print(f"--- Checking {ticker} ---")
        buys = get_politician_buys_for_ticker(ticker)
        has_politician_buy = len(buys) > 0
        decision = evaluate_entry_decision(ticker, has_politician_buy)
        pattern = evaluate_pattern_overlay(ticker)
        decision = apply_pattern_overlay_to_decision(decision, pattern)

        if buys:
            print(f"  🏛️  {len(buys)} politician buy(s) found!")
            for b in buys:
                print(f"      → {b.get('name', 'Unknown')} bought on {b.get('transactionDate', '?')}")

        momentum_ok = check_momentum(ticker)
        confirmation = evaluate_entry_confirmations(ticker)
        print(
            "  🧪 Entry confirm: "
            f"trend={confirmation['trend']} macd={confirmation['macd']} "
            f"rsi_bb={confirmation['mean_reversion']} "
            f"count={confirmation['confirmations']}/3 "
            f"weight={confirmation['weighted_score']:.2f} -> {confirmation['reason']}"
        )
        print(
            "  🧩 Pattern overlay: "
            f"enabled={pattern['enabled']} pass={pattern['passed']} "
            f"bull={pattern['bull_score']:.2f} bear={pattern['bear_score']:.2f} "
            f"reason={pattern['reason']}"
        )

        if (
            decision["action"] == "buy"
            and decision["confidence"] >= MIN_ENSEMBLE_CONFIDENCE
            and momentum_ok
            and confirmation["passed"]
            and pattern["passed"]
        ):
            print(
                f"  🟢 Ensemble BUY (score {decision['buy_score']}, conf {decision['confidence']})"
            )
            place_buy_order(ticker, signal_source="ensemble_day_trader")
        elif (
            has_politician_buy
            and momentum_ok
            and decision["confidence"] >= 0.45
            and confirmation["passed"]
            and pattern["passed"]
        ):
            print("  🟡 Fallback BUY: politician signal + momentum + moderate confidence")
            place_buy_order(ticker, signal_source="politician_momentum_fallback")
        else:
            print(
                f"  ⬜ HOLD ({decision['action']}, buy {decision['buy_score']}, "
                f"sell {decision['sell_score']}, conf {decision['confidence']}, "
                f"confirm={confirmation['passed']}, pattern={pattern['passed']})"
            )

    print("\n✅ Bot run complete.\n")


# ──────────────────────────────────────────────────────────────────────────────
# Run once now, then schedule daily at 10am
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    initialize_schema()

    cfg_ok, cfg_issues = validate_runtime_config()
    if not cfg_ok:
        print("\n🛑 Configuration validation failed:")
        for issue in cfg_issues:
            print(f"  - {issue}")
        print("\nFix .env values, then restart.")
        sys.exit(1)

    net_ok, net_issues = connectivity_preflight()
    if not net_ok:
        print("\n🛑 Connectivity preflight failed:")
        for issue in net_issues:
            print(f"  - {issue}")
        print("\nVerify API keys/network, then restart.")
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("🚀 TRADING BOT WITH OPTIMIZATION AGENTS")
    print("=" * 70)
    print("\nAvailable commands:")
    print("  'run'     - Execute bot once immediately")
    print("  'report'  - Show optimization report")
    print("  'metrics' - Show current optimization metrics")
    print("  'debug'   - Run optimization-focused diagnostics")
    print("  'rebalance' - Force weekly backtest/reweight now")
    print("  'start'   - Start scheduled execution (daily at 10:00 AM)")
    print("  'autonomous' - Continuous passive autonomous mode")
    print("  'exit'    - Exit the program")
    print("=" * 70 + "\n")

    # Non-interactive production startup modes.
    if AUTO_START_MODE == "run_once":
        run_bot()
        sys.exit(0)

    if AUTO_START_MODE == "autonomous":
        start_autonomous_mode()
        sys.exit(0)

    # Interactive default behavior.
    run_bot()

    while True:
        try:
            user_input = input("\n📍 Enter command: ").strip().lower()

            if user_input == "run":
                run_bot()

            elif user_input == "report":
                print("\nGenerating comprehensive optimization report...")
                generate_optimization_report(OPTIMIZATION_LOOKBACK_DAYS)

            elif user_input == "metrics":
                print_optimization_metrics()

            elif user_input == "debug":
                run_optimization_debug()

            elif user_input == "rebalance":
                changed = maybe_run_weekly_reweight(force=True)
                if not changed:
                    print("ℹ️  Rebalance skipped by configuration.")

            elif user_input == "start":
                print("\n⏰ Bot scheduled daily at 10:00 AM. Press Ctrl+C to stop.")
                schedule.every().day.at("10:00").do(run_bot)
                try:
                    while True:
                        schedule.run_pending()
                        time.sleep(60)
                except KeyboardInterrupt:
                    print("\n⏹️  Bot scheduler stopped.")

            elif user_input == "autonomous":
                start_autonomous_mode()

            elif user_input == "exit":
                print("\n👋 Goodbye!")
                break

            else:
                print("❌ Unknown command. Try 'run', 'report', 'metrics', 'debug', 'rebalance', 'start', 'autonomous', or 'exit'")

        except KeyboardInterrupt:
            print("\n\n👋 Bot interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
