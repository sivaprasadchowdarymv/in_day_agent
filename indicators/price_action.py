"""Price-action features (causal): previous-day levels, day extremes, pivots,
confirmed swings, support/resistance, candlestick patterns, gaps."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.volume import session_key


def previous_day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """PDH/PDL/PDC for intraday frames, derived from the intraday data itself."""
    key = session_key(df.index)
    daily = df.groupby(key).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                open=("open", "first"))
    prev = daily.shift(1)
    out = pd.DataFrame(index=df.index)
    out["pdh"] = key.map(prev["high"])
    out["pdl"] = key.map(prev["low"])
    out["pdc"] = key.map(prev["close"])
    out["day_open"] = key.map(daily["open"])  # session open is known from the first bar onward
    out["day_high"] = df["high"].groupby(key).cummax()
    out["day_low"] = df["low"].groupby(key).cummin()
    out["gap_pct"] = (out["day_open"] / out["pdc"] - 1) * 100
    return out


def classic_pivots(pdh: pd.Series, pdl: pd.Series, pdc: pd.Series) -> pd.DataFrame:
    p = (pdh + pdl + pdc) / 3
    return pd.DataFrame({"pivot": p, "r1": 2 * p - pdl, "s1": 2 * p - pdh,
                         "r2": p + (pdh - pdl), "s2": p - (pdh - pdl)})


def confirmed_swings(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Swing high at bar i if high[i] is the max of the window [i-k, i+k].
    It is only *known* k bars later, so the level is placed at i+k (no repaint)."""
    w = 2 * k + 1
    hi_c = df["high"].rolling(w, center=True, min_periods=w).max()
    lo_c = df["low"].rolling(w, center=True, min_periods=w).min()
    sh = df["high"].where(df["high"] == hi_c)
    sl = df["low"].where(df["low"] == lo_c)
    return pd.DataFrame({"swing_high": sh.shift(k), "swing_low": sl.shift(k)}, index=df.index)


def last_swing_levels(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    s = confirmed_swings(df, k)
    return pd.DataFrame({"last_swing_high": s["swing_high"].ffill(), "last_swing_low": s["swing_low"].ffill()})


def support_resistance(df: pd.DataFrame, upto: int | None = None, k: int = 3, tolerance_pct: float = 0.3,
                       max_levels: int = 6) -> list[dict]:
    """Cluster confirmed swing points up to bar index `upto` into levels.
    Returns [{'price', 'touches', 'kind'}] sorted by price."""
    sub = df.iloc[: (upto + 1) if upto is not None else len(df)]
    s = confirmed_swings(sub, k)
    pts = [(float(v), "R") for v in s["swing_high"].dropna()] + [(float(v), "S") for v in s["swing_low"].dropna()]
    if not pts:
        return []
    pts.sort()
    clusters: list[list[tuple[float, str]]] = [[pts[0]]]
    for p in pts[1:]:
        ref = np.mean([x[0] for x in clusters[-1]])
        if abs(p[0] - ref) / ref * 100 <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = []
    for c in clusters:
        kinds = [x[1] for x in c]
        levels.append({"price": float(np.mean([x[0] for x in c])), "touches": len(c),
                       "kind": max(set(kinds), key=kinds.count)})
    levels.sort(key=lambda d: -d["touches"])
    return sorted(levels[:max_levels], key=lambda d: d["price"])


def candle_patterns(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l
    po, pc = o.shift(1), c.shift(1)
    out = pd.DataFrame(index=df.index)
    out["bull_engulf"] = (c > o) & (pc < po) & (c >= po) & (o <= pc) & (body > (pc - po).abs())
    out["bear_engulf"] = (c < o) & (pc > po) & (c <= po) & (o >= pc) & (body > (pc - po).abs())
    out["hammer"] = (lower >= 2 * body) & (upper <= body * 0.6) & (body / rng >= 0.1)
    out["shooting_star"] = (upper >= 2 * body) & (lower <= body * 0.6) & (body / rng >= 0.1)
    out["doji"] = body / rng <= 0.1
    out["inside_bar"] = (h <= h.shift(1)) & (l >= l.shift(1))
    out["strong_bull"] = (c > o) & (body / rng >= 0.6) & ((h - c) / rng <= 0.2)
    out["strong_bear"] = (c < o) & (body / rng >= 0.6) & ((c - l) / rng <= 0.2)
    return out.fillna(False).astype(bool)


PATTERN_LABELS = {
    "bull_engulf": "Bullish engulfing", "bear_engulf": "Bearish engulfing", "hammer": "Hammer",
    "shooting_star": "Shooting star", "doji": "Doji", "inside_bar": "Inside bar",
    "strong_bull": "Strong bullish candle", "strong_bear": "Strong bearish candle",
}
