"""Momentum indicators (causal)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.trend import wilder


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(loss != 0, 100.0)
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ef = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    es = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = ef - es
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def roc(close: pd.Series, n: int = 10) -> pd.Series:
    return (close / close.shift(n) - 1) * 100


def stoch_rsi(close: pd.Series, n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    r = rsi(close, n)
    lo = r.rolling(n, min_periods=n).min()
    hi = r.rolling(n, min_periods=n).max()
    raw = (r - lo) / (hi - lo).replace(0, np.nan) * 100
    kk = raw.rolling(k, min_periods=k).mean()
    return pd.DataFrame({"stochrsi_k": kk, "stochrsi_d": kk.rolling(d, min_periods=d).mean()})
