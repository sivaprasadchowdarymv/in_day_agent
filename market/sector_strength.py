"""Sector relative strength vs NIFTY 50 (today and 5-day)."""
from __future__ import annotations

import pandas as pd

from brokers.base import DataProviderError, MarketDataProvider
from config.markets import BENCHMARKS, SECTOR_INDICES


def _changes(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    if daily is None or len(daily) < 6:
        return None, None
    c = daily["close"]
    return float((c.iloc[-1] / c.iloc[-2] - 1) * 100), float((c.iloc[-1] / c.iloc[-6] - 1) * 100)


def compute_sector_strength(provider: MarketDataProvider,
                            live_changes: dict[str, float] | None = None,
                            live_nifty_change: float | None = None) -> tuple[pd.DataFrame, list[str]]:
    """5-day RS from daily candles; today's change from NSE live index data when
    supplied (`live_changes`), otherwise from the candle provider."""
    warnings: list[str] = []
    try:
        n = provider.get_candles(BENCHMARKS["NIFTY 50"], "1d", 15, is_index=True, include_provisional=True).df
    except DataProviderError as exc:
        return pd.DataFrame(), [f"NIFTY 50 unavailable: {exc}"]
    n1, n5 = _changes(n)
    rows = []
    for key, (name, ticker) in SECTOR_INDICES.items():
        try:
            d = provider.get_candles(ticker, "1d", 15, is_index=True, include_provisional=True).df
        except DataProviderError as exc:
            if live_changes and key in live_changes:
                d = None
            else:
                warnings.append(f"{name}: {exc}")
                continue
        c1, c5 = _changes(d)
        src = provider.name
        if live_changes and key in live_changes:
            c1, src = live_changes[key], "NSE live"
        if c1 is None:
            warnings.append(f"{name}: insufficient data")
            continue
        nifty_today = live_nifty_change if (live_changes and live_nifty_change is not None) else n1
        rows.append({"sector": key, "index": name, "change_1d_pct": c1, "change_5d_pct": c5,
                     "rs_1d": c1 - (nifty_today or 0), "rs_5d": (c5 or 0) - (n5 or 0), "source_1d": src})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["rs_score"] = 0.6 * df["rs_1d"] + 0.4 * df["rs_5d"] / 2
        df = df.sort_values("rs_score", ascending=False).reset_index(drop=True)
        df["rank"] = range(1, len(df) + 1)
    return df, warnings


def sector_alignment(sector_df: pd.DataFrame, sector: str, direction: str) -> tuple[float, str]:
    """0..1 score: strong sector for longs / weak sector for shorts."""
    if sector_df is None or sector_df.empty or sector not in set(sector_df["sector"]):
        return 0.5, "Sector strength unavailable (neutral)"
    row = sector_df[sector_df["sector"] == sector].iloc[0]
    n = len(sector_df)
    pct = 1 - (row["rank"] - 1) / max(n - 1, 1)  # 1 = strongest
    val = pct if direction == "LONG" else 1 - pct
    return float(val), f"{row['index']} ranked {int(row['rank'])}/{n} (RS today {row['rs_1d']:+.2f}%)"
