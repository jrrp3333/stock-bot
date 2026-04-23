"""
Optimization Agents for Trading Bot
Analyzes historical trades and provides dynamic optimization for:
- Take-profit and stop-loss levels
- Entry signal quality
- Position sizing
- Risk/reward ratios
"""

import sqlite3
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
import yfinance as yf

DB_PATH = Path(__file__).resolve().parent / "trades.db"
WEIGHTS_PATH = Path(__file__).resolve().parent / "ensemble_weights.json"
DEFAULT_AGENT_WEIGHTS = {
    "momentum_breakout": 0.34,
    "mean_reversion": 0.33,
    "volatility_regime": 0.33,
}


@dataclass
class TradeMetrics:
    """Container for trade performance metrics"""
    ticker: str
    entry_price: float
    exit_price: float
    qty: float
    pnl_percent: float
    duration_hours: float
    signal_source: str
    timestamp: str


@dataclass
class OptimizationRecommendation:
    """Container for optimization recommendations"""
    metric: str  # "take_profit", "stop_loss", "position_size", "entry_quality"
    current_value: float
    recommended_value: float
    confidence: float  # 0-1
    rationale: str


@dataclass
class AgentSignal:
    """Normalized output from a strategy agent."""
    agent_name: str
    ticker: str
    action: str  # buy, sell, hold
    score: float
    confidence: float
    rationale: str


def _safe_download_close_series(ticker: str, period: str = "1mo"):
    """Fetch close prices with defensive handling for API/data edge cases."""
    try:
        data = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
        if data.empty or "Close" not in data:
            return None

        close_data = data["Close"]
        # yfinance may return a 1-col DataFrame for some inputs; normalize to Series.
        if hasattr(close_data, "ndim") and close_data.ndim == 2:
            if close_data.shape[1] == 0:
                return None
            close_data = close_data.iloc[:, 0]

        series = close_data.astype(float).dropna()
        if len(series) < 5:
            return None
        return series
    except Exception:
        return None


def _safe_download_ohlcv(ticker: str, period: str, interval: str):
    """Fetch OHLCV with defensive handling for yfinance schema differences."""
    try:
        data = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if data.empty:
            return None

        required = ["Open", "High", "Low", "Close", "Volume"]
        for col in required:
            if col not in data:
                return None

        out = data[required].copy()
        for col in required:
            block = out[col]
            if hasattr(block, "ndim") and block.ndim == 2:
                if block.shape[1] == 0:
                    return None
                out[col] = block.iloc[:, 0]

        out = out.astype(float).dropna(how="any")
        return out if len(out) > 5 else None
    except Exception:
        return None


def _calc_rsi(series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - (100 / (1 + rs))


class MarketRegimeAgent:
    """Classifies broad market regime and returns confidence/size multipliers."""

    def __init__(self, benchmark: str = "SPY"):
        self.benchmark = benchmark

    def assess_market_regime(self) -> Dict[str, Any]:
        closes = _safe_download_close_series(self.benchmark, period="6mo")
        if closes is None or len(closes) < 60:
            return {
                "regime": "unknown",
                "confidence_multiplier": 1.0,
                "sizing_multiplier": 1.0,
                "reason": "Insufficient benchmark history for regime detection.",
            }

        ret_20d = float((closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] * 100)
        ma_50 = float(closes.rolling(50).mean().iloc[-1])
        price = float(closes.iloc[-1])
        vol_20 = float(closes.pct_change().tail(20).std() * np.sqrt(252))

        if ret_20d > 2.5 and price > ma_50 and vol_20 < 0.30:
            return {
                "regime": "risk_on",
                "confidence_multiplier": 1.08,
                "sizing_multiplier": 1.10,
                "reason": f"{self.benchmark} trend positive with contained volatility.",
            }
        if ret_20d < -2.5 and price < ma_50 and vol_20 > 0.35:
            return {
                "regime": "risk_off",
                "confidence_multiplier": 0.90,
                "sizing_multiplier": 0.80,
                "reason": f"{self.benchmark} downtrend with elevated volatility.",
            }
        return {
            "regime": "chop",
            "confidence_multiplier": 0.97,
            "sizing_multiplier": 0.92,
            "reason": f"{self.benchmark} in mixed trend/volatility regime.",
        }


class IntradayTimingAgent:
    """Evaluates intraday timing quality from 15m and 60m structure."""

    def evaluate_entry_timing(self, ticker: str) -> Dict[str, Any]:
        bars_15m = _safe_download_ohlcv(ticker, period="5d", interval="15m")
        bars_1h = _safe_download_ohlcv(ticker, period="1mo", interval="60m")

        if bars_15m is None or bars_1h is None or len(bars_15m) < 30 or len(bars_1h) < 30:
            return {
                "passed": True,
                "confidence_delta": 0.0,
                "buy_score_delta": 0.0,
                "sell_score_delta": 0.0,
                "reason": "Insufficient intraday history; no timing adjustment applied.",
            }

        close_15 = bars_15m["Close"].astype(float)
        close_1h = bars_1h["Close"].astype(float)
        rsi_15 = float(_calc_rsi(close_15, period=14).iloc[-1])

        ret_1h_8 = float((close_1h.iloc[-1] - close_1h.iloc[-9]) / close_1h.iloc[-9] * 100)
        short_high = float(close_15.tail(16).max())
        pullback_pct = 0.0
        if short_high > 0:
            pullback_pct = float((short_high - close_15.iloc[-1]) / short_high * 100)

        buy_score_delta = 0.0
        sell_score_delta = 0.0
        confidence_delta = 0.0
        reason = []

        if ret_1h_8 > 0:
            buy_score_delta += 0.05
            reason.append("1h trend up")
        elif ret_1h_8 < 0:
            sell_score_delta += 0.04
            reason.append("1h trend down")

        if 35 <= rsi_15 <= 62:
            buy_score_delta += 0.04
            confidence_delta += 0.02
            reason.append("15m RSI in constructive zone")
        elif rsi_15 >= 75:
            sell_score_delta += 0.06
            confidence_delta -= 0.03
            reason.append("15m RSI overbought")
        elif rsi_15 <= 24:
            confidence_delta -= 0.02
            reason.append("15m RSI oversold extremes")

        if 0.2 <= pullback_pct <= 1.8 and ret_1h_8 > 0:
            buy_score_delta += 0.04
            reason.append("healthy intraday pullback")
        elif pullback_pct > 3.0:
            confidence_delta -= 0.03
            reason.append("deep pullback / potential breakdown")

        passed = confidence_delta > -0.08
        return {
            "passed": passed,
            "confidence_delta": round(confidence_delta, 3),
            "buy_score_delta": round(buy_score_delta, 3),
            "sell_score_delta": round(sell_score_delta, 3),
            "reason": "; ".join(reason) if reason else "neutral intraday timing",
            "rsi_15": round(rsi_15, 2),
            "pullback_pct": round(pullback_pct, 2),
            "ret_1h_8_pct": round(ret_1h_8, 2),
        }


class TradePacingAgent:
    """Adapts aggressiveness to maintain target trade cadence without overtrading."""

    def get_pacing_adjustment(
        self,
        completed_trades: int,
        target_trades_per_day: int,
        max_trades_per_day: int,
        recent_win_rate_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        if completed_trades >= max_trades_per_day:
            return {
                "veto": True,
                "confidence_delta": -0.5,
                "sizing_multiplier": 0.6,
                "reason": "Max daily trade cap reached.",
            }

        pace_ratio = completed_trades / max(1, target_trades_per_day)
        confidence_delta = 0.0
        sizing_multiplier = 1.0
        reason_parts = []

        if pace_ratio < 0.40:
            confidence_delta += 0.04
            sizing_multiplier *= 1.06
            reason_parts.append("below target cadence")
        elif pace_ratio > 1.00:
            confidence_delta -= 0.07
            sizing_multiplier *= 0.88
            reason_parts.append("above target cadence")

        if recent_win_rate_pct is not None:
            if recent_win_rate_pct < 40:
                confidence_delta -= 0.08
                sizing_multiplier *= 0.82
                reason_parts.append("recent win rate weak")
            elif recent_win_rate_pct > 62:
                confidence_delta += 0.02
                sizing_multiplier *= 1.04
                reason_parts.append("recent win rate strong")

        return {
            "veto": False,
            "confidence_delta": round(confidence_delta, 3),
            "sizing_multiplier": round(max(0.7, min(1.2, sizing_multiplier)), 3),
            "reason": "; ".join(reason_parts) if reason_parts else "pacing neutral",
        }


class ExecutionQualityAgent:
    """Estimates intraday execution quality and whether to throttle or skip entries."""

    def assess_execution(self, ticker: str, proposed_notional: float) -> Dict[str, Any]:
        bars_1m = _safe_download_ohlcv(ticker, period="1d", interval="1m")
        if bars_1m is None or len(bars_1m) < 35:
            return {
                "mode": "market",
                "allowed": True,
                "confidence_delta": 0.0,
                "sizing_multiplier": 1.0,
                "reason": "No intraday microstructure sample; default market mode.",
            }

        tail = bars_1m.tail(30)
        close = tail["Close"].astype(float)
        high = tail["High"].astype(float)
        low = tail["Low"].astype(float)
        vol = tail["Volume"].astype(float)

        spread_proxy = float(((high - low) / close.replace(0.0, np.nan)).mean() * 100)
        dollar_vol = float((close * vol).mean())
        notional_ratio = float(proposed_notional / max(1.0, dollar_vol))

        if spread_proxy > 1.2 or notional_ratio > 0.08:
            return {
                "mode": "skip",
                "allowed": False,
                "confidence_delta": -0.12,
                "sizing_multiplier": 0.7,
                "reason": (
                    f"Execution stress high (spread~{spread_proxy:.2f}%, "
                    f"notional/liquidity~{notional_ratio:.3f})."
                ),
            }

        if spread_proxy > 0.55 or notional_ratio > 0.035:
            return {
                "mode": "limit_preferred",
                "allowed": True,
                "confidence_delta": -0.03,
                "sizing_multiplier": 0.9,
                "reason": (
                    f"Execution caution (spread~{spread_proxy:.2f}%, "
                    f"notional/liquidity~{notional_ratio:.3f})."
                ),
            }

        return {
            "mode": "market",
            "allowed": True,
            "confidence_delta": 0.01,
            "sizing_multiplier": 1.02,
            "reason": (
                f"Execution healthy (spread~{spread_proxy:.2f}%, "
                f"notional/liquidity~{notional_ratio:.3f})."
            ),
        }


class AdaptiveWeightingAgent:
    """Blends baseline and new weights with EWMA and bounded weekly drift."""

    def _normalize(self, weights: Dict[str, float], min_weight: float) -> Dict[str, float]:
        out = {k: max(min_weight, float(v)) for k, v in weights.items()}
        total = sum(out.values())
        if total <= 0:
            n = max(1, len(out))
            return {k: round(1.0 / n, 4) for k in out}
        return {k: round(v / total, 4) for k, v in out.items()}

    def blend_weights(
        self,
        previous_weights: Dict[str, float],
        proposed_weights: Dict[str, float],
        alpha: float = 0.35,
        max_weekly_drift: float = 0.08,
        min_weight: float = 0.05,
    ) -> Dict[str, float]:
        alpha = max(0.05, min(0.95, float(alpha)))
        max_weekly_drift = max(0.01, min(0.5, float(max_weekly_drift)))

        keys = sorted(set(previous_weights.keys()) | set(proposed_weights.keys()))
        prev = self._normalize({k: previous_weights.get(k, 0.0) for k in keys}, min_weight=min_weight)
        prop = self._normalize({k: proposed_weights.get(k, 0.0) for k in keys}, min_weight=min_weight)

        blended = {}
        for k in keys:
            raw = (1 - alpha) * prev[k] + alpha * prop[k]
            lower = max(min_weight, prev[k] - max_weekly_drift)
            upper = min(1.0, prev[k] + max_weekly_drift)
            blended[k] = min(upper, max(lower, raw))

        return self._normalize(blended, min_weight=min_weight)


class MomentumBreakoutAgent:
    """Trend-following agent that looks for short-term breakout continuation."""

    def __init__(self, min_history_days: int = 15):
        self.min_history_days = min_history_days

    def generate_signal(self, ticker: str) -> AgentSignal:
        closes = _safe_download_close_series(ticker, period="2mo")
        if closes is None or len(closes) < self.min_history_days:
            return AgentSignal(
                agent_name="momentum_breakout",
                ticker=ticker,
                action="hold",
                score=0.0,
                confidence=0.2,
                rationale="Insufficient price history for momentum breakout model.",
            )

        sma_fast = float(closes.rolling(5).mean().iloc[-1])
        sma_slow = float(closes.rolling(12).mean().iloc[-1])
        ret_3d = float((closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4] * 100)

        trend_score = 0.0
        if sma_fast > sma_slow:
            trend_score += 0.55
        if ret_3d > 0:
            trend_score += min(0.35, ret_3d / 10)

        score = max(0.0, min(1.0, trend_score))
        confidence = 0.45 + min(0.45, len(closes) / 120)

        if score >= 0.62:
            action = "buy"
        elif score <= 0.22:
            action = "sell"
        else:
            action = "hold"

        return AgentSignal(
            agent_name="momentum_breakout",
            ticker=ticker,
            action=action,
            score=round(score, 3),
            confidence=round(confidence, 3),
            rationale=(
                f"SMA5 {'>' if sma_fast > sma_slow else '<='} SMA12; 3d return {ret_3d:.2f}%"
            ),
        )


class MeanReversionAgent:
    """Counter-trend agent that buys oversold conditions and sells overextended rallies."""

    def generate_signal(self, ticker: str) -> AgentSignal:
        closes = _safe_download_close_series(ticker, period="3mo")
        if closes is None or len(closes) < 20:
            return AgentSignal(
                agent_name="mean_reversion",
                ticker=ticker,
                action="hold",
                score=0.0,
                confidence=0.2,
                rationale="Insufficient price history for mean-reversion model.",
            )

        window = closes.tail(20)
        mean = float(window.mean())
        std = float(window.std())
        if std <= 0:
            return AgentSignal(
                agent_name="mean_reversion",
                ticker=ticker,
                action="hold",
                score=0.0,
                confidence=0.2,
                rationale="Price variance too low for z-score evaluation.",
            )

        z_score = (float(closes.iloc[-1]) - mean) / std
        # Lower z_score is better for long entries.
        buy_score = max(0.0, min(1.0, (1.3 - z_score) / 2.6))
        sell_score = max(0.0, min(1.0, (z_score - 0.9) / 2.2))

        if buy_score >= 0.65:
            action = "buy"
            score = buy_score
        elif sell_score >= 0.65:
            action = "sell"
            score = sell_score
        else:
            action = "hold"
            score = max(buy_score, sell_score) * 0.5

        confidence = 0.5 + min(0.35, len(closes) / 180)
        return AgentSignal(
            agent_name="mean_reversion",
            ticker=ticker,
            action=action,
            score=round(float(score), 3),
            confidence=round(float(confidence), 3),
            rationale=f"20d z-score: {z_score:.2f}",
        )


class VolatilityRegimeAgent:
    """Risk-gating agent that scales conviction based on volatility regime."""

    def generate_signal(self, ticker: str) -> AgentSignal:
        closes = _safe_download_close_series(ticker, period="3mo")
        if closes is None or len(closes) < 30:
            return AgentSignal(
                agent_name="volatility_regime",
                ticker=ticker,
                action="hold",
                score=0.0,
                confidence=0.25,
                rationale="Insufficient history for volatility regime assessment.",
            )

        returns = closes.pct_change().dropna()
        vol_10 = float(returns.tail(10).std() * np.sqrt(252))
        vol_30 = float(returns.tail(30).std() * np.sqrt(252))
        trend_10 = float((closes.iloc[-1] - closes.iloc[-11]) / closes.iloc[-11] * 100)

        if vol_10 > max(0.7, vol_30 * 1.25) and trend_10 < 0:
            action = "sell"
            score = 0.75
        elif vol_10 < max(0.22, vol_30 * 0.95) and trend_10 > 0:
            action = "buy"
            score = 0.62
        else:
            action = "hold"
            score = 0.3

        confidence = 0.45 + min(0.4, len(closes) / 180)
        return AgentSignal(
            agent_name="volatility_regime",
            ticker=ticker,
            action=action,
            score=round(score, 3),
            confidence=round(confidence, 3),
            rationale=f"10d vol {vol_10:.2f}, 30d vol {vol_30:.2f}, 10d trend {trend_10:.2f}%",
        )


class EnsembleTradingAgent:
    """Combines multiple strategy agents into a single buy/sell/hold decision."""

    def __init__(self, weights_path: Path = WEIGHTS_PATH):
        self.weights_path = Path(weights_path)
        self.agents = [
            MomentumBreakoutAgent(),
            MeanReversionAgent(),
            VolatilityRegimeAgent(),
        ]
        self.weights = self._load_weights()

    def _normalize_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        normalized = {}
        for name in self._agent_names():
            normalized[name] = max(0.01, float(weights.get(name, DEFAULT_AGENT_WEIGHTS.get(name, 0.33))))

        total = sum(normalized.values())
        if total <= 0:
            return dict(DEFAULT_AGENT_WEIGHTS)

        for key in normalized:
            normalized[key] = round(normalized[key] / total, 4)
        return normalized

    def _agent_names(self) -> List[str]:
        return ["momentum_breakout", "mean_reversion", "volatility_regime"]

    def _load_weights(self) -> Dict[str, float]:
        if not self.weights_path.exists():
            return dict(DEFAULT_AGENT_WEIGHTS)

        try:
            payload = json.loads(self.weights_path.read_text(encoding="utf-8"))
            stored = payload.get("weights", {}) if isinstance(payload, dict) else {}
            merged = {name: stored.get(name, DEFAULT_AGENT_WEIGHTS[name]) for name in self._agent_names()}
            return self._normalize_weights(merged)
        except Exception:
            return dict(DEFAULT_AGENT_WEIGHTS)

    def reload_weights(self) -> None:
        self.weights = self._load_weights()

    def get_weights(self) -> Dict[str, float]:
        return dict(self.weights)

    def set_weights(self, new_weights: Dict[str, float], save: bool = True, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.weights = self._normalize_weights(new_weights)
        if not save:
            return

        payload = {
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "weights": self.weights,
        }
        if metadata:
            payload.update(metadata)

        self.weights_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def evaluate_ticker(self, ticker: str, external_buy_bias: float = 0.0) -> Dict[str, Any]:
        signals = [agent.generate_signal(ticker) for agent in self.agents]

        buy_score = 0.0
        sell_score = 0.0
        confidence_weight_sum = 0.0
        reasons: List[str] = []

        for signal in signals:
            agent_weight = self.weights.get(signal.agent_name, 0.33)
            weighted = signal.score * signal.confidence * agent_weight
            confidence_weight_sum += signal.confidence
            reasons.append(f"{signal.agent_name}: {signal.action} ({signal.rationale})")

            if signal.action == "buy":
                buy_score += weighted
            elif signal.action == "sell":
                sell_score += weighted

        if external_buy_bias > 0:
            buy_score += external_buy_bias
            reasons.append(f"external_bias: +{external_buy_bias:.2f} from non-price signals")

        confidence = min(0.98, (confidence_weight_sum / max(1, len(signals))) + 0.1)

        if buy_score >= sell_score and buy_score >= 0.55:
            action = "buy"
        elif sell_score > buy_score and sell_score >= 0.55:
            action = "sell"
        else:
            action = "hold"

        return {
            "ticker": ticker,
            "action": action,
            "buy_score": round(float(buy_score), 3),
            "sell_score": round(float(sell_score), 3),
            "confidence": round(float(confidence), 3),
            "signals": [
                {
                    "agent": s.agent_name,
                    "action": s.action,
                    "ensemble_weight": self.weights.get(s.agent_name, 0.33),
                    "score": s.score,
                    "confidence": s.confidence,
                    "rationale": s.rationale,
                }
                for s in signals
            ],
            "reasons": reasons,
            "weights": self.get_weights(),
        }


def _history_signal(agent_name: str, closes) -> Tuple[str, float]:
    """Generate an agent-like signal from historical weekly closes for walk-forward tests."""
    if closes is None or len(closes) < 12:
        return "hold", 0.0

    if agent_name == "momentum_breakout":
        sma_fast = float(closes.tail(5).mean())
        sma_slow = float(closes.tail(12).mean())
        ret_3 = float((closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4]) if len(closes) >= 4 else 0.0
        score = 0.0
        if sma_fast > sma_slow:
            score += 0.55
        if ret_3 > 0:
            score += min(0.35, ret_3 * 5)
        score = max(0.0, min(1.0, score))
        if score >= 0.62:
            return "buy", score
        if score <= 0.22:
            return "sell", max(0.2, 1.0 - score)
        return "hold", score * 0.4

    if agent_name == "mean_reversion":
        window = closes.tail(20) if len(closes) >= 20 else closes
        mean = float(window.mean())
        std = float(window.std())
        if std <= 0:
            return "hold", 0.0
        z_score = (float(closes.iloc[-1]) - mean) / std
        buy_score = max(0.0, min(1.0, (1.3 - z_score) / 2.6))
        sell_score = max(0.0, min(1.0, (z_score - 0.9) / 2.2))
        if buy_score >= 0.65:
            return "buy", buy_score
        if sell_score >= 0.65:
            return "sell", sell_score
        return "hold", max(buy_score, sell_score) * 0.4

    # volatility_regime
    returns = closes.pct_change().dropna()
    if len(returns) < 10:
        return "hold", 0.0
    vol_6 = float(returns.tail(6).std() * np.sqrt(52))
    vol_12 = float(returns.tail(12).std() * np.sqrt(52)) if len(returns) >= 12 else vol_6
    trend_4 = float((closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5]) if len(closes) >= 5 else 0.0
    if vol_6 > max(0.65, vol_12 * 1.2) and trend_4 < 0:
        return "sell", 0.75
    if vol_6 < max(0.2, vol_12 * 0.95) and trend_4 > 0:
        return "buy", 0.62
    return "hold", 0.25


class PaperBacktestHarness:
    """Walk-forward paper backtest harness for agent scoring and weekly reweighting."""

    def __init__(self, watchlist: List[str], weights_path: Path = WEIGHTS_PATH):
        self.watchlist = list(dict.fromkeys([w.upper() for w in watchlist]))
        self.weights_path = Path(weights_path)
        self.agent_names = ["momentum_breakout", "mean_reversion", "volatility_regime"]

    def _download_weekly_closes(self, ticker: str):
        try:
            data = yf.download(ticker, period="18mo", interval="1d", progress=False, auto_adjust=True)
            if data.empty or "Close" not in data:
                return None
            close_data = data["Close"]
            if hasattr(close_data, "ndim") and close_data.ndim == 2:
                if close_data.shape[1] == 0:
                    return None
                close_data = close_data.iloc[:, 0]
            closes = close_data.astype(float).dropna()
            weekly = closes.resample("W-FRI").last().dropna()
            return weekly if len(weekly) > 30 else None
        except Exception:
            return None

    def evaluate_out_of_sample(self, lookback_weeks: int = 26, oos_weeks: int = 8) -> Dict[str, Any]:
        per_agent_returns = {name: [] for name in self.agent_names}

        for ticker in self.watchlist:
            weekly = self._download_weekly_closes(ticker)
            if weekly is None:
                continue

            needed = lookback_weeks + oos_weeks + 2
            if len(weekly) < needed:
                continue

            start_idx = len(weekly) - oos_weeks - 1
            for i in range(start_idx, len(weekly) - 1):
                history = weekly.iloc[: i + 1]
                next_ret = float((weekly.iloc[i + 1] - weekly.iloc[i]) / weekly.iloc[i])

                for agent_name in self.agent_names:
                    action, conviction = _history_signal(agent_name, history)
                    realized = 0.0
                    if action == "buy":
                        realized = next_ret * conviction
                    elif action == "sell":
                        realized = -next_ret * conviction
                    per_agent_returns[agent_name].append(realized)

        agent_scores = {}
        strengths = {}
        for agent_name, returns in per_agent_returns.items():
            if not returns:
                agent_scores[agent_name] = {
                    "samples": 0,
                    "avg_return_pct": 0.0,
                    "hit_rate_pct": 0.0,
                    "score": 0.0,
                }
                strengths[agent_name] = 0.01
                continue

            avg_r = float(np.mean(returns))
            hit = float(sum(1 for r in returns if r > 0) / len(returns))
            std_r = float(np.std(returns)) if len(returns) > 1 else 0.0
            stability = avg_r / std_r if std_r > 1e-9 else avg_r
            score = max(0.0, (avg_r * 100) * 0.7 + (hit * 100 - 50) * 0.3 + stability * 0.1)

            agent_scores[agent_name] = {
                "samples": len(returns),
                "avg_return_pct": round(avg_r * 100, 4),
                "hit_rate_pct": round(hit * 100, 2),
                "score": round(score, 4),
            }
            strengths[agent_name] = max(0.01, score)

        total_strength = sum(strengths.values())
        new_weights = {
            k: round(v / total_strength, 4) for k, v in strengths.items()
        } if total_strength > 0 else dict(DEFAULT_AGENT_WEIGHTS)

        return {
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "lookback_weeks": lookback_weeks,
            "oos_weeks": oos_weeks,
            "watchlist_size": len(self.watchlist),
            "agent_scores": agent_scores,
            "weights": new_weights,
        }

    def reweight_and_persist(
        self,
        lookback_weeks: int = 26,
        oos_weeks: int = 8,
        adaptive: bool = True,
        ewma_alpha: float = 0.35,
        max_weekly_drift: float = 0.08,
    ) -> Dict[str, Any]:
        result = self.evaluate_out_of_sample(lookback_weeks=lookback_weeks, oos_weeks=oos_weeks)

        proposed_weights = result.get("weights", dict(DEFAULT_AGENT_WEIGHTS))
        final_weights = dict(proposed_weights)
        method = "walk_forward_oos"

        if adaptive:
            prev_state = get_ensemble_weight_state(self.weights_path)
            previous_weights = prev_state.get("weights", dict(DEFAULT_AGENT_WEIGHTS))
            blender = AdaptiveWeightingAgent()
            final_weights = blender.blend_weights(
                previous_weights=previous_weights,
                proposed_weights=proposed_weights,
                alpha=ewma_alpha,
                max_weekly_drift=max_weekly_drift,
            )
            method = "walk_forward_oos_adaptive_ewma"

        payload = {
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "last_reweight_date": datetime.utcnow().date().isoformat(),
            "method": method,
            "weights": final_weights,
            "backtest": result,
            "proposed_weights": proposed_weights,
            "adaptive": {
                "enabled": bool(adaptive),
                "ewma_alpha": float(ewma_alpha),
                "max_weekly_drift": float(max_weekly_drift),
            },
        }
        self.weights_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload


def get_ensemble_weight_state(weights_path: Path = WEIGHTS_PATH) -> Dict[str, Any]:
    path = Path(weights_path)
    if not path.exists():
        return {
            "updated_at": None,
            "last_reweight_date": None,
            "method": "default",
            "weights": dict(DEFAULT_AGENT_WEIGHTS),
            "backtest": None,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Invalid weight payload")
        payload.setdefault("weights", dict(DEFAULT_AGENT_WEIGHTS))
        return payload
    except Exception:
        return {
            "updated_at": None,
            "last_reweight_date": None,
            "method": "default_fallback",
            "weights": dict(DEFAULT_AGENT_WEIGHTS),
            "backtest": None,
        }


class TradeAnalysisAgent:
    """Analyzes historical trades to extract patterns and metrics"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def get_closed_trades(self, lookback_days: int = 90) -> List[TradeMetrics]:
        """Retrieve all closed trades from the database"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

            cur.execute(
                """
                SELECT id, timestamp, ticker, entry_price, exit_price, qty, signal_source, pnl_percent
                FROM trades
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                """,
                (cutoff_date,),
            )

            trades = []
            for row in cur.fetchall():
                _, ts, ticker, entry, exit_p, qty, sig_src, pnl = row
                try:
                    entry_dt = datetime.fromisoformat(ts)
                    duration = (datetime.utcnow() - entry_dt).total_seconds() / 3600
                except:
                    duration = 0

                trades.append(
                    TradeMetrics(
                        ticker=ticker,
                        entry_price=entry,
                        exit_price=exit_p,
                        qty=qty,
                        pnl_percent=pnl,
                        duration_hours=duration,
                        signal_source=sig_src,
                        timestamp=ts,
                    )
                )

            return trades
        finally:
            conn.close()

    def calculate_win_rate(self, trades: List[TradeMetrics]) -> float:
        """Calculate win rate percentage"""
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.pnl_percent > 0)
        return (wins / len(trades)) * 100

    def calculate_avg_win_loss(self, trades: List[TradeMetrics]) -> Tuple[float, float]:
        """Calculate average win and average loss percentages"""
        winning = [t.pnl_percent for t in trades if t.pnl_percent > 0]
        losing = [t.pnl_percent for t in trades if t.pnl_percent < 0]

        avg_win = np.mean(winning) if winning else 0.0
        avg_loss = np.mean(losing) if losing else 0.0

        return avg_win, avg_loss

    def calculate_profit_factor(self, trades: List[TradeMetrics]) -> float:
        """Calculate profit factor (gross profit / gross loss)"""
        if not trades:
            return 0.0

        gross_profit = sum(t.pnl_percent for t in trades if t.pnl_percent > 0)
        gross_loss = abs(sum(t.pnl_percent for t in trades if t.pnl_percent < 0))

        if gross_loss == 0:
            return 1.0 if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def analyze_by_signal_source(self, trades: List[TradeMetrics]) -> Dict[str, Dict]:
        """Analyze performance by signal source"""
        by_source = {}

        for trade in trades:
            src = trade.signal_source
            if src not in by_source:
                by_source[src] = {"trades": [], "win_rate": 0, "avg_pnl": 0}
            by_source[src]["trades"].append(trade)

        for src in by_source:
            source_trades = by_source[src]["trades"]
            by_source[src]["win_rate"] = self.calculate_win_rate(source_trades)
            by_source[src]["avg_pnl"] = np.mean([t.pnl_percent for t in source_trades])
            by_source[src]["count"] = len(source_trades)

        return by_source

    def get_summary(self, trades: List[TradeMetrics]) -> Dict:
        """Get overall trade summary"""
        if not trades:
            return {"status": "No trades to analyze"}

        win_rate = self.calculate_win_rate(trades)
        avg_win, avg_loss = self.calculate_avg_win_loss(trades)
        profit_factor = self.calculate_profit_factor(trades)
        avg_duration = np.mean([t.duration_hours for t in trades])

        return {
            "total_trades": len(trades),
            "win_rate_pct": round(win_rate, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_duration_hours": round(avg_duration, 2),
        }


class DynamicTPSLAgent:
    """Recommends optimal take-profit and stop-loss levels"""

    def __init__(self, default_tp: float = 5.0, default_sl: float = -3.0):
        self.default_tp = default_tp
        self.default_sl = default_sl
        self.analysis_agent = TradeAnalysisAgent()

    def recommend_tp_sl(self, lookback_days: int = 90) -> Dict[str, OptimizationRecommendation]:
        """Generate TP/SL recommendations based on historical data"""
        trades = self.analysis_agent.get_closed_trades(lookback_days)

        if len(trades) < 10:
            return {
                "take_profit": OptimizationRecommendation(
                    metric="take_profit",
                    current_value=self.default_tp,
                    recommended_value=self.default_tp,
                    confidence=0.2,
                    rationale="Insufficient trade history for optimization (< 10 trades)",
                ),
                "stop_loss": OptimizationRecommendation(
                    metric="stop_loss",
                    current_value=self.default_sl,
                    recommended_value=self.default_sl,
                    confidence=0.2,
                    rationale="Insufficient trade history for optimization (< 10 trades)",
                ),
            }

        # Analyze win/loss distribution
        avg_win, avg_loss = self.analysis_agent.calculate_avg_win_loss(trades)
        win_rate = self.analysis_agent.calculate_win_rate(trades)

        # Recommend new TP: average win * risk adjustment factor
        recommended_tp = avg_win * 0.85 if avg_win > 0 else self.default_tp
        tp_confidence = min(0.95, (len(trades) / 100))

        # Recommend new SL: average loss * risk adjustment factor
        recommended_sl = avg_loss * 1.1 if avg_loss < 0 else self.default_sl
        sl_confidence = min(0.95, (len(trades) / 100))

        return {
            "take_profit": OptimizationRecommendation(
                metric="take_profit",
                current_value=self.default_tp,
                recommended_value=round(recommended_tp, 2),
                confidence=round(tp_confidence, 2),
                rationale=f"Based on avg win of {avg_win:.2f}% across {len(trades)} trades",
            ),
            "stop_loss": OptimizationRecommendation(
                metric="stop_loss",
                current_value=self.default_sl,
                recommended_value=round(recommended_sl, 2),
                confidence=round(sl_confidence, 2),
                rationale=f"Based on avg loss of {avg_loss:.2f}% across {len(trades)} trades",
            ),
        }


class RiskAnalysisAgent:
    """Analyzes volatility and recommends position sizing"""

    def __init__(self):
        self.analysis_agent = TradeAnalysisAgent()

    def get_ticker_volatility(self, ticker: str, period: str = "3mo") -> Optional[float]:
        """Calculate volatility (annualized standard deviation) for a ticker"""
        try:
            data = yf.download(ticker, period=period, progress=False)
            if data.empty or len(data) < 5:
                return None

            close_col = data["Close"]
            if hasattr(close_col, "ndim") and close_col.ndim == 2:
                close_col = close_col.iloc[:, 0]
            returns = close_col.pct_change().dropna()
            volatility = returns.std() * np.sqrt(252)  # Annualized
            return float(volatility)
        except Exception as e:
            print(f"  ⚠️  Could not calculate volatility for {ticker}: {e}")
            return None

    def recommend_position_size(
        self, ticker: str, account_size: float = 10000.0, risk_per_trade: float = 0.02
    ) -> Dict:
        """Recommend position size based on volatility"""
        volatility = self.get_ticker_volatility(ticker)

        if volatility is None:
            return {
                "ticker": ticker,
                "status": "Unable to calculate volatility",
                "recommended_notional": account_size * risk_per_trade,
            }

        # Higher volatility = smaller position
        volatility_factor = max(0.1, min(1.0, 0.5 / volatility)) if volatility > 0 else 1.0
        recommended_notional = account_size * risk_per_trade * volatility_factor

        return {
            "ticker": ticker,
            "volatility_annual": round(volatility * 100, 2),
            "status": "success",
            "recommended_notional": round(recommended_notional, 2),
            "basis": "volatility-adjusted position sizing",
        }

    def get_correlation_with_spy(self, ticker: str, period: str = "3mo") -> Optional[float]:
        """Calculate correlation with SPY for diversification analysis"""
        try:
            ticker_data = yf.download(ticker, period=period, progress=False)
            spy_data = yf.download("SPY", period=period, progress=False)

            if ticker_data.empty or spy_data.empty:
                return None

            ticker_returns = ticker_data["Close"].pct_change().dropna()
            spy_returns = spy_data["Close"].pct_change().dropna()

            # Align the series
            aligned = np.corrcoef(
                ticker_returns.values[: len(spy_returns)],
                spy_returns.values[: len(ticker_returns)],
            )

            return float(aligned[0, 1])
        except Exception as e:
            print(f"  ⚠️  Could not calculate correlation for {ticker}: {e}")
            return None


class EntryQualityAgent:
    """Evaluates quality of entry signals"""

    def __init__(self):
        self.analysis_agent = TradeAnalysisAgent()

    def assess_signal_quality(self, signal_source: str, lookback_days: int = 90) -> Dict:
        """Assess the quality of a specific signal source"""
        trades = self.analysis_agent.get_closed_trades(lookback_days)
        source_trades = [t for t in trades if t.signal_source == signal_source]

        if len(source_trades) < 5:
            return {
                "signal_source": signal_source,
                "status": "Insufficient data",
                "trade_count": len(source_trades),
            }

        win_rate = self.analysis_agent.calculate_win_rate(source_trades)
        avg_win, avg_loss = self.analysis_agent.calculate_avg_win_loss(source_trades)
        profit_factor = self.analysis_agent.calculate_profit_factor(source_trades)
        avg_pnl = np.mean([t.pnl_percent for t in source_trades])

        quality_score = (win_rate / 100) * (1 + profit_factor) * 100

        return {
            "signal_source": signal_source,
            "trade_count": len(source_trades),
            "win_rate_pct": round(win_rate, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_pnl_pct": round(avg_pnl, 2),
            "quality_score": round(quality_score, 2),
            "recommendation": "Strong" if quality_score > 100 else "Weak" if quality_score < 50 else "Neutral",
        }


# ──────────────────────────────────────────────────────────────────────────────
# Utility function for reporting
# ──────────────────────────────────────────────────────────────────────────────
def generate_optimization_report(lookback_days: int = 90) -> str:
    """Generate a comprehensive optimization report"""
    print("\n" + "=" * 70)
    print("📊 OPTIMIZATION REPORT")
    print("=" * 70)

    # Trade Analysis
    analysis_agent = TradeAnalysisAgent()
    trades = analysis_agent.get_closed_trades(lookback_days)
    summary = analysis_agent.get_summary(trades)

    print("\n📈 Trade Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    # TP/SL Optimization
    tpsl_agent = DynamicTPSLAgent()
    recommendations = tpsl_agent.recommend_tp_sl(lookback_days)

    print("\n⚙️  TP/SL Recommendations:")
    for metric, rec in recommendations.items():
        print(f"  {rec.metric.upper()}")
        print(f"    Current: {rec.current_value}%")
        print(f"    Recommended: {rec.recommended_value}%")
        print(f"    Confidence: {rec.confidence * 100:.1f}%")
        print(f"    Rationale: {rec.rationale}")

    # Signal Quality Analysis
    entry_agent = EntryQualityAgent()
    sources = set(t.signal_source for t in trades)

    print("\n📍 Signal Source Quality:")
    for source in sources:
        quality = entry_agent.assess_signal_quality(source, lookback_days)
        print(f"  {source}:")
        for key, value in quality.items():
            if key != "signal_source":
                print(f"    {key}: {value}")

    # By Ticker Analysis
    print("\n🎯 Performance by Ticker:")
    ticker_perf = {}
    for trade in trades:
        if trade.ticker not in ticker_perf:
            ticker_perf[trade.ticker] = []
        ticker_perf[trade.ticker].append(trade)

    for ticker, ticker_trades in ticker_perf.items():
        win_rate = analysis_agent.calculate_win_rate(ticker_trades)
        avg_pnl = np.mean([t.pnl_percent for t in ticker_trades])
        print(f"  {ticker}: {len(ticker_trades)} trades, {win_rate:.1f}% WR, {avg_pnl:.2f}% avg PnL")

    print("\n" + "=" * 70 + "\n")

    return "Report generated successfully"


def get_optimization_snapshot(lookback_days: int = 90) -> Dict[str, Any]:
    """Return optimization metrics in JSON-friendly format for dashboards/APIs."""
    analysis = TradeAnalysisAgent()
    tpsl = DynamicTPSLAgent()

    trades = analysis.get_closed_trades(lookback_days)
    summary = analysis.get_summary(trades)
    recs = tpsl.recommend_tp_sl(lookback_days)

    signal_breakdown = analysis.analyze_by_signal_source(trades) if trades else {}
    formatted_sources = []
    for source, stats in signal_breakdown.items():
        formatted_sources.append(
            {
                "signal_source": source,
                "count": int(stats.get("count", 0)),
                "win_rate": round(float(stats.get("win_rate", 0.0)), 2),
                "avg_pnl": round(float(stats.get("avg_pnl", 0.0)), 2),
            }
        )

    formatted_sources.sort(key=lambda x: (x["avg_pnl"], x["win_rate"]), reverse=True)

    return {
        "summary": summary,
        "recommendations": {
            "take_profit": {
                "current": recs["take_profit"].current_value,
                "recommended": recs["take_profit"].recommended_value,
                "confidence": recs["take_profit"].confidence,
                "rationale": recs["take_profit"].rationale,
            },
            "stop_loss": {
                "current": recs["stop_loss"].current_value,
                "recommended": recs["stop_loss"].recommended_value,
                "confidence": recs["stop_loss"].confidence,
                "rationale": recs["stop_loss"].rationale,
            },
        },
        "signal_sources": formatted_sources,
    }
