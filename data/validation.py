"""Candle data validation, completeness and freshness checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from brokers.base import CANDLE_COLUMNS, CandleSet, DataLabel
from data.market_hours import INTERVAL_MINUTES, TZ, is_trading_day, now_ist
from config.markets import SESSION_CLOSE, SESSION_OPEN


@dataclass
class ValidationReport:
    ok: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completeness: float = 1.0
    rows: int = 0


def clean_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise, sort, de-duplicate and drop impossible rows. Never invents rows."""
    if df is None or df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    missing = [c for c in CANDLE_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"Missing candle columns: {missing}")
    out = out[CANDLE_COLUMNS].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])
    out["volume"] = out["volume"].fillna(0.0)
    bad = (out["high"] < out[["open", "close"]].max(axis=1) - 1e-9) | (
        out["low"] > out[["open", "close"]].min(axis=1) + 1e-9) | (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
    return out[~bad]


def validate_candles(df: pd.DataFrame, interval: str) -> ValidationReport:
    rep = ValidationReport(ok=True, rows=0 if df is None else len(df))
    if df is None or df.empty:
        return ValidationReport(ok=False, issues=["No candles returned"], completeness=0.0)
    if not df.index.is_monotonic_increasing:
        rep.issues.append("Timestamps not sorted")
    if df.index.has_duplicates:
        rep.issues.append("Duplicate timestamps")
    if df[["open", "high", "low", "close"]].isna().any().any():
        rep.issues.append("NaN prices present")
    if ((df["high"] < df["low"])).any():
        rep.issues.append("High < Low in some candles")
    if (df["volume"] < 0).any():
        rep.issues.append("Negative volume")
    if df.index.tz is None:
        rep.warnings.append("Timestamps are timezone-naive")
    if interval != "1d":
        rep.completeness = intraday_completeness(df, interval)
        if rep.completeness < 0.8:
            rep.warnings.append(f"Intraday data only {rep.completeness:.0%} complete (missing candles)")
        zero_vol = (df["volume"] == 0).mean()
        if zero_vol > 0.3:
            rep.warnings.append(f"{zero_vol:.0%} of candles have zero volume")
    rep.ok = not rep.issues
    return rep


def intraday_completeness(df: pd.DataFrame, interval: str) -> float:
    minutes = INTERVAL_MINUTES.get(interval, 5)
    per_session = int(np.ceil(375 / minutes))
    if df.empty:
        return 0.0
    idx = df.index.tz_convert(TZ) if df.index.tz is not None else df.index
    days = pd.Series(idx.date).value_counts()
    # the last day may be in progress; measure completed days only when possible
    if len(days) > 1:
        last_day = max(days.index)
        days = days.drop(last_day)
    expected = per_session * len(days)
    return float(min(1.0, days.sum() / expected)) if expected else 1.0


def expected_latest_candle(interval: str, at: datetime | None = None) -> datetime | None:
    """Latest completed candle start we should have at `at` (IST), or None if no
    session has started yet today and no previous trading day is computable."""
    at = at or now_ist()
    minutes = INTERVAL_MINUTES.get(interval, 5)
    day = at
    open_dt = datetime.combine(day.date(), SESSION_OPEN, TZ)
    close_dt = datetime.combine(day.date(), SESSION_CLOSE, TZ)
    if is_trading_day(day) and at >= open_dt + timedelta(minutes=minutes):
        end = min(at, close_dt)
        elapsed = int((end - open_dt).total_seconds() // 60)
        n_complete = elapsed // minutes
        return open_dt + timedelta(minutes=(n_complete - 1) * minutes)
    # otherwise last candle of previous trading day
    d = day - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(d):
            last_open = datetime.combine(d.date(), SESSION_CLOSE, TZ) - timedelta(minutes=minutes)
            return last_open
        d -= timedelta(days=1)
    return None


def freshness(cs: CandleSet, stale_after_minutes: int, at: datetime | None = None) -> tuple[bool, str]:
    """Return (is_fresh, message). Intraday candles are 'fresh' if the latest
    completed candle is within `stale_after_minutes` of what the clock implies."""
    at = at or now_ist()
    if cs.empty:
        return False, "No data"
    if cs.label == DataLabel.SIMULATED:
        return True, "Simulated data (demo mode) – not market data"
    last = cs.last_ts.tz_convert(TZ).to_pydatetime()
    if cs.interval == "1d":
        lag_days = (at.date() - last.date()).days
        if lag_days > 4:
            return False, f"Daily data is {lag_days} days old"
        return True, f"Last daily candle {last.date()}"
    expected = expected_latest_candle(cs.interval, at)
    if expected is None:
        return True, "No session reference available"
    lag = (expected - last).total_seconds() / 60
    if lag > stale_after_minutes:
        return False, f"Latest candle {last:%Y-%m-%d %H:%M} is {lag:.0f} min behind expected {expected:%H:%M} (stale)"
    return True, f"Latest candle {last:%Y-%m-%d %H:%M} IST (lag {max(lag, 0):.0f} min)"
