#!/usr/bin/env python3
"""
backtest.py  —  Full strategy backtest using the backtesting.py engine.

Mirrors bot.py live logic exactly:
  • Ensemble of MomentumBreakout + MeanReversion + VolatilityRegime agents
  • Momentum check  (close > close 5 days ago)
  • Take-profit / Stop-loss exits + ensemble sell-signal exit
  • Loads live ensemble weights from ensemble_weights.json

Usage
-----
  python backtest.py                                # full watchlist, 2-year window
  python backtest.py --tickers AAPL MSFT NVDA       # selected tickers
  python backtest.py --period 3y --cash 10000
  python backtest.py --tp 6 --sl 2.5
  python backtest.py --min-confidence 0.45          # relax entry threshold
  python backtest.py --politician-bias 0.15         # simulate always-on politician signal
  python backtest.py --open-html                    # open interactive Bokeh report

Outputs
-------
  backtest_equity_curve.png   — static equity curve (all tickers + portfolio line)
  backtest_results.csv        — per-ticker metric summary table
  backtest_report.html        — interactive Bokeh report (most-recently-run ticker)
"""

import argparse
import json
import sys
import warnings
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from pattern_signals import detect_ohlc_patterns

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
    import matplotlib
    matplotlib.use("Agg")          # non-interactive; avoids display-server requirement
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from backtesting import Backtest, Strategy
except ImportError as _ie:
    print(
        f"\n❌  Missing dependency: {_ie}\n"
        "    Run:  pip install backtesting matplotlib\n"
        "    Then re-run:  python backtest.py\n"
    )
    sys.exit(1)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ── Paths / hard-coded defaults ───────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
WEIGHTS_PATH = ROOT / "ensemble_weights.json"

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM",
    "BAC", "WMT", "UNH", "PFE", "XOM", "CVX", "LMT", "RTX", "BA",
    "GS", "V", "AMD", "INTC", "DIS", "NFLX", "CRM", "PYPL",
]

SECTOR_MAP = {
    "AAPL": "technology", "MSFT": "technology", "GOOGL": "technology", "AMZN": "consumer_discretionary",
    "NVDA": "technology", "META": "communication_services", "TSLA": "consumer_discretionary",
    "JPM": "financials", "BAC": "financials", "WMT": "consumer_staples", "UNH": "healthcare",
    "PFE": "healthcare", "XOM": "energy", "CVX": "energy", "LMT": "industrials", "RTX": "industrials",
    "BA": "industrials", "GS": "financials", "V": "financials", "AMD": "technology", "INTC": "technology",
    "DIS": "communication_services", "NFLX": "communication_services", "CRM": "technology", "PYPL": "financials",
}

DEFAULT_TAKE_PROFIT_PCT = 5.0
DEFAULT_STOP_LOSS_PCT   = 3.0    # positive number; applied as a loss
DEFAULT_MIN_CONFIDENCE  = 0.55
DEFAULT_CASH            = 10_000.0
DEFAULT_PERIOD          = "2y"
DEFAULT_COMMISSION      = 0.001  # 0.1 % per trade (realistic for retail brokers)

# Minimal, stable indicator parameters (avoid overfitting)
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
TREND_MA_PERIOD = 50

# ── Ensemble weight loader ────────────────────────────────────────────────────
_FALLBACK_WEIGHTS: dict[str, float] = {
    "momentum_breakout": 0.34,
    "mean_reversion":    0.33,
    "volatility_regime": 0.33,
}


def _load_weights() -> dict[str, float]:
    """Read ensemble_weights.json; fall back to equal weights on any error."""
    if not WEIGHTS_PATH.exists():
        return dict(_FALLBACK_WEIGHTS)
    try:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        stored  = payload.get("weights", {}) if isinstance(payload, dict) else {}
        merged  = {k: max(0.01, float(stored.get(k, _FALLBACK_WEIGHTS[k])))
                   for k in _FALLBACK_WEIGHTS}
        total   = sum(merged.values())
        return {k: round(v / total, 4) for k, v in merged.items()}
    except Exception:
        return dict(_FALLBACK_WEIGHTS)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = _ema(close, MACD_FAST) - _ema(close, MACD_SLOW)
    signal = _ema(macd_line, MACD_SIGNAL)
    hist = macd_line - signal
    return macd_line, signal, hist


def _bollinger(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    upper = mid + BB_STD * std
    lower = mid - BB_STD * std
    return lower, mid, upper


# ── Vectorized signal engine ─────────────────────────────────────────────────
def _compute_signals(
    close: pd.Series,
    weights: dict[str, float],
    min_confidence: float,
    politician_bias: float = 0.0,
    entry_model: str = "baseline",
    confirmation_mode: str = "boolean",
    min_confirmations: int = 2,
    confirmation_weight_threshold: float = 0.6,
    ohlc: pd.DataFrame | None = None,
    pattern_mode: str = "off",
    pattern_weight: float = 0.15,
) -> pd.DataFrame:
    """
    Vectorized replication of all three ensemble agents in optimization_agents.py.

    The output columns (entry_signal, exit_signal) are stored as float32 (1 / 0)
    so backtesting.py can treat them as numeric indicator arrays.

    Signal derivation mirrors evaluate_ticker() → run_bot() in bot.py:
      entry = ens_buy AND confidence >= threshold AND price > 5d-ago
      exit  = ens_sell AND confidence >= threshold
    """
    close = close.astype(float)
    n     = len(close)

    # bars-of-history counter (for confidence scaling, same as live agents)
    n_bars = pd.Series(range(1, n + 1), index=close.index, dtype=float)

    # ── MomentumBreakoutAgent ─────────────────────────────────────────────────
    sma5  = close.rolling(5).mean()
    sma12 = close.rolling(12).mean()
    ret3  = (close / close.shift(3) - 1) * 100

    mb_score = (
        (sma5 > sma12).astype(float) * 0.55
        + ret3.clip(lower=0).div(10).clip(upper=0.35)
    ).clip(0, 1)

    mb_buy  = mb_score >= 0.62
    mb_sell = mb_score <= 0.22
    mb_conf = (0.45 + (n_bars / 120).clip(upper=0.45)).clip(upper=0.9)

    # ── MeanReversionAgent ────────────────────────────────────────────────────
    roll20_mean = close.rolling(20).mean()
    roll20_std  = close.rolling(20).std().replace(0.0, np.nan)
    z_score     = (close - roll20_mean) / roll20_std

    mr_buy_score  = ((1.3 - z_score) / 2.6).clip(0, 1)
    mr_sell_score = ((z_score - 0.9) / 2.2).clip(0, 1)

    mr_buy  = mr_buy_score  >= 0.65
    mr_sell = mr_sell_score >= 0.65
    mr_conf = (0.5 + (n_bars / 180).clip(upper=0.35)).clip(upper=0.85)

    # ── VolatilityRegimeAgent ─────────────────────────────────────────────────
    rets     = close.pct_change()
    vol10    = rets.rolling(10).std() * np.sqrt(252)
    vol30    = rets.rolling(30).std() * np.sqrt(252)
    trend10  = (close / close.shift(10) - 1) * 100

    vol_low_thr  = vol30.mul(0.95).clip(lower=0.22)
    vol_high_thr = vol30.mul(1.25).clip(lower=0.70)

    vr_buy  = (vol10 < vol_low_thr)  & (trend10 > 0)
    vr_sell = (vol10 > vol_high_thr) & (trend10 < 0)

    # Agent scores when action==buy or ==sell (mirrors optimize_agents.py logic)
    vr_buy_score_s  = vr_buy.astype(float)  * 0.62
    vr_sell_score_s = vr_sell.astype(float) * 0.75

    vr_conf = (0.45 + (n_bars / 180).clip(upper=0.40)).clip(upper=0.85)

    # ── Ensemble (mirrors EnsembleTradingAgent.evaluate_ticker) ──────────────
    w_mb = weights["momentum_breakout"]
    w_mr = weights["mean_reversion"]
    w_vr = weights["volatility_regime"]

    ens_buy_score = (
        mb_buy.astype(float)  * mb_score        * mb_conf * w_mb
        + mr_buy.astype(float)  * mr_buy_score   * mr_conf * w_mr
        + vr_buy_score_s                         * vr_conf * w_vr
    ).fillna(0.0) + politician_bias

    ens_sell_score = (
        mb_sell.astype(float) * mb_score         * mb_conf * w_mb
        + mr_sell.astype(float) * mr_sell_score  * mr_conf * w_mr
        + vr_sell_score_s                        * vr_conf * w_vr
    ).fillna(0.0)

    # Average agent confidence → ensemble confidence (mirrors live code)
    ens_confidence = ((mb_conf + mr_conf + vr_conf) / 3 + 0.1).clip(upper=0.98)

    # Momentum gate: price > price 5 bars ago (mirrors check_momentum())
    momentum_ok = close > close.shift(5)

    # Optional pattern overlay from OHLC structure (Week 5-6)
    pattern_bull_score = pd.Series(0.0, index=close.index)
    pattern_bear_score = pd.Series(0.0, index=close.index)
    pattern_bull = pd.Series(False, index=close.index)
    pattern_bear = pd.Series(False, index=close.index)
    if pattern_mode != "off" and ohlc is not None:
        pat = detect_ohlc_patterns(ohlc[["Open", "High", "Low", "Close"]])
        pattern_bull_score = pat["pattern_bull_score"].reindex(close.index).fillna(0.0)
        pattern_bear_score = pat["pattern_bear_score"].reindex(close.index).fillna(0.0)
        pattern_bull = pat["pattern_bull"].reindex(close.index).fillna(0.0).astype(bool)
        pattern_bear = pat["pattern_bear"].reindex(close.index).fillna(0.0).astype(bool)

    if pattern_mode == "weighted":
        ens_buy_score = ens_buy_score + pattern_bull_score.clip(0, 1) * pattern_weight
        ens_sell_score = ens_sell_score + pattern_bear_score.clip(0, 1) * pattern_weight

    # Baseline entry/exit (current live logic parity)
    baseline_entry_signal = (
        (ens_buy_score >= ens_sell_score)
        & (ens_buy_score  >= 0.55)
        & (ens_confidence >= min_confidence)
        & momentum_ok
    ).fillna(False)

    exit_signal = (
        (ens_sell_score > ens_buy_score)
        & (ens_sell_score >= 0.55)
        & (ens_confidence >= min_confidence)
    ).fillna(False)

    # Week 3: modular confirmation blocks
    trend_ma = close.rolling(TREND_MA_PERIOD).mean()
    trend_filter = (trend_ma > trend_ma.shift(5)) & (close > trend_ma)

    macd_line, macd_signal, macd_hist = _macd(close)
    momentum_confirm = (macd_line > macd_signal) & (macd_hist > 0)

    rsi = _rsi(close, period=RSI_PERIOD)
    bb_lower, bb_mid, _ = _bollinger(close)
    mean_reversion_confirm = ((rsi < 45) | ((close <= bb_mid) & (rsi < 55))) & (close >= bb_lower * 0.98)

    confirm_count = (
        trend_filter.astype(int)
        + momentum_confirm.astype(int)
        + mean_reversion_confirm.astype(int)
    )

    weighted_confirm_score = (
        trend_filter.astype(float) * 0.40
        + momentum_confirm.astype(float) * 0.35
        + mean_reversion_confirm.astype(float) * 0.25
    )

    if confirmation_mode == "weighted":
        confirmation_ok = weighted_confirm_score >= confirmation_weight_threshold
    else:
        confirmation_ok = confirm_count >= min_confirmations

    confirmed_entry_signal = baseline_entry_signal & confirmation_ok.fillna(False)
    if pattern_mode == "confirm":
        confirmed_entry_signal = confirmed_entry_signal & pattern_bull & (~pattern_bear)

    entry_signal = confirmed_entry_signal if entry_model == "confirmed" else baseline_entry_signal

    return pd.DataFrame(
        {
            "entry_signal":   entry_signal.astype("float32"),
            "entry_baseline": baseline_entry_signal.astype("float32"),
            "entry_confirmed": confirmed_entry_signal.astype("float32"),
            "exit_signal":    exit_signal.astype("float32"),
            "ens_buy_score":  ens_buy_score.round(4).astype("float32"),
            "ens_sell_score": ens_sell_score.round(4).astype("float32"),
            "ens_confidence": ens_confidence.round(4).astype("float32"),
            "trend_filter": trend_filter.astype("float32"),
            "momentum_confirm": momentum_confirm.astype("float32"),
            "mean_reversion_confirm": mean_reversion_confirm.astype("float32"),
            "confirm_count": confirm_count.astype("float32"),
            "confirm_weighted_score": weighted_confirm_score.astype("float32"),
            "pattern_bull": pattern_bull.astype("float32"),
            "pattern_bear": pattern_bear.astype("float32"),
            "pattern_bull_score": pattern_bull_score.astype("float32"),
            "pattern_bear_score": pattern_bear_score.astype("float32"),
        },
        index=close.index,
    )


# ── backtesting.py Strategy ───────────────────────────────────────────────────
class BotStrategy(Strategy):
    """
    Event-driven wrapper around the precomputed signal arrays.
    Class-level attributes become overridable parameters in bt.run().
    """
    tp_pct: float = DEFAULT_TAKE_PROFIT_PCT
    sl_pct: float = DEFAULT_STOP_LOSS_PCT

    def init(self) -> None:
        # Wrap precomputed arrays as recognised indicators.
        # lambda x: x is the identity passthrough; backtesting.py calls it with
        # the argument array and stores the result for bar-level access.
        self.entry_ind = self.I(
            lambda x: x,
            self.data.df["entry_signal"].to_numpy(),
            name="EntrySignal",
            overlay=False,
        )
        self.exit_ind = self.I(
            lambda x: x,
            self.data.df["exit_signal"].to_numpy(),
            name="ExitSignal",
            overlay=False,
        )

    def next(self) -> None:
        price = self.data.Close[-1]

        if not self.position:
            if self.entry_ind[-1]:
                self.buy(
                    sl=price * (1.0 - self.sl_pct / 100.0),
                    tp=price * (1.0 + self.tp_pct / 100.0),
                )
        else:
            # Ensemble sell-signal overrides; TP/SL are handled natively
            if self.exit_ind[-1]:
                self.position.close()


# ── Data download ─────────────────────────────────────────────────────────────
def _fetch_ohlcv(ticker: str, period: str) -> pd.DataFrame | None:
    """
    Download daily OHLCV from yfinance.
    Returns a backtesting.py-compatible DataFrame (Open/High/Low/Close/Volume)
    or None if data is insufficient.
    """
    try:
        df = yf.download(
            ticker, period=period, interval="1d",
            progress=False, auto_adjust=True,
        )
        if df.empty:
            return None
        # Flatten potential MultiIndex columns (multi-ticker downloads)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.DatetimeIndex(df.index)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 60:
            return None
        return df
    except Exception as exc:
        print(f"  ⚠️  Download failed for {ticker}: {exc}")
        return None


# ── Reporting ─────────────────────────────────────────────────────────────────
def _print_ticker_stats(ticker: str, stats: pd.Series) -> None:
    def _f(key: str, default=float("nan")):
        v = stats.get(key, default)
        return float(v) if v is not None else float("nan")

    ret      = _f("Return [%]")
    ann_ret  = _f("Return (Ann.) [%]")
    bah      = _f("Buy & Hold Return [%]")
    max_dd   = _f("Max. Drawdown [%]")
    n        = int(stats.get("# Trades", 0) or 0)
    win_rate = _f("Win Rate [%]")
    pf       = _f("Profit Factor")
    sharpe   = _f("Sharpe Ratio")
    sortino  = _f("Sortino Ratio")
    expect   = _f("Expectancy [%]")
    best     = _f("Best Trade [%]")
    worst    = _f("Worst Trade [%]")
    exposure = _f("Exposure Time [%]")

    print(f"\n  ┌{'─'*54}┐")
    print(f"  │  {ticker:<52}│")
    print(f"  ├{'─'*54}┤")
    print(f"  │  Total Return      {ret:>9.2f}%   (B&H: {bah:>8.2f}%)  │")
    print(f"  │  CAGR              {ann_ret:>9.2f}%                       │")
    print(f"  │  Max Drawdown      {max_dd:>9.2f}%                       │")
    print(f"  │  # Trades          {n:>9d}                         │")
    print(f"  │  Win Rate          {win_rate:>9.2f}%                       │")
    print(f"  │  Profit Factor     {pf:>9.2f}                         │")
    print(f"  │  Sharpe Ratio      {sharpe:>9.2f}                         │")
    print(f"  │  Sortino Ratio     {sortino:>9.2f}                         │")
    print(f"  │  Expectancy        {expect:>9.2f}%  per trade              │")
    print(f"  │  Best / Worst      {best:>+8.2f}% / {worst:>+8.2f}%             │")
    print(f"  │  Market Exposure   {exposure:>9.2f}%                       │")
    print(f"  └{'─'*54}┘")


def _print_aggregate_stats(rows: list[dict]) -> None:
    if not rows:
        return

    def _avg(key: str) -> float:
        vals = [r[key] for r in rows if not np.isnan(r.get(key, float("nan")))]
        return float(np.mean(vals)) if vals else float("nan")

    def _trade_weighted(key: str) -> float:
        pairs = [
            (r.get(key, float("nan")), r.get("trades", 0))
            for r in rows
        ]
        pairs = [(v, w) for v, w in pairs if not np.isnan(v) and w > 0]
        if not pairs:
            return float("nan")
        total_w = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / total_w if total_w > 0 else float("nan")

    total_trades = sum(r.get("trades", 0) for r in rows)

    print(f"\n  {'═'*56}")
    print(f"  PORTFOLIO AGGREGATE  ({len(rows)} tickers | {total_trades} total trades)")
    print(f"  {'═'*56}")
    print(f"  Avg Total Return     {_avg('return'):>10.2f}%")
    print(f"  Avg CAGR             {_avg('cagr'):>10.2f}%")
    print(f"  Avg Max Drawdown     {_avg('max_dd'):>10.2f}%")
    print(f"  Win Rate (trade-wt)  {_trade_weighted('win_rate'):>10.2f}%")
    print(f"  Profit Factor (t-wt) {_trade_weighted('profit_factor'):>10.2f}")
    print(f"  Avg Sharpe           {_avg('sharpe'):>10.2f}")
    print(f"  Avg Sortino          {_avg('sortino'):>10.2f}")
    print(f"  Avg Expectancy       {_avg('expectancy'):>10.2f}%  per trade")
    print(f"  {'═'*56}")


# ── Equity curve ──────────────────────────────────────────────────────────────
def _save_equity_curve(
    curves: dict[str, pd.Series],
    out_path: Path,
    title_prefix: str = "Strategy",
) -> None:
    """
    Save a two-panel PNG:
      Top:    per-ticker equity curves normalised to 100 + equal-weight portfolio line
      Bottom: portfolio drawdown
    """
    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(14, 9),
        gridspec_kw={"height_ratios": [3, 1]},
    )

    normed: list[pd.Series] = []
    for ticker, eq in curves.items():
        norm = (eq / eq.iloc[0]) * 100.0
        ax_eq.plot(eq.index, norm, linewidth=1.1, alpha=0.65, label=ticker)
        normed.append(norm)

    if normed:
        port = pd.concat(normed, axis=1).ffill().mean(axis=1)
        ax_eq.plot(
            port.index, port,
            color="black", linewidth=2.2, label="Portfolio (equal-wt)", zorder=5,
        )
        dd = (port / port.cummax() - 1.0) * 100.0
        ax_dd.fill_between(dd.index, dd, 0, color="crimson", alpha=0.35, label="Drawdown")
        ax_dd.plot(dd.index, dd, color="darkred", linewidth=1.0)
        ax_dd.axhline(0, color="black", linewidth=0.6)

    ax_eq.axhline(100, color="gray", linewidth=0.7, linestyle="--")
    ax_eq.set_title(f"{title_prefix} Equity Curve  (base = 100)", fontsize=13, fontweight="bold")
    ax_eq.set_ylabel("Normalised Equity")
    ax_eq.legend(loc="upper left", fontsize=7, ncol=5)
    ax_eq.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax_eq.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    ax_dd.set_title("Equal-Weight Portfolio Drawdown", fontsize=10)
    ax_dd.set_ylabel("Drawdown %")
    ax_dd.set_xlabel("Date")

    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  📊 Equity curve  →  {out_path.name}")


def _extract_row(ticker: str, stats: pd.Series) -> dict:
    return {
        "ticker": ticker,
        "return": float(stats.get("Return [%]", float("nan"))),
        "cagr": float(stats.get("Return (Ann.) [%]", float("nan"))),
        "bah_return": float(stats.get("Buy & Hold Return [%]", float("nan"))),
        "max_dd": float(stats.get("Max. Drawdown [%]", float("nan"))),
        "trades": int(stats.get("# Trades", 0) or 0),
        "win_rate": float(stats.get("Win Rate [%]", float("nan"))),
        "profit_factor": float(stats.get("Profit Factor", float("nan"))),
        "sharpe": float(stats.get("Sharpe Ratio", float("nan"))),
        "sortino": float(stats.get("Sortino Ratio", float("nan"))),
        "expectancy": float(stats.get("Expectancy [%]", float("nan"))),
        "best_trade": float(stats.get("Best Trade [%]", float("nan"))),
        "worst_trade": float(stats.get("Worst Trade [%]", float("nan"))),
        "exposure_pct": float(stats.get("Exposure Time [%]", float("nan"))),
    }


def _aggregate_model(rows: list[dict]) -> dict:
    if not rows:
        return {
            "avg_return": float("nan"),
            "avg_cagr": float("nan"),
            "avg_max_dd": float("nan"),
            "avg_sharpe": float("nan"),
            "avg_sortino": float("nan"),
            "trade_wt_win_rate": float("nan"),
            "trade_wt_profit_factor": float("nan"),
            "total_trades": 0,
        }

    def avg(key: str) -> float:
        vals = [r[key] for r in rows if not np.isnan(r.get(key, float("nan")))]
        return float(np.mean(vals)) if vals else float("nan")

    def trade_weighted(key: str) -> float:
        pairs = [(r.get(key, float("nan")), r.get("trades", 0)) for r in rows]
        pairs = [(v, w) for v, w in pairs if not np.isnan(v) and w > 0]
        if not pairs:
            return float("nan")
        denom = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / denom if denom > 0 else float("nan")

    return {
        "avg_return": avg("return"),
        "avg_cagr": avg("cagr"),
        "avg_max_dd": avg("max_dd"),
        "avg_sharpe": avg("sharpe"),
        "avg_sortino": avg("sortino"),
        "trade_wt_win_rate": trade_weighted("win_rate"),
        "trade_wt_profit_factor": trade_weighted("profit_factor"),
        "total_trades": int(sum(r.get("trades", 0) for r in rows)),
    }


@dataclass
class BacktestRunResult:
    rows: list[dict]
    equity_curves: dict[str, pd.Series]
    last_bt: Backtest | None
    skipped: list[str]


def _series_metrics(equity: pd.Series) -> dict:
    eq = equity.dropna().astype(float)
    if len(eq) < 3:
        return {
            "total_return_pct": float("nan"),
            "cagr_pct": float("nan"),
            "max_drawdown_pct": float("nan"),
            "sharpe": float("nan"),
            "sortino": float("nan"),
        }

    rets = eq.pct_change().fillna(0.0)
    total_return = (eq.iloc[-1] / eq.iloc[0] - 1.0) * 100.0
    years = max(1 / 252, len(eq) / 252.0)
    cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0) * 100.0

    drawdown = eq / eq.cummax() - 1.0
    max_dd = drawdown.min() * 100.0

    vol = rets.std()
    downside = rets[rets < 0].std()
    sharpe = ((rets.mean() / vol) * np.sqrt(252)) if vol and vol > 1e-12 else float("nan")
    sortino = ((rets.mean() / downside) * np.sqrt(252)) if downside and downside > 1e-12 else float("nan")

    return {
        "total_return_pct": float(total_return),
        "cagr_pct": float(cagr),
        "max_drawdown_pct": float(max_dd),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
    }


def _build_portfolio_equity(
    equity_curves: dict[str, pd.Series],
    allocation_model: str,
    max_weight_per_ticker: float,
    max_sector_concentration: float,
    max_total_open_risk_pct: float,
    stop_loss_pct: float,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Build daily portfolio equity from per-ticker strategy equity curves with constraints.
    Returns portfolio equity and daily weight matrix.
    """
    if not equity_curves:
        return pd.Series(dtype=float), pd.DataFrame()

    # Normalize each ticker series to 1.0 and convert to daily strategy returns.
    normed = {
        t: (s / s.iloc[0]).astype(float) for t, s in equity_curves.items() if len(s) > 2 and s.iloc[0] != 0
    }
    if not normed:
        return pd.Series(dtype=float), pd.DataFrame()

    eq_df = pd.concat(normed, axis=1).sort_index().ffill().dropna(how="all")
    ret_df = eq_df.pct_change().fillna(0.0)
    tickers = list(ret_df.columns)

    if allocation_model == "vol_adjusted":
        hist_vol = ret_df.shift(1).rolling(20).std().replace(0.0, np.nan)
        inv_vol = 1.0 / hist_vol
        raw_weights = inv_vol.div(inv_vol.sum(axis=1), axis=0).fillna(1.0 / len(tickers))
    elif allocation_model == "capped":
        raw_weights = pd.DataFrame(1.0 / len(tickers), index=ret_df.index, columns=tickers)
        raw_weights = raw_weights.clip(upper=max_weight_per_ticker)
        raw_weights = raw_weights.div(raw_weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    else:
        raw_weights = pd.DataFrame(1.0 / len(tickers), index=ret_df.index, columns=tickers)

    weights = raw_weights.copy()

    # Per-ticker cap (applies to every model as a hard portfolio limit).
    weights = weights.clip(upper=max_weight_per_ticker)

    # Sector concentration cap; extra exposure becomes cash (de-risking, not force-redistribution).
    sectors = {t: SECTOR_MAP.get(t, "unknown") for t in tickers}
    for sector in sorted(set(sectors.values())):
        sec_cols = [t for t in tickers if sectors[t] == sector]
        if not sec_cols:
            continue
        sec_total = weights[sec_cols].sum(axis=1)
        scale = np.where(sec_total > max_sector_concentration, max_sector_concentration / sec_total, 1.0)
        weights.loc[:, sec_cols] = weights[sec_cols].mul(scale, axis=0)

    # Open-risk cap based on stop-loss approximation.
    per_dollar_risk = abs(stop_loss_pct) / 100.0
    max_gross_exposure = 1.0 if per_dollar_risk <= 0 else min(1.0, max_total_open_risk_pct / per_dollar_risk)
    gross = weights.sum(axis=1)
    gross_scale = np.where(gross > max_gross_exposure, max_gross_exposure / gross, 1.0)
    weights = weights.mul(gross_scale, axis=0)

    # Apply one-day lag so allocations only use prior information.
    effective_w = weights.shift(1).fillna(0.0)
    port_rets = (effective_w * ret_df).sum(axis=1)
    port_equity = (1.0 + port_rets).cumprod() * 100.0
    return port_equity, weights


def _compare_diversified_vs_single(
    equity_curves: dict[str, pd.Series],
    allocation_model: str,
    max_weight_per_ticker: float,
    max_sector_concentration: float,
    max_total_open_risk_pct: float,
    stop_loss_pct: float,
    single_ticker: str,
) -> dict:
    portfolio_eq, weights = _build_portfolio_equity(
        equity_curves=equity_curves,
        allocation_model=allocation_model,
        max_weight_per_ticker=max_weight_per_ticker,
        max_sector_concentration=max_sector_concentration,
        max_total_open_risk_pct=max_total_open_risk_pct,
        stop_loss_pct=stop_loss_pct,
    )
    if portfolio_eq.empty:
        return {"status": "no_data"}

    single = single_ticker.upper()
    if single not in equity_curves:
        single = sorted(equity_curves.keys())[0]
    single_eq = (equity_curves[single] / equity_curves[single].iloc[0]) * 100.0

    common_index = portfolio_eq.index.intersection(single_eq.index)
    portfolio_eq = portfolio_eq.loc[common_index]
    single_eq = single_eq.loc[common_index]

    pm = _series_metrics(portfolio_eq)
    sm = _series_metrics(single_eq)
    improved = (
        (pm["sharpe"] > sm["sharpe"])
        and (pm["sortino"] > sm["sortino"])
        and (pm["max_drawdown_pct"] > sm["max_drawdown_pct"])
    )

    return {
        "status": "ok",
        "single_ticker": single,
        "portfolio_metrics": pm,
        "single_metrics": sm,
        "improved": improved,
        "portfolio_equity": portfolio_eq,
        "single_equity": single_eq,
        "weights": weights,
    }


def _run_model(
    tickers: list[str],
    period: str,
    cash: float,
    commission: float,
    tp: float,
    sl: float,
    min_confidence: float,
    politician_bias: float,
    weights: dict[str, float],
    entry_model: str,
    confirmation_mode: str,
    min_confirmations: int,
    confirmation_weight_threshold: float,
    pattern_mode: str,
    pattern_weight: float,
    spread: float,
    label: str,
) -> BacktestRunResult:
    rows: list[dict] = []
    equity_curves: dict[str, pd.Series] = {}
    last_bt = None
    skipped: list[str] = []

    print(f"\n{'─'*58}")
    print(f"  MODEL: {label} ({entry_model})")
    print(f"{'─'*58}")

    for ticker in tickers:
        print(f"\n🔍 {ticker}  ({period})")
        df = _fetch_ohlcv(ticker, period)
        if df is None:
            print("  ⚠️  Skipped — insufficient data (< 60 bars)")
            skipped.append(ticker)
            continue

        sigs = _compute_signals(
            df["Close"],
            weights=weights,
            min_confidence=min_confidence,
            politician_bias=politician_bias,
            entry_model=entry_model,
            confirmation_mode=confirmation_mode,
            min_confirmations=min_confirmations,
            confirmation_weight_threshold=confirmation_weight_threshold,
            ohlc=df,
            pattern_mode=pattern_mode,
            pattern_weight=pattern_weight,
        )

        n_entries = int(sigs["entry_signal"].sum())
        print(f"  📶 Signal scan: {n_entries} potential entry bars over {len(df)} trading days")
        if entry_model == "confirmed":
            avg_confirm = float(sigs["confirm_count"].mean())
            print(
                "  ✅ Confirmation gates: "
                f"trend={int(sigs['trend_filter'].sum())}, "
                f"macd={int(sigs['momentum_confirm'].sum())}, "
                f"rsi+bb={int(sigs['mean_reversion_confirm'].sum())}, "
                f"avg_count={avg_confirm:.2f}"
            )

        df = pd.concat([df, sigs[["entry_signal", "exit_signal"]]], axis=1)

        bt = Backtest(
            df,
            BotStrategy,
            cash=cash,
            commission=commission,
            spread=spread,
            exclusive_orders=True,
            trade_on_close=False,
        )
        stats = bt.run(tp_pct=tp, sl_pct=sl)
        _print_ticker_stats(ticker, stats)

        rows.append(_extract_row(ticker, stats))

        eq_curve = stats.get("_equity_curve")
        if eq_curve is not None and "Equity" in eq_curve.columns:
            equity_curves[ticker] = eq_curve["Equity"]

        last_bt = bt

    if skipped:
        print(f"\n  ⚠️  Skipped tickers ({len(skipped)}): {', '.join(skipped)}")

    if rows:
        _print_aggregate_stats(rows)

    return BacktestRunResult(rows=rows, equity_curves=equity_curves, last_bt=last_bt, skipped=skipped)


def _split_oos(df: pd.DataFrame, oos_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_idx = max(60, int(len(df) * (1 - oos_ratio)))
    split_idx = min(split_idx, len(df) - 30)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


# ── Pattern-weight robustness sweep ─────────────────────────────────────────
def _run_pattern_weight_sweep(
    *,
    tickers: list,
    period: str,
    cash: float,
    commission: float,
    tp: float,
    sl: float,
    spread: float,
    min_confidence: float,
    politician_bias: float,
    weights: dict,
    confirmation_mode: str,
    min_confirmations: int,
    confirmation_weight_threshold: float,
    oos_ratio: float,
    sweep_weights: list,
    pattern_mode: str,
) -> None:
    """Sweep multiple pattern weights and emit a robustness stability table."""

    print(f"\n{'='*70}")
    print(f"  PATTERN-WEIGHT ROBUSTNESS SWEEP  ({len(sweep_weights)} weights)")
    print(f"  Weights: {sweep_weights}")
    print(f"  OOS ratio: {oos_ratio:.2f}  |  Pattern mode: {pattern_mode}")
    print(f"{'='*70}")

    # -- Download OOS data once per ticker --
    oos_frames: dict = {}
    for ticker in tickers:
        df_full = _fetch_ohlcv(ticker, period)
        if df_full is None or len(df_full) < 140:
            continue
        _, oos_df = _split_oos(df_full, oos_ratio)
        if len(oos_df) < 70:
            continue
        oos_frames[ticker] = oos_df

    if not oos_frames:
        print("  No valid OOS data found. Aborting sweep.")
        return

    print(f"  Tickers with valid OOS data: {len(oos_frames)}")

    # -- Run numeric-only baseline once (pattern_mode=off) --
    numeric_rows: list = []
    for ticker, oos_df in oos_frames.items():
        sig = _compute_signals(
            oos_df["Close"],
            weights=weights,
            min_confidence=min_confidence,
            politician_bias=politician_bias,
            entry_model="confirmed",
            confirmation_mode=confirmation_mode,
            min_confirmations=min_confirmations,
            confirmation_weight_threshold=confirmation_weight_threshold,
            ohlc=oos_df,
            pattern_mode="off",
            pattern_weight=0.0,
        )
        df_bt = pd.concat([oos_df.copy(), sig[["entry_signal", "exit_signal"]]], axis=1)
        stats = Backtest(
            df_bt, BotStrategy,
            cash=cash, commission=commission, spread=spread,
            exclusive_orders=True, trade_on_close=False,
        ).run(tp_pct=tp, sl_pct=sl)
        numeric_rows.append(_extract_row(ticker, stats))

    n_agg = _aggregate_model(numeric_rows)
    print(
        f"\n  Numeric-only baseline: "
        f"return={n_agg['avg_return']:.2f}%  "
        f"sharpe={n_agg['avg_sharpe']:.3f}  "
        f"trades={n_agg['total_trades']}"
    )

    # -- Loop over each weight --
    eff_pattern_mode = pattern_mode if pattern_mode != "off" else "weighted"
    sweep_rows: list = []
    for w in sweep_weights:
        pattern_rows: list = []
        for ticker, oos_df in oos_frames.items():
            sig = _compute_signals(
                oos_df["Close"],
                weights=weights,
                min_confidence=min_confidence,
                politician_bias=politician_bias,
                entry_model="confirmed",
                confirmation_mode=confirmation_mode,
                min_confirmations=min_confirmations,
                confirmation_weight_threshold=confirmation_weight_threshold,
                ohlc=oos_df,
                pattern_mode=eff_pattern_mode,
                pattern_weight=w,
            )
            df_bt = pd.concat([oos_df.copy(), sig[["entry_signal", "exit_signal"]]], axis=1)
            stats = Backtest(
                df_bt, BotStrategy,
                cash=cash, commission=commission, spread=spread,
                exclusive_orders=True, trade_on_close=False,
            ).run(tp_pct=tp, sl_pct=sl)
            pattern_rows.append(_extract_row(ticker, stats))

        p_agg = _aggregate_model(pattern_rows)
        ret_delta = p_agg["avg_return"] - n_agg["avg_return"]
        sharpe_delta = p_agg["avg_sharpe"] - n_agg["avg_sharpe"]
        sortino_delta = p_agg["avg_sortino"] - n_agg["avg_sortino"]
        maxdd_delta = p_agg["avg_max_dd"] - n_agg["avg_max_dd"]
        improved = sharpe_delta > 0 and sortino_delta > 0 and maxdd_delta > 0

        sweep_rows.append({
            "weight": w,
            "status": "ok",
            "num_return": n_agg["avg_return"],
            "pat_return": p_agg["avg_return"],
            "ret_delta": ret_delta,
            "num_sharpe": n_agg["avg_sharpe"],
            "pat_sharpe": p_agg["avg_sharpe"],
            "sharpe_delta": sharpe_delta,
            "num_sortino": n_agg["avg_sortino"],
            "pat_sortino": p_agg["avg_sortino"],
            "sortino_delta": sortino_delta,
            "num_maxdd": n_agg["avg_max_dd"],
            "pat_maxdd": p_agg["avg_max_dd"],
            "maxdd_delta": maxdd_delta,
            "num_trades": n_agg["total_trades"],
            "pat_trades": p_agg["total_trades"],
            "improved": improved,
        })

    # -- Print stability table (ASCII-safe) --
    print(f"\n{'='*70}")
    print("  STABILITY TABLE")
    print(f"{'='*70}")
    col = f"  {'weight':>7}  {'ret_delta':>10}  {'sharpe_d':>10}  {'sortino_d':>10}  {'maxdd_d':>10}  {'improved':>9}"
    print(col)
    print(f"  {'-'*7}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*9}")
    for r in sweep_rows:
        print(
            f"  {r['weight']:>7.2f}  {r['ret_delta']:>+10.4f}  {r['sharpe_delta']:>+10.4f}  "
            f"{r['sortino_delta']:>+10.4f}  {r['maxdd_delta']:>+10.4f}  {str(r['improved']):>9}"
        )

    # -- Save CSV --
    csv_path = ROOT / "pattern_weight_stability_table.csv"
    pd.DataFrame(sweep_rows).to_csv(str(csv_path), index=False)
    improved_count = sum(r["improved"] for r in sweep_rows)
    print(f"\n  Stability table saved -> pattern_weight_stability_table.csv")
    print(f"  {improved_count}/{len(sweep_rows)} weights showed improvement.")


# ── CLI entry point ───────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest the trading bot strategy (mirrors bot.py live logic).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="Tickers to backtest (default: full watchlist)",
    )
    parser.add_argument(
        "--period", default=DEFAULT_PERIOD,
        help="yfinance period string, e.g. 1y 2y 3y 5y",
    )
    parser.add_argument(
        "--cash", type=float, default=DEFAULT_CASH,
        help="Starting capital per ticker ($)",
    )
    parser.add_argument(
        "--tp", type=float, default=DEFAULT_TAKE_PROFIT_PCT,
        help="Take-profit %%",
    )
    parser.add_argument(
        "--sl", type=float, default=DEFAULT_STOP_LOSS_PCT,
        help="Stop-loss %% (positive number)",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
        help="Ensemble confidence threshold",
    )
    parser.add_argument(
        "--politician-bias", type=float, default=0.0,
        help="Simulate always-on politician buy bias (0 = off)",
    )
    parser.add_argument(
        "--commission", type=float, default=DEFAULT_COMMISSION,
        help="Commission per trade as decimal (0.001 = 0.1%%)",
    )
    parser.add_argument(
        "--open-html", action="store_true",
        help="Open interactive Bokeh HTML report in browser after run",
    )
    parser.add_argument(
        "--entry-model", choices=["baseline", "confirmed"], default="confirmed",
        help="Entry model for main run",
    )
    parser.add_argument(
        "--confirmation-mode", choices=["boolean", "weighted"], default="boolean",
        help="Confirmation logic for confirmed model",
    )
    parser.add_argument(
        "--min-confirmations", type=int, default=2,
        help="Boolean mode: required confirmations out of trend/MACD/RSI+BB",
    )
    parser.add_argument(
        "--confirmation-weight-threshold", type=float, default=0.6,
        help="Weighted mode: min weighted confirmation score",
    )
    parser.add_argument(
        "--compare-oos", action="store_true",
        help="Run baseline vs confirmed comparison on out-of-sample split",
    )
    parser.add_argument(
        "--oos-ratio", type=float, default=0.3,
        help="Out-of-sample fraction used when --compare-oos is enabled",
    )
    parser.add_argument(
        "--allocation-model", choices=["equal_weight", "vol_adjusted", "capped"], default="vol_adjusted",
        help="Portfolio allocation model for diversified comparison",
    )
    parser.add_argument(
        "--max-weight-per-ticker", type=float, default=0.25,
        help="Hard max portfolio weight per ticker",
    )
    parser.add_argument(
        "--max-sector-concentration", type=float, default=0.40,
        help="Max total portfolio weight in any single sector",
    )
    parser.add_argument(
        "--max-total-open-risk", type=float, default=0.02,
        help="Max portfolio open risk based on stop-loss approximation",
    )
    parser.add_argument(
        "--single-ticker-baseline", default="AAPL",
        help="Ticker to compare diversified portfolio against",
    )
    parser.add_argument(
        "--pattern-mode", choices=["off", "confirm", "weighted"], default="off",
        help="Pattern integration mode for OHLC pattern detector",
    )
    parser.add_argument(
        "--pattern-weight", type=float, default=0.15,
        help="Pattern score weight when pattern-mode=weighted",
    )
    parser.add_argument(
        "--compare-pattern-oos", action="store_true",
        help="Compare confirmed numeric-only vs confirmed+pattern on OOS segment",
    )
    parser.add_argument(
        "--slippage-bps", type=float, default=2.0,
        help="Approx execution slippage as half-spread in basis points",
    )
    parser.add_argument(
        "--sweep-pattern-weights", action="store_true",
        help="Sweep multiple pattern weights and emit a robustness stability table",
    )
    parser.add_argument(
        "--sweep-weights", nargs="+", type=float, default=[0.05, 0.08, 0.12, 0.16, 0.20],
        help="Pattern weights to sweep (used with --sweep-pattern-weights)",
    )
    args = parser.parse_args()
    spread = max(0.0, args.slippage_bps / 10000.0)

    tickers = [t.upper() for t in args.tickers] if args.tickers else list(DEFAULT_WATCHLIST)
    weights = _load_weights()

    # ── Pattern-weight sweep (early exit) ─────────────────────────────────────
    if args.sweep_pattern_weights:
        _run_pattern_weight_sweep(
            tickers=tickers,
            period=args.period,
            cash=args.cash,
            commission=args.commission,
            tp=args.tp,
            sl=args.sl,
            spread=spread,
            min_confidence=args.min_confidence,
            politician_bias=args.politician_bias,
            weights=weights,
            confirmation_mode=args.confirmation_mode,
            min_confirmations=args.min_confirmations,
            confirmation_weight_threshold=args.confirmation_weight_threshold,
            oos_ratio=args.oos_ratio,
            sweep_weights=args.sweep_weights,
            pattern_mode=args.pattern_mode,
        )
        return

    print(f"\n{'═'*58}")
    print(f"  BACKTEST  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*58}")
    print(f"  Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"  Period:          {args.period}")
    print(f"  Cash/ticker:     ${args.cash:,.0f}")
    print(f"  TP / SL:         {args.tp}% / –{args.sl}%")
    print(f"  Min confidence:  {args.min_confidence}")
    print(f"  Commission:      {args.commission * 100:.2f}%")
    print(f"  Politician bias: {args.politician_bias}")
    print(f"  Slippage:        {args.slippage_bps:.1f} bps")
    print(f"  Entry model:     {args.entry_model}")
    if args.entry_model == "confirmed" or args.compare_oos:
        print(
            "  Confirmation:    "
            f"{args.confirmation_mode} "
            f"(min={args.min_confirmations}, weight_thr={args.confirmation_weight_threshold})"
        )
        print(
            "  Indicators:      "
            f"RSI({RSI_PERIOD}), MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}), "
            f"BB({BB_PERIOD},{BB_STD}), MA slope({TREND_MA_PERIOD})"
        )
    print(f"  Ensemble weights: {weights}")
    print(
        "  Portfolio model: "
        f"{args.allocation_model} "
        f"(max_ticker={args.max_weight_per_ticker:.2f}, "
        f"max_sector={args.max_sector_concentration:.2f}, "
        f"max_open_risk={args.max_total_open_risk:.2f})"
    )
    print(f"  Pattern mode:    {args.pattern_mode} (weight={args.pattern_weight:.2f})")
    print(f"{'─'*58}")

    run = _run_model(
        tickers=tickers,
        period=args.period,
        cash=args.cash,
        commission=args.commission,
        tp=args.tp,
        sl=args.sl,
        min_confidence=args.min_confidence,
        politician_bias=args.politician_bias,
        weights=weights,
        entry_model=args.entry_model,
        confirmation_mode=args.confirmation_mode,
        min_confirmations=args.min_confirmations,
        confirmation_weight_threshold=args.confirmation_weight_threshold,
        pattern_mode=args.pattern_mode,
        pattern_weight=args.pattern_weight,
        spread=spread,
        label="Main Backtest",
    )

    if not run.rows:
        print("\n  ❌  No tickers produced results.")
        return

    # ── Save equity curve PNG ─────────────────────────────────────────────────
    if run.equity_curves:
        _save_equity_curve(
            run.equity_curves,
            ROOT / "backtest_equity_curve.png",
            title_prefix=f"{args.entry_model.title()} Strategy",
        )

    # ── Save CSV ──────────────────────────────────────────────────────────────
    csv_path = ROOT / "backtest_results.csv"
    pd.DataFrame(run.rows).set_index("ticker").to_csv(str(csv_path))
    print(f"  📄 Results CSV  →  backtest_results.csv")

    # ── Save / open HTML interactive report (last ticker) ────────────────────
    if run.last_bt is not None:
        html_path = ROOT / "backtest_report.html"
        try:
            run.last_bt.plot(
                filename=str(html_path),
                open_browser=args.open_html,
            )
            flag = "" if not args.open_html else "  (opening in browser…)"
            print(f"  🌐 HTML report  →  backtest_report.html{flag}")
        except Exception as exc:
            print(f"  ⚠️  HTML report skipped (Bokeh error): {exc}")

    # ── Diversified portfolio comparison (Week 4) ────────────────────────────
    if len(run.equity_curves) >= 2:
        print(f"\n{'═'*58}")
        print("  DIVERSIFICATION VALIDATION (portfolio vs single ticker)")
        print(f"{'═'*58}")

        comp = _compare_diversified_vs_single(
            equity_curves=run.equity_curves,
            allocation_model=args.allocation_model,
            max_weight_per_ticker=args.max_weight_per_ticker,
            max_sector_concentration=args.max_sector_concentration,
            max_total_open_risk_pct=args.max_total_open_risk,
            stop_loss_pct=args.sl,
            single_ticker=args.single_ticker_baseline,
        )

        if comp.get("status") == "ok":
            pm = comp["portfolio_metrics"]
            sm = comp["single_metrics"]
            single = comp["single_ticker"]

            print(
                f"  Diversified: return={pm['total_return_pct']:.2f}% | "
                f"CAGR={pm['cagr_pct']:.2f}% | maxDD={pm['max_drawdown_pct']:.2f}% | "
                f"Sharpe={pm['sharpe']:.2f} | Sortino={pm['sortino']:.2f}"
            )
            print(
                f"  Single ({single}): return={sm['total_return_pct']:.2f}% | "
                f"CAGR={sm['cagr_pct']:.2f}% | maxDD={sm['max_drawdown_pct']:.2f}% | "
                f"Sharpe={sm['sharpe']:.2f} | Sortino={sm['sortino']:.2f}"
            )

            if comp["improved"]:
                print("  ✅ Diversified portfolio improved risk-adjusted performance vs single ticker.")
            else:
                print("  ⚠️  Diversified portfolio did not beat single ticker on all risk-adjusted metrics.")

            comp_df = pd.DataFrame(
                [
                    {"model": "diversified", **pm},
                    {"model": f"single_{single}", **sm},
                ]
            ).set_index("model")
            comp_df.to_csv(str(ROOT / "backtest_diversification_comparison.csv"))
            print("  📄 Diversification CSV  →  backtest_diversification_comparison.csv")

            p_eq = comp["portfolio_equity"]
            s_eq = comp["single_equity"]
            plt.figure(figsize=(12, 5))
            plt.plot(p_eq.index, p_eq.values, label="Diversified Portfolio", linewidth=2)
            plt.plot(s_eq.index, s_eq.values, label=f"Single {single}", linewidth=1.5)
            plt.title("Diversified vs Single-Ticker Equity (base=100)")
            plt.ylabel("Equity")
            plt.xlabel("Date")
            plt.legend()
            plt.tight_layout()
            plt.savefig(str(ROOT / "backtest_diversification_curve.png"), dpi=150)
            plt.close()
            print("  📊 Diversification curve  →  backtest_diversification_curve.png")
        else:
            print("  ⚠️  Diversification comparison skipped (insufficient curve data).")
    else:
        print("  ⚠️  Diversification comparison skipped (need at least 2 valid tickers).")

    # ── OOS comparison: baseline vs confirmed ────────────────────────────────
    if args.compare_oos:
        print(f"\n{'═'*58}")
        print(f"  OOS VALIDATION  (baseline vs confirmed, ratio={args.oos_ratio:.2f})")
        print(f"{'═'*58}")

        baseline_rows: list[dict] = []
        confirmed_rows: list[dict] = []

        for ticker in tickers:
            df_full = _fetch_ohlcv(ticker, args.period)
            if df_full is None or len(df_full) < 120:
                continue

            _, oos_df = _split_oos(df_full, args.oos_ratio)
            if len(oos_df) < 60:
                continue

            sig_base = _compute_signals(
                oos_df["Close"],
                weights=weights,
                min_confidence=args.min_confidence,
                politician_bias=args.politician_bias,
                entry_model="baseline",
                confirmation_mode=args.confirmation_mode,
                min_confirmations=args.min_confirmations,
                confirmation_weight_threshold=args.confirmation_weight_threshold,
            )
            df_base = pd.concat([oos_df.copy(), sig_base[["entry_signal", "exit_signal"]]], axis=1)
            stats_base = Backtest(
                df_base,
                BotStrategy,
                cash=args.cash,
                commission=args.commission,
                exclusive_orders=True,
                trade_on_close=False,
            ).run(tp_pct=args.tp, sl_pct=args.sl)
            baseline_rows.append(_extract_row(ticker, stats_base))

            sig_conf = _compute_signals(
                oos_df["Close"],
                weights=weights,
                min_confidence=args.min_confidence,
                politician_bias=args.politician_bias,
                entry_model="confirmed",
                confirmation_mode=args.confirmation_mode,
                min_confirmations=args.min_confirmations,
                confirmation_weight_threshold=args.confirmation_weight_threshold,
            )
            df_conf = pd.concat([oos_df.copy(), sig_conf[["entry_signal", "exit_signal"]]], axis=1)
            stats_conf = Backtest(
                df_conf,
                BotStrategy,
                cash=args.cash,
                commission=args.commission,
                spread=spread,
                exclusive_orders=True,
                trade_on_close=False,
            ).run(tp_pct=args.tp, sl_pct=args.sl)
            confirmed_rows.append(_extract_row(ticker, stats_conf))

        if baseline_rows and confirmed_rows:
            b = _aggregate_model(baseline_rows)
            c = _aggregate_model(confirmed_rows)

            print(f"  Baseline OOS:  avg_return={b['avg_return']:.2f}% | avg_sharpe={b['avg_sharpe']:.2f} | avg_max_dd={b['avg_max_dd']:.2f}% | trades={b['total_trades']}")
            print(f"  Confirmed OOS: avg_return={c['avg_return']:.2f}% | avg_sharpe={c['avg_sharpe']:.2f} | avg_max_dd={c['avg_max_dd']:.2f}% | trades={c['total_trades']}")

            beats_return = c["avg_return"] > b["avg_return"]
            beats_sharpe = c["avg_sharpe"] > b["avg_sharpe"]
            better_dd = c["avg_max_dd"] > b["avg_max_dd"]

            if beats_return and beats_sharpe and better_dd:
                print("  ✅ OOS verdict: confirmed model beats baseline (return, Sharpe, and drawdown).")
            else:
                print("  ⚠️  OOS verdict: confirmed model did not beat baseline on all core metrics.")

            compare_df = pd.DataFrame(
                [
                    {"model": "baseline", **b},
                    {"model": "confirmed", **c},
                ]
            ).set_index("model")
            compare_df.to_csv(str(ROOT / "backtest_oos_comparison.csv"))
            print("  📄 OOS comparison CSV  →  backtest_oos_comparison.csv")
        else:
            print("  ⚠️  OOS comparison skipped due to insufficient OOS data.")

    # ── Pattern module OOS validation with costs/slippage ────────────────────
    if args.compare_pattern_oos:
        print(f"\n{'═'*58}")
        print("  PATTERN OOS VALIDATION (numeric-only vs numeric+pattern)")
        print(f"{'═'*58}")

        numeric_rows: list[dict] = []
        pattern_rows: list[dict] = []

        for ticker in tickers:
            df_full = _fetch_ohlcv(ticker, args.period)
            if df_full is None or len(df_full) < 140:
                continue
            _, oos_df = _split_oos(df_full, args.oos_ratio)
            if len(oos_df) < 70:
                continue

            sig_numeric = _compute_signals(
                oos_df["Close"],
                weights=weights,
                min_confidence=args.min_confidence,
                politician_bias=args.politician_bias,
                entry_model="confirmed",
                confirmation_mode=args.confirmation_mode,
                min_confirmations=args.min_confirmations,
                confirmation_weight_threshold=args.confirmation_weight_threshold,
                ohlc=oos_df,
                pattern_mode="off",
                pattern_weight=args.pattern_weight,
            )
            df_num = pd.concat([oos_df.copy(), sig_numeric[["entry_signal", "exit_signal"]]], axis=1)
            st_num = Backtest(
                df_num,
                BotStrategy,
                cash=args.cash,
                commission=args.commission,
                spread=spread,
                exclusive_orders=True,
                trade_on_close=False,
            ).run(tp_pct=args.tp, sl_pct=args.sl)
            numeric_rows.append(_extract_row(ticker, st_num))

            sig_pattern = _compute_signals(
                oos_df["Close"],
                weights=weights,
                min_confidence=args.min_confidence,
                politician_bias=args.politician_bias,
                entry_model="confirmed",
                confirmation_mode=args.confirmation_mode,
                min_confirmations=args.min_confirmations,
                confirmation_weight_threshold=args.confirmation_weight_threshold,
                ohlc=oos_df,
                pattern_mode=(args.pattern_mode if args.pattern_mode != "off" else "confirm"),
                pattern_weight=args.pattern_weight,
            )
            df_pat = pd.concat([oos_df.copy(), sig_pattern[["entry_signal", "exit_signal"]]], axis=1)
            st_pat = Backtest(
                df_pat,
                BotStrategy,
                cash=args.cash,
                commission=args.commission,
                spread=spread,
                exclusive_orders=True,
                trade_on_close=False,
            ).run(tp_pct=args.tp, sl_pct=args.sl)
            pattern_rows.append(_extract_row(ticker, st_pat))

        if numeric_rows and pattern_rows:
            n = _aggregate_model(numeric_rows)
            p = _aggregate_model(pattern_rows)
            print(
                f"  Numeric-only OOS: return={n['avg_return']:.2f}% | sharpe={n['avg_sharpe']:.2f} | "
                f"maxDD={n['avg_max_dd']:.2f}% | trades={n['total_trades']}"
            )
            print(
                f"  Pattern OOS:      return={p['avg_return']:.2f}% | sharpe={p['avg_sharpe']:.2f} | "
                f"maxDD={p['avg_max_dd']:.2f}% | trades={p['total_trades']}"
            )

            improved = (
                p["avg_sharpe"] > n["avg_sharpe"]
                and p["avg_sortino"] > n["avg_sortino"]
                and p["avg_max_dd"] > n["avg_max_dd"]
            )
            if improved:
                print("  ✅ Pattern module improved OOS risk-adjusted metrics after costs/slippage.")
            else:
                print("  ⚠️  Pattern module did not improve all OOS risk-adjusted metrics.")

            pd.DataFrame(
                [
                    {"model": "numeric_only", **n},
                    {"model": "pattern_overlay", **p},
                ]
            ).set_index("model").to_csv(str(ROOT / "backtest_pattern_oos_comparison.csv"))
            print("  📄 Pattern OOS CSV  →  backtest_pattern_oos_comparison.csv")
        else:
            print("  ⚠️  Pattern OOS validation skipped due to insufficient OOS data.")

    print(f"\n✅ Backtest complete.\n")


if __name__ == "__main__":
    main()
