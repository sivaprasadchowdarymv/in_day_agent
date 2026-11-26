"""NSE session helpers (IST)."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from config.markets import IST, NSE_HOLIDAYS, SESSION_CLOSE, SESSION_OPEN

TZ = ZoneInfo(IST)

INTERVAL_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 375}


def now_ist() -> datetime:
    return datetime.now(TZ)


def is_trading_day(d: datetime) -> bool:
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in NSE_HOLIDAYS


def market_status(at: datetime | None = None) -> str:
    at = at or now_ist()
    if not is_trading_day(at):
        return "CLOSED (holiday/weekend)"
    t = at.time()
    if t < time(9, 0):
        return "PRE-OPEN WAIT"
    if t < SESSION_OPEN:
        return "PRE-OPEN"
    if t <= SESSION_CLOSE:
        return "OPEN"
    return "CLOSED"


def is_market_open(at: datetime | None = None) -> bool:
    return market_status(at) == "OPEN"


def candle_is_complete(candle_start: pd.Timestamp, interval: str, at: datetime | None = None) -> bool:
    """A candle is complete when its end time has passed (or the session ended)."""
    at = at or now_ist()
    minutes = INTERVAL_MINUTES.get(interval, 5)
    start = candle_start.tz_convert(TZ) if candle_start.tzinfo else candle_start.tz_localize(TZ)
    if interval == "1d":
        close_dt = datetime.combine(start.date(), SESSION_CLOSE, TZ)
        return at >= close_dt
    end = start + timedelta(minutes=minutes)
    session_end = datetime.combine(start.date(), SESSION_CLOSE, TZ)
    return at >= min(end.to_pydatetime(), session_end)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def minutes_since_open(ts: pd.Timestamp) -> int:
    t = ts.tz_convert(TZ) if ts.tzinfo else ts
    return (t.hour * 60 + t.minute) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)


def as_ist(dt) -> pd.Timestamp | None:
    """Normalise datetimes read back from the DB (SQLite drops tzinfo; values are
    stored as IST wall-clock time) into tz-aware IST timestamps."""
    if dt is None:
        return None
    ts = pd.Timestamp(dt)
    return ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)
