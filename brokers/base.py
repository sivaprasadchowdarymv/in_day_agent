"""Data-provider interface. Strategy code depends only on this module, so any
broker/vendor (Groww, Zerodha Kite, Upstox, Angel, NSE files...) can be added
later by implementing `MarketDataProvider`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd


class DataLabel(str, Enum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    HISTORICAL = "HISTORICAL"
    SIMULATED = "SIMULATED"
    UNAVAILABLE = "UNAVAILABLE"


SUPPORTED_INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "1d")

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class CandleSet:
    """OHLCV candles plus provenance metadata.

    `df` index: tz-aware IST timestamps marking the *start* of each candle.
    Only completed candles are kept unless `includes_provisional` is True.
    """

    symbol: str
    interval: str
    df: pd.DataFrame
    label: DataLabel
    source: str
    fetched_at: datetime
    includes_provisional: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.df is None or self.df.empty

    @property
    def last_ts(self) -> pd.Timestamp | None:
        return None if self.empty else self.df.index[-1]


@dataclass
class Quote:
    symbol: str
    ltp: float | None
    timestamp: datetime | None
    label: DataLabel
    source: str
    prev_close: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    warnings: list[str] = field(default_factory=list)
    upper_band: float | None = None  # exchange circuit limits (when the source provides them)
    lower_band: float | None = None
    delivery_pct: float | None = None  # previous/current day delivery % (informational only)
    sources: list[str] = field(default_factory=list)
    cross_check: str | None = None

    @property
    def near_circuit(self) -> str | None:
        """'UPPER' / 'LOWER' when price is within 0.5% of a circuit limit."""
        if self.ltp is None:
            return None
        if self.upper_band and self.ltp >= self.upper_band * 0.995:
            return "UPPER"
        if self.lower_band and self.ltp <= self.lower_band * 1.005:
            return "LOWER"
        return None

    @property
    def spread_pct(self) -> float | None:
        if self.bid and self.ask and self.ask > 0 and self.bid > 0:
            mid = (self.bid + self.ask) / 2
            return (self.ask - self.bid) / mid * 100
        return None


class DataProviderError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    name: str = "base"
    #: label applied to the freshest intraday data this provider can give
    realtime_label: DataLabel = DataLabel.DELAYED

    @abstractmethod
    def get_candles(self, symbol: str, interval: str, days: int, *, is_index: bool = False,
                    include_provisional: bool = False) -> CandleSet:
        """Return candles for NSE symbol (e.g. 'RELIANCE') or index ticker."""

    @abstractmethod
    def get_quote(self, symbol: str, *, is_index: bool = False) -> Quote:
        ...

    def validate_symbol(self, symbol: str) -> bool:
        try:
            cs = self.get_candles(symbol, "1d", 10)
            return not cs.empty
        except DataProviderError:
            return False

    def test_connection(self) -> dict:
        """Used by the connection-test page."""
        started = datetime.now()
        try:
            cs = self.get_candles("^NSEI", "1d", 5, is_index=True)
            ok = not cs.empty
            return {
                "ok": ok,
                "provider": self.name,
                "label": cs.label.value,
                "rows": 0 if cs.empty else len(cs.df),
                "last_candle": str(cs.last_ts) if ok else None,
                "latency_ms": int((datetime.now() - started).total_seconds() * 1000),
                "warnings": cs.warnings,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": type(exc).__name__ + ": " + str(exc)[:300]}
