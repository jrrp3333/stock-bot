"""
Pattern detection from OHLC bars using swing/pivot structure.

Design goals:
- Deterministic, inspectable rules (not black-box CV)
- Minimal parameter surface to reduce overfitting risk
- Signals are advisory filters layered on top of numeric strategy logic

Detected structures (heuristic):
- Head & Shoulders / Inverse H&S
- Flag-like continuation (bull/bear)
- Wedge breakout (falling/rising)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _pivots(series: pd.Series, left: int = 3, right: int = 3, kind: str = "high") -> pd.Series:
    values = series.astype(float).to_numpy()
    n = len(values)
    out = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window = values[i - left : i + right + 1]
        center = values[i]
        if kind == "high":
            if center >= np.max(window):
                out[i] = True
        else:
            if center <= np.min(window):
                out[i] = True
    return pd.Series(out, index=series.index)


def _line_value(y1: float, y2: float, x1: int, x2: int, x: int) -> float:
    if x2 == x1:
        return y2
    m = (y2 - y1) / (x2 - x1)
    return y1 + m * (x - x1)


def detect_ohlc_patterns(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Return pattern bull/bear confidence columns aligned to ohlc index."""
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(ohlc.columns)):
        raise ValueError("OHLC data must contain Open, High, Low, Close")

    high = ohlc["High"].astype(float)
    low = ohlc["Low"].astype(float)
    close = ohlc["Close"].astype(float)

    ph = _pivots(high, left=3, right=3, kind="high")
    pl = _pivots(low, left=3, right=3, kind="low")

    n = len(ohlc)
    bull = np.zeros(n, dtype=float)
    bear = np.zeros(n, dtype=float)

    ph_idx = np.where(ph.to_numpy())[0]
    pl_idx = np.where(pl.to_numpy())[0]

    for i in range(40, n):
        # --- Head & Shoulders (bear) / Inverse H&S (bull)
        recent_highs = ph_idx[ph_idx < i]
        recent_lows = pl_idx[pl_idx < i]

        if len(recent_highs) >= 3 and len(recent_lows) >= 2:
            h1, h2, h3 = recent_highs[-3], recent_highs[-2], recent_highs[-1]
            if h1 < h2 < h3 and (h3 - h1) <= 35:
                v1, v2, v3 = high.iloc[h1], high.iloc[h2], high.iloc[h3]
                shoulders_similar = abs(v1 - v3) / max(v2, 1e-9) < 0.03
                head_higher = v2 > max(v1, v3) * 1.02
                lows_between = recent_lows[(recent_lows > h1) & (recent_lows < h3)]
                if shoulders_similar and head_higher and len(lows_between) >= 2:
                    l1, l2 = lows_between[0], lows_between[-1]
                    neck = _line_value(low.iloc[l1], low.iloc[l2], l1, l2, i)
                    if close.iloc[i] < neck:
                        bear[i] = max(bear[i], 0.90)

        if len(recent_lows) >= 3 and len(recent_highs) >= 2:
            l1, l2, l3 = recent_lows[-3], recent_lows[-2], recent_lows[-1]
            if l1 < l2 < l3 and (l3 - l1) <= 35:
                v1, v2, v3 = low.iloc[l1], low.iloc[l2], low.iloc[l3]
                shoulders_similar = abs(v1 - v3) / max(abs(v2), 1e-9) < 0.03
                head_lower = v2 < min(v1, v3) * 0.98
                highs_between = recent_highs[(recent_highs > l1) & (recent_highs < l3)]
                if shoulders_similar and head_lower and len(highs_between) >= 2:
                    h1i, h2i = highs_between[0], highs_between[-1]
                    neck = _line_value(high.iloc[h1i], high.iloc[h2i], h1i, h2i, i)
                    if close.iloc[i] > neck:
                        bull[i] = max(bull[i], 0.90)

        # --- Flag continuation (heuristic)
        window = ohlc.iloc[max(0, i - 25) : i + 1]
        if len(window) >= 15:
            first = float(window["Close"].iloc[0])
            mid_peak = float(window["High"].iloc[:10].max())
            end_close = float(window["Close"].iloc[-1])
            pole_up = (mid_peak / max(first, 1e-9)) - 1.0
            pullback = (mid_peak - float(window["Low"].iloc[10:].min())) / max(mid_peak, 1e-9)
            breakout_up = end_close > float(window["High"].iloc[-5:].max())
            if pole_up > 0.05 and 0.01 < pullback < 0.08 and breakout_up:
                bull[i] = max(bull[i], 0.70)

            first_low = float(window["Low"].iloc[:10].min())
            mid_trough = float(window["Low"].iloc[:10].min())
            pole_dn = 1.0 - (mid_trough / max(float(window["Close"].iloc[0]), 1e-9))
            rebound = (float(window["High"].iloc[10:].max()) - mid_trough) / max(mid_trough, 1e-9)
            breakout_dn = end_close < float(window["Low"].iloc[-5:].min())
            if pole_dn > 0.05 and 0.01 < rebound < 0.08 and breakout_dn:
                bear[i] = max(bear[i], 0.70)

        # --- Wedge breakout (trendline convergence via recent pivots)
        highs_lookback = ph_idx[(ph_idx < i) & (ph_idx >= i - 35)]
        lows_lookback = pl_idx[(pl_idx < i) & (pl_idx >= i - 35)]
        if len(highs_lookback) >= 3 and len(lows_lookback) >= 3:
            hh_x = highs_lookback[-3:]
            ll_x = lows_lookback[-3:]
            hh_y = high.iloc[hh_x].to_numpy()
            ll_y = low.iloc[ll_x].to_numpy()

            h_slope = np.polyfit(hh_x, hh_y, 1)[0]
            l_slope = np.polyfit(ll_x, ll_y, 1)[0]
            spread_now = hh_y[-1] - ll_y[-1]
            spread_prev = hh_y[0] - ll_y[0]
            converging = spread_now < spread_prev

            if converging and h_slope < 0 and l_slope < 0 and h_slope < l_slope:
                upper_line_now = _line_value(hh_y[-2], hh_y[-1], hh_x[-2], hh_x[-1], i)
                if close.iloc[i] > upper_line_now:
                    bull[i] = max(bull[i], 0.65)

            if converging and h_slope > 0 and l_slope > 0 and h_slope < l_slope:
                lower_line_now = _line_value(ll_y[-2], ll_y[-1], ll_x[-2], ll_x[-1], i)
                if close.iloc[i] < lower_line_now:
                    bear[i] = max(bear[i], 0.65)

    out = pd.DataFrame(index=ohlc.index)
    out["pattern_bull_score"] = bull.astype(float)
    out["pattern_bear_score"] = bear.astype(float)
    out["pattern_bull"] = (out["pattern_bull_score"] >= 0.6).astype(float)
    out["pattern_bear"] = (out["pattern_bear_score"] >= 0.6).astype(float)
    out["pattern_net_score"] = out["pattern_bull_score"] - out["pattern_bear_score"]
    return out
