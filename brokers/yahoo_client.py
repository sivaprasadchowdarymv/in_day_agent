"""Yahoo Finance adapter (free, no API key) via the `yfinance` library.

Important limitations, surfaced to the user in the UI:
* Data is DELAYED (exchange-licensed real-time data is not free). Treat the
  latest candle as delayed, never as a live tick.
* Yahoo intraday history limits: 1m ≈ last 7 days, 5m/15m/30m ≈ last 60 days,
  1h ≈ 730 days. 3m candles are built by resampling 1m candles.
* No bid/ask depth – spreads are estimated and labelled as estimates.
* Yahoo is an unofficial source; outages or ticker renames are reported as
  errors, never papered over with synthetic data.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from brokers.base import (CANDLE_COLUMNS, CandleSet, DataLabel, DataProviderError, MarketDataProvider,
                          Quote, SUPPORTED_INTERVALS)
from config.logging_setup import get_logger
from data.market_hours import TZ, candle_is_complete, now_ist
from data.validation import clean_candles

log = get_logger("yahoo")

_YF_INTERVAL = {"1m": "1m", "3m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "1d": "1d"}
_MAX_DAYS = {"1m": 7, "3m": 7, "5m": 59, "15m": 59, "30m": 59, "1h": 729, "1d": 3650}


def to_yahoo_symbol(symbol: str, is_index: bool = False) -> str:
    s = symbol.strip().upper()
    if is_index or s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


class RateLimiter:
    def __init__(self, min_interval_sec: float):
        self.min_interval = min_interval_sec
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample within each session day so candles never span two sessions."""
    if df.empty:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    parts = []
    for _, day in df.groupby(df.index.date):
        r = day.resample(rule, origin="start_day", label="left", closed="left").agg(agg)
        parts.append(r.dropna(subset=["open"]))
    return pd.concat(parts) if parts else df.iloc[0:0]


class YahooFinanceProvider(MarketDataProvider):
    name = "Yahoo Finance (yfinance)"
    realtime_label = DataLabel.DELAYED

    def __init__(self, request_delay_sec: float = 0.3, max_retries: int = 3, cache_ttl_sec: int = 60):
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise DataProviderError("yfinance is not installed: pip install yfinance") from exc
        self.limiter = RateLimiter(request_delay_sec)
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl_sec
        self._mem: dict[tuple, tuple[float, CandleSet]] = {}

    # ------------------------------------------------------------------ raw
    def _download(self, ysym: str, yf_interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        import yfinance as yf

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                t = yf.Ticker(ysym)
                df = t.history(start=start, end=end, interval=yf_interval, auto_adjust=False,
                               actions=False, prepost=False)
                if df is not None and not df.empty:
                    return df
                last_exc = DataProviderError(f"No data returned for {ysym} ({yf_interval})")
            except Exception as exc:  # noqa: BLE001 - network / parsing errors
                last_exc = exc
            backoff = 2 ** attempt
            log.warning(f"Yahoo fetch failed for {ysym} {yf_interval} (attempt {attempt + 1}); retry in {backoff}s")
            time.sleep(backoff)
        raise DataProviderError(f"Yahoo Finance request failed for {ysym}: {last_exc}")

    # ------------------------------------------------------------------ API
    def get_candles(self, symbol: str, interval: str, days: int, *, is_index: bool = False,
                    include_provisional: bool = False) -> CandleSet:
        if interval not in SUPPORTED_INTERVALS:
            raise DataProviderError(f"Unsupported interval {interval}")
        key = (symbol, interval, days, is_index, include_provisional)
        hit = self._mem.get(key)
        ttl = self.cache_ttl if interval != "1d" else 3600
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]

        warnings: list[str] = []
        req_days = days
        if days > _MAX_DAYS[interval]:
            warnings.append(f"Yahoo limits {interval} history to ~{_MAX_DAYS[interval]} days; request clipped.")
            req_days = _MAX_DAYS[interval]
        ysym = to_yahoo_symbol(symbol, is_index)
        now = now_ist()
        start = (now - timedelta(days=req_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now + timedelta(days=1)
        raw = self._download(ysym, _YF_INTERVAL[interval], start, end)

        df = raw.rename(columns=str.lower)
        df = df[[c for c in CANDLE_COLUMNS if c in df.columns]]
        if df.index.tz is None:
            df.index = df.index.tz_localize(TZ)
        else:
            df.index = df.index.tz_convert(TZ)
        if interval == "1d":
            df.index = df.index.normalize()
        df = clean_candles(df)
        if interval == "3m":
            df = resample_ohlcv(df, "3min")

        provisional = False
        if not df.empty and not candle_is_complete(df.index[-1], interval, now):
            if include_provisional:
                provisional = True
                warnings.append("Last candle is still forming (provisional).")
            else:
                df = df.iloc[:-1]

        if df.empty:
            label = DataLabel.UNAVAILABLE
        elif df.index[-1].date() == now.date():
            label = DataLabel.DELAYED
        else:
            label = DataLabel.HISTORICAL
        if (is_index or symbol.startswith("^")) and "volume" in df and (df["volume"] == 0).all():
            warnings.append("Index volume not provided by source; volume-based index metrics disabled.")

        cs = CandleSet(symbol=symbol, interval=interval, df=df, label=label, source=self.name,
                       fetched_at=now, includes_provisional=provisional, warnings=warnings)
        self._mem[key] = (time.monotonic(), cs)
        return cs

    def get_quote(self, symbol: str, *, is_index: bool = False) -> Quote:
        warnings = ["Yahoo quote is delayed; bid/ask not available (spread estimated)."]
        intraday = self.get_candles(symbol, "1m", 2, is_index=is_index, include_provisional=True)
        daily = self.get_candles(symbol, "1d", 10, is_index=is_index, include_provisional=True)
        if intraday.empty and daily.empty:
            return Quote(symbol, None, None, DataLabel.UNAVAILABLE, self.name, warnings=["No quote data"])
        src = intraday if not intraday.empty else daily
        last = src.df.iloc[-1]
        ts = src.df.index[-1]
        if src.interval == "1m":
            ts = min(ts + pd.Timedelta(minutes=1), pd.Timestamp(now_ist()))  # price as of candle end
        today = now_ist().date()
        d = daily.df
        prev_close = None
        day_open = day_high = day_low = vol = None
        if not d.empty:
            past = d[d.index.date < today]
            prev_close = float(past["close"].iloc[-1]) if not past.empty else None
            cur = d[d.index.date == today]
            if not cur.empty:
                day_open, day_high, day_low, vol = (float(cur["open"].iloc[-1]), float(cur["high"].iloc[-1]),
                                                    float(cur["low"].iloc[-1]), float(cur["volume"].iloc[-1]))
        label = DataLabel.DELAYED if ts.date() == today else DataLabel.HISTORICAL
        return Quote(symbol=symbol, ltp=float(last["close"]), timestamp=ts.to_pydatetime(), label=label,
                     source=self.name, prev_close=prev_close, day_open=day_open, day_high=day_high,
                     day_low=day_low, volume=vol, warnings=warnings)
