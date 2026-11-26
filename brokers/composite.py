"""Multi-source provider: candles from Yahoo Finance, quotes/order book/circuit
limits/delivery % from the NSE public website, with cross-validation.

Labelling rules
* NSE quote time-stamped ≤ 3 min ago while the market is open → LIVE (NSE website, unofficial).
* Older same-day NSE quote → DELAYED. Previous-day → HISTORICAL.
* If NSE fails, the Yahoo quote is used and the reason is attached as a warning.
* If both sources respond and their previous closes differ by > 0.5 %, a
  cross-check warning is attached (possible corporate action / data error).
"""
from __future__ import annotations

from brokers.base import CandleSet, DataLabel, DataProviderError, MarketDataProvider, Quote
from brokers.nse_web import NSEWebClient, NSEWebError, quote_age_minutes
from config.logging_setup import get_logger
from data.market_hours import is_market_open, now_ist

log = get_logger("composite")


class CompositeProvider(MarketDataProvider):
    realtime_label = DataLabel.DELAYED

    def __init__(self, candles: MarketDataProvider, nse: NSEWebClient, use_trade_info: bool = True,
                 cross_check: bool = True):
        self.candles = candles
        self.nse = nse
        self.use_trade_info = use_trade_info
        self.cross_check = cross_check
        self.name = f"{candles.name} + NSE website"

    def get_candles(self, symbol, interval, days, *, is_index=False, include_provisional=False) -> CandleSet:
        return self.candles.get_candles(symbol, interval, days, is_index=is_index,
                                        include_provisional=include_provisional)

    def get_quote(self, symbol: str, *, is_index: bool = False) -> Quote:
        if is_index or symbol.startswith("^"):
            return self.candles.get_quote(symbol, is_index=True)
        try:
            nq = self.nse.quote(symbol)
        except NSEWebError as exc:
            q = self.candles.get_quote(symbol)
            q.warnings.append(f"NSE quote unavailable ({exc}); using {self.candles.name}")
            q.sources = [self.candles.name]
            return q
        if nq.ltp is None:
            q = self.candles.get_quote(symbol)
            q.warnings.append("NSE quote had no last price; using fallback source")
            q.sources = [self.candles.name]
            return q
        now = now_ist()
        age = quote_age_minutes(nq, now)
        if nq.last_update is None:
            label = DataLabel.DELAYED
        elif nq.last_update.date() < now.date():
            label = DataLabel.HISTORICAL
        elif age is not None and age <= 3 and is_market_open(now):
            label = DataLabel.LIVE
        else:
            label = DataLabel.DELAYED
        warnings = ["Quote from NSE public website (free, unofficial endpoint)."]
        bid = ask = dpct = None
        if self.use_trade_info:
            try:
                ti = self.nse.trade_info(symbol)
                bid, ask, dpct = ti.best_bid, ti.best_ask, ti.delivery_pct
            except NSEWebError as exc:
                warnings.append(f"Order book unavailable ({exc}); spread not checked")
        q = Quote(symbol=symbol, ltp=nq.ltp, timestamp=nq.last_update, label=label, source="NSE website",
                  prev_close=nq.prev_close, day_open=nq.open, day_high=nq.day_high, day_low=nq.day_low,
                  bid=bid, ask=ask, warnings=warnings, upper_band=nq.upper_band, lower_band=nq.lower_band,
                  delivery_pct=dpct, sources=["NSE website"])
        if self.cross_check:
            try:
                yq = self.candles.get_quote(symbol)
                q.sources.append(self.candles.name)
                notes = []
                if yq.prev_close and nq.prev_close:
                    d = abs(yq.prev_close / nq.prev_close - 1) * 100
                    if d > 0.5:
                        q.warnings.append(f"Cross-check: previous close differs {d:.2f}% between NSE "
                                          f"({nq.prev_close}) and Yahoo ({yq.prev_close:.2f}) – verify before trading")
                    notes.append(f"prev close Δ {d:.2f}%")
                if yq.ltp:
                    notes.append(f"Yahoo LTP {yq.ltp:.2f} ({yq.label.value}) vs NSE {nq.ltp:.2f}")
                q.cross_check = "; ".join(notes) or None
            except DataProviderError as exc:
                q.cross_check = f"Yahoo cross-check unavailable: {exc}"
        if q.near_circuit:
            q.warnings.append(f"Price within 0.5% of {q.near_circuit} circuit limit")
        return q

    def test_connection(self) -> dict:
        out = self.candles.test_connection()
        try:
            ms = self.nse.market_status()
            out["nse_website"] = {"ok": True, "market_status": ms}
        except NSEWebError as exc:
            out["nse_website"] = {"ok": False, "error": str(exc)}
        return out
