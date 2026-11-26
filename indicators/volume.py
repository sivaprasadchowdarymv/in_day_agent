"""Volume indicators (causal). VWAP resets at each exchange session (IST date)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def session_key(index: pd.DatetimeIndex) -> pd.Series:
    idx = index.tz_convert("Asia/Kolkata") if index.tz is not None else index
    return pd.Series(idx.date, index=index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP using typical price, reset at every session boundary.
    Returns NaN where cumulative volume is zero (e.g. indices without volume)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    key = session_key(df.index)
    pv = (tp * df["volume"]).groupby(key).cumsum()
    vv = df["volume"].groupby(key).cumsum()
    return pv / vv.replace(0, np.nan)


def volume_ma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    return df["volume"].rolling(n, min_periods=n).mean()


def relative_volume_bar(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Volume vs the average of the previous n bars (excludes current bar)."""
    prev = df["volume"].shift(1).rolling(n, min_periods=n).mean()
    return df["volume"] / prev.replace(0, np.nan)


def relative_volume_tod(df: pd.DataFrame, lookback_days: int = 10) -> pd.Series:
    """Cumulative session volume up to this time-of-day vs the average
    cumulative volume at the same time over previous sessions. Corrects for the
    U-shaped intraday volume curve. Uses only prior sessions (causal)."""
    idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    key = pd.Series(idx.date, index=df.index)
    slot = pd.Series(idx.strftime("%H:%M"), index=df.index)
    cumvol = df["volume"].groupby(key).cumsum()
    tbl = pd.DataFrame({"day": key, "slot": slot, "cum": cumvol})
    piv = tbl.pivot_table(index="day", columns="slot", values="cum", aggfunc="last").sort_index()
    hist = piv.shift(1).rolling(lookback_days, min_periods=3).mean()
    stacked = hist.stack()
    stacked.index = stacked.index.set_names(["day", "slot"])
    ref = pd.Series([stacked.get((d, s), np.nan) for d, s in zip(key, slot)], index=df.index)
    return cumvol / ref.replace(0, np.nan)


def volume_breakout(df: pd.DataFrame, mult: float = 2.0, n: int = 20) -> pd.Series:
    return relative_volume_bar(df, n) >= mult
