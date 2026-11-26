"""Verify screenshot-derived evidence against freshly fetched market data."""
from __future__ import annotations

import pandas as pd

from brokers.base import DataProviderError, MarketDataProvider
from data.market_hours import now_ist
from indicators.compute import compute_all
from vision.schemas import ScreenshotEvidence, VerificationResult

PRICE_TOLERANCE_PCT = 3.0


def verify_evidence(ev: ScreenshotEvidence, provider: MarketDataProvider, universe: set[str],
                    symbol_override: str | None = None, default_interval: str = "5m") -> VerificationResult:
    sym = (symbol_override or ev.symbol.value or "").upper() or None
    res = VerificationResult(image_id=ev.image_id, symbol=sym, status="UNVERIFIED")
    if not sym:
        res.reasons.append("No symbol identified – add it manually to verify")
        return res
    if sym not in universe:
        res.reasons.append(f"{sym} is not in the configured NSE universe")
    interval = ev.timeframe.value if ev.timeframe.value in ("1m", "3m", "5m", "15m", "30m", "1h") else default_interval
    if ev.timeframe.value == "1d":
        res.agreements.append("Screenshot is a daily chart; intraday verification uses " + default_interval)
    res.interval_used = interval
    try:
        cs = provider.get_candles(sym, interval, 10)
        q = provider.get_quote(sym)
    except DataProviderError as exc:
        res.reasons.append(f"Symbol could not be validated with data provider: {exc}")
        return res
    if cs.empty or q.ltp is None:
        res.reasons.append("No market data returned for symbol")
        return res
    res.current_price, res.current_price_label = q.ltp, q.label.value
    res.current_price_ts = str(q.timestamp)
    ind = compute_all(cs.df, interval)
    last = ind.iloc[-1]

    # price comparison
    sp = ev.visible_price.value
    if sp:
        dev = (q.ltp / float(sp) - 1) * 100
        msg = f"Screenshot price {sp} vs current {q.ltp:.2f} ({q.label.value}) = {dev:+.2f}%"
        (res.discrepancies if abs(dev) > PRICE_TOLERANCE_PCT else res.agreements).append(
            msg + (" – screenshot may be old or misread" if abs(dev) > PRICE_TOLERANCE_PCT else ""))
    # trend comparison
    if ev.trend.value in ("up", "down", "sideways") and pd.notna(last["ema9"]) and pd.notna(last["ema21"]):
        comp = "up" if last["ema9"] > last["ema21"] and last["close"] > last["ema21"] else (
            "down" if last["ema9"] < last["ema21"] and last["close"] < last["ema21"] else "sideways")
        msg = f"Screenshot trend '{ev.trend.value}' vs computed '{comp}' (EMA9/21, {interval})"
        (res.agreements if comp == ev.trend.value else res.discrepancies).append(msg)
    # RSI comparison
    if isinstance(ev.rsi.value, (int, float)) and pd.notna(last["rsi14"]):
        d = abs(float(ev.rsi.value) - float(last["rsi14"]))
        msg = f"Screenshot RSI {ev.rsi.value:.1f} vs computed {last['rsi14']:.1f}"
        (res.discrepancies if d > 10 else res.agreements).append(msg)
    # VWAP side
    if ev.vwap.value and pd.notna(last["vwap"]):
        res.agreements.append(f"Computed VWAP {last['vwap']:.2f}; price {'above' if last['close'] > last['vwap'] else 'below'} VWAP now")
    # timestamp
    if ev.timestamp.value:
        try:
            ts = pd.to_datetime(ev.timestamp.value, dayfirst=True, errors="raise")
            ref_day = q.timestamp.date() if q.timestamp else now_ist().date()
            if ts.date() != ref_day and ts.year > 2000:
                res.discrepancies.append(f"Screenshot dated {ts.date()} – latest market data is from {ref_day}")
        except (ValueError, TypeError, OverflowError):
            pass
    if res.reasons and any("not in the configured" in r for r in res.reasons):
        # symbol outside universe but provider confirms it exists: still verified, with a note
        res.reasons = [r for r in res.reasons if "not in the configured" not in r]
        res.discrepancies.append(f"{sym} is outside the configured universe (liquidity filters still apply)")
    res.status = "VERIFIED_WITH_DISCREPANCIES" if res.discrepancies else "VERIFIED"
    return res
