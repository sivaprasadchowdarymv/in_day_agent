"""Compute the full causal indicator set for a candle frame."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.momentum import macd, roc, rsi, stoch_rsi
from indicators.price_action import candle_patterns, classic_pivots, last_swing_levels, previous_day_levels
from indicators.trend import adx, atr, ema, supertrend
from indicators.volatility import bollinger, historical_volatility, opening_range
from indicators.volume import relative_volume_bar, relative_volume_tod, session_vwap, volume_ma

INDICATOR_VERSION = "1.0"


def compute_all(df: pd.DataFrame, interval: str, opening_range_minutes: int = 15) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    c = out["close"]
    out["ema9"] = ema(c, 9)
    out["ema21"] = ema(c, 21)
    out["ema50"] = ema(c, 50)
    out["ema200"] = ema(c, 200) if len(out) >= 200 else np.nan
    out = out.join(supertrend(out, 10, 3.0))
    out = out.join(adx(out, 14))
    out["rsi14"] = rsi(c, 14)
    out = out.join(macd(c))
    out["roc10"] = roc(c, 10)
    out = out.join(stoch_rsi(c))
    out["atr14"] = atr(out, 14)
    out = out.join(bollinger(c))
    has_volume = bool(out["volume"].sum() > 0)
    out["vol_ma20"] = volume_ma(out, 20)
    out["rvol_bar"] = relative_volume_bar(out, 20) if has_volume else np.nan
    if interval == "1d":
        out["hv20"] = historical_volatility(c, 20, 252)
        out["vwap"] = np.nan
        out["rvol_tod"] = out["rvol_bar"]
    else:
        out["vwap"] = session_vwap(out) if has_volume else np.nan
        out["rvol_tod"] = relative_volume_tod(out) if has_volume else np.nan
        out = out.join(opening_range(out, opening_range_minutes))
        pdl = previous_day_levels(out)
        out = out.join(pdl)
        out = out.join(classic_pivots(pdl["pdh"], pdl["pdl"], pdl["pdc"]))
    out = out.join(last_swing_levels(out, 3))
    out = out.join(candle_patterns(out))
    return out


def snapshot(ind: pd.DataFrame, i: int = -1) -> dict:
    """JSON-friendly dict of the indicator row at position i."""
    row = ind.iloc[i]
    res = {"ts": str(ind.index[i])}
    for k, v in row.items():
        if isinstance(v, (bool, np.bool_)):
            res[k] = bool(v)
        elif isinstance(v, (int, float, np.floating, np.integer)):
            res[k] = None if pd.isna(v) else round(float(v), 4)
    return res
