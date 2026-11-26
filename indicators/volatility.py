"""Volatility indicators (causal)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.trend import atr  # re-export
from indicators.volume import session_key

__all__ = ["atr", "bollinger", "historical_volatility", "opening_range"]


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd, "bb_lower": mid - k * sd,
                         "bb_width": (2 * k * sd) / mid * 100})


def historical_volatility(close: pd.Series, n: int = 20, periods_per_year: float = 252) -> pd.Series:
    lr = np.log(close / close.shift(1))
    return lr.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(periods_per_year) * 100


def opening_range(df: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
    """Opening range high/low of each session. Values are NaN until the range
    window has fully completed, so the series is free of look-ahead."""
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    mins = pd.Series((idx.hour * 60 + idx.minute) - (9 * 60 + 15), index=df.index)
    key = session_key(df.index)
    bar_minutes = int(pd.Series(df.index).diff().dropna().dt.total_seconds().min() / 60) if len(df) > 1 else 5
    in_range = mins + bar_minutes <= minutes
    hi = df["high"].where(in_range).groupby(key).cummax()
    lo = df["low"].where(in_range).groupby(key).cummin()
    hi = hi.groupby(key).ffill()
    lo = lo.groupby(key).ffill()
    complete = mins + bar_minutes > minutes  # range known only after its last bar closed
    # on the bar that completes the range we also know it (bar close == range end)
    complete = complete | (mins + bar_minutes == minutes)
    return pd.DataFrame({"or_high": hi.where(complete), "or_low": lo.where(complete)})
