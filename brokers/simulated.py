"""Deterministic SIMULATED market data for demo mode and automated tests.

Every CandleSet/Quote produced here carries DataLabel.SIMULATED and the UI
shows a red banner. The app never switches to this provider automatically –
the user must select it explicitly in Settings.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from brokers.base import CandleSet, DataLabel, DataProviderError, MarketDataProvider, Quote, SUPPORTED_INTERVALS
from brokers.yahoo_client import resample_ohlcv
from data.market_hours import TZ, candle_is_complete, is_trading_day, now_ist
from config.markets import SESSION_OPEN

_RULE = {"1m": None, "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min"}

_INDEX_BASE = {"^NSEI": 24500.0, "^NSEBANK": 52000.0, "^INDIAVIX": 13.0}


def _seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


class SimulatedProvider(MarketDataProvider):
    name = "Simulated (demo – NOT market data)"
    realtime_label = DataLabel.SIMULATED
    HORIZON_DAYS = 200  # one consistent price path per symbol, sliced per request

    def __init__(self, anchor: datetime | None = None, unknown_symbols: set[str] | None = None):
        """`anchor`: pretend 'now' (IST). `unknown_symbols`: symbols that should
        behave as invalid (for verification tests)."""
        self.anchor = anchor
        self.unknown = {s.upper() for s in (unknown_symbols or set())}
        self._cache: dict[str, pd.DataFrame] = {}

    def _now(self) -> datetime:
        return self.anchor or now_ist()

    def _minute_bars(self, symbol: str, days: int) -> pd.DataFrame:
        key = f"{symbol}|{days}|{self._now():%Y%m%d%H%M}"
        if key in self._cache:
            return self._cache[key]
        rng = np.random.default_rng(_seed(symbol))
        now = self._now()
        sessions = []
        d = now
        while len(sessions) < days:
            if is_trading_day(d):
                sessions.append(d.date())
            d -= timedelta(days=1)
        sessions = sorted(sessions)
        if symbol == "^INDIAVIX":
            base = 13.0
        else:
            base = _INDEX_BASE.get(symbol, 80 + (_seed(symbol) % 2900))
        vol_daily = 0.009 if symbol.startswith("^") else 0.012 + (_seed(symbol) % 10) / 1000
        sym_drift = ((_seed(symbol) % 7) - 3) / 4000.0
        frames = []
        price = base
        for sd in sessions:
            day_drift = sym_drift + rng.normal(0, 0.004)
            gap = rng.normal(0, 0.004)
            price *= (1 + gap)
            n = 375
            sig = vol_daily / np.sqrt(n)
            rets = rng.normal(day_drift / n, sig, n)
            closes = price * np.exp(np.cumsum(rets))
            opens = np.concatenate([[price], closes[:-1]])
            wick = np.abs(rng.normal(0, sig * 0.6, n)) * closes
            highs = np.maximum(opens, closes) + wick
            lows = np.minimum(opens, closes) - np.abs(rng.normal(0, sig * 0.6, n)) * closes
            u = np.linspace(-1, 1, n)
            base_vol = 0 if symbol.startswith("^") else 2_000 + (_seed(symbol) % 20_000)
            vols = base_vol * (1.2 + 1.8 * u ** 2) * rng.lognormal(0, 0.5, n)
            start = datetime.combine(sd, SESSION_OPEN, TZ)
            idx = pd.date_range(start, periods=n, freq="1min")
            frames.append(pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                                        "volume": np.round(vols)}, index=idx))
            price = closes[-1]
        df = pd.concat(frames)
        df = df[df.index <= now]
        self._cache[key] = df
        return df

    def get_candles(self, symbol: str, interval: str, days: int, *, is_index: bool = False,
                    include_provisional: bool = False) -> CandleSet:
        if interval not in SUPPORTED_INTERVALS:
            raise DataProviderError(f"Unsupported interval {interval}")
        sym = symbol.upper()
        if sym in self.unknown:
            raise DataProviderError(f"Unknown symbol {symbol}")
        full = self._minute_bars(sym, self.HORIZON_DAYS)
        sessions = sorted(set(full.index.date))[-max(days, 2):]
        m = full[full.index.date >= sessions[0]]
        if interval == "1d":
            agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            df = m.groupby(m.index.normalize()).agg(agg)
        elif interval == "1m":
            df = m.copy()
        else:
            df = resample_ohlcv(m, _RULE[interval])
        now = self._now()
        provisional = False
        if not df.empty and not candle_is_complete(df.index[-1], interval, now):
            if include_provisional:
                provisional = True
            else:
                df = df.iloc[:-1]
        return CandleSet(symbol=symbol, interval=interval, df=df, label=DataLabel.SIMULATED, source=self.name,
                         fetched_at=now, includes_provisional=provisional,
                         warnings=["SIMULATED data for demo/testing – not real market prices."])

    def get_quote(self, symbol: str, *, is_index: bool = False) -> Quote:
        cs = self.get_candles(symbol, "1m", 2, include_provisional=True)
        d = self.get_candles(symbol, "1d", 5, include_provisional=True).df
        last = cs.df.iloc[-1]
        today = self._now().date()
        past = d[d.index.date < today]
        cur = d[d.index.date == today]
        return Quote(symbol=symbol, ltp=float(last["close"]),
                     timestamp=min((cs.df.index[-1] + pd.Timedelta(minutes=1)).to_pydatetime(), self._now()),
                     label=DataLabel.SIMULATED, source=self.name,
                     prev_close=float(past["close"].iloc[-1]) if not past.empty else None,
                     day_open=float(cur["open"].iloc[0]) if not cur.empty else None,
                     day_high=float(cur["high"].iloc[0]) if not cur.empty else None,
                     day_low=float(cur["low"].iloc[0]) if not cur.empty else None,
                     volume=float(cur["volume"].iloc[0]) if not cur.empty else None,
                     warnings=["SIMULATED quote – not a market price."])
