"""SQLite candle cache with incremental updates and duplicate prevention.

Only *completed* candles are persisted. If the upstream request fails, cached
candles are returned labelled HISTORICAL with an explicit warning; downstream
freshness checks then reject them for live recommendations.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta

import pandas as pd

from brokers.base import CandleSet, DataLabel, DataProviderError, MarketDataProvider, Quote
from config.logging_setup import get_logger
from config.settings import STORAGE_DIR
from data.market_hours import TZ, now_ist

log = get_logger("cache")


class CandleCache:
    def __init__(self, path=None):
        self.path = str(path or STORAGE_DIR / "candle_cache.sqlite")
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY (symbol, interval, ts))"""
            )

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30)

    def load(self, symbol: str, interval: str, since: pd.Timestamp | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume FROM candles WHERE symbol=? AND interval=?"
        args: list = [symbol, interval]
        if since is not None:
            q += " AND ts>=?"
            args.append(int(since.timestamp()))
        q += " ORDER BY ts"
        with self._lock, self._conn() as c:
            rows = c.execute(q, args).fetchall()
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), unit="s", utc=True)).tz_convert(TZ)
        df.index.name = None
        return df

    def upsert(self, symbol: str, interval: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows = [(symbol, interval, int(ts.timestamp()), float(r.open), float(r.high), float(r.low),
                 float(r.close), float(r.volume)) for ts, r in df.iterrows()]
        with self._lock, self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def purge_older_than(self, days: int) -> int:
        cutoff = int((now_ist() - timedelta(days=days)).timestamp())
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM candles WHERE ts < ? AND interval != '1d'", (cutoff,))
            return cur.rowcount


class CachedProvider(MarketDataProvider):
    """Wraps another provider; fetches only the missing recent window."""

    def __init__(self, inner: MarketDataProvider, cache: CandleCache | None = None):
        self.inner = inner
        self.cache = cache or CandleCache()
        self.name = inner.name
        self.realtime_label = inner.realtime_label

    def get_candles(self, symbol, interval, days, *, is_index=False, include_provisional=False) -> CandleSet:
        now = now_ist()
        since = pd.Timestamp(now - timedelta(days=days))
        cached = self.cache.load(symbol, interval, since)
        fetch_days = days
        if not cached.empty:
            gap_days = (now.date() - cached.index[-1].date()).days + 2
            fetch_days = max(2, min(days, gap_days))
        try:
            fresh = self.inner.get_candles(symbol, interval, fetch_days, is_index=is_index,
                                           include_provisional=include_provisional)
        except DataProviderError as exc:
            if cached.empty:
                raise
            log.warning(f"Upstream failed for {symbol} {interval}; serving cached historical candles")
            return CandleSet(symbol, interval, cached, DataLabel.HISTORICAL, self.name + " (cache)", now,
                             warnings=[f"Live fetch failed ({exc}); showing CACHED historical data only."])
        to_store = fresh.df.iloc[:-1] if fresh.includes_provisional else fresh.df
        self.cache.upsert(symbol, interval, to_store)
        if cached.empty:
            return fresh
        merged = pd.concat([cached, fresh.df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged = merged[merged.index >= since.normalize()]
        return CandleSet(symbol, interval, merged, fresh.label, fresh.source, fresh.fetched_at,
                         fresh.includes_provisional, fresh.warnings)

    def get_quote(self, symbol, *, is_index=False) -> Quote:
        return self.inner.get_quote(symbol, is_index=is_index)
