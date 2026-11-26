"""Provider factory. Selection is explicit – no silent fallback to simulated data."""
from __future__ import annotations

from brokers.base import MarketDataProvider
from config.settings import Settings


def demo_anchor(clock: str):
    """Simulated mode: pretend it is HH:MM IST on the most recent weekday."""
    if not clock:
        return None
    from datetime import datetime, timedelta

    from data.market_hours import TZ, is_trading_day, now_ist, parse_hhmm

    d = now_ist()
    t = parse_hhmm(clock)
    if d.time() < t or not is_trading_day(d):
        d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return datetime.combine(d.date(), t, TZ)


def make_provider(settings: Settings) -> MarketDataProvider:
    choice = (settings.app.data_provider or "yahoo").lower()
    if choice == "simulated":
        from brokers.simulated import SimulatedProvider

        return SimulatedProvider(anchor=demo_anchor(settings.app.demo_clock))
    if choice == "yahoo_nse":
        from brokers.composite import CompositeProvider
        from brokers.nse_web import get_client
        from brokers.yahoo_client import YahooFinanceProvider
        from data.cache import CachedProvider

        return CompositeProvider(CachedProvider(YahooFinanceProvider(request_delay_sec=settings.scan.request_delay_sec)),
                                 get_client())
    if choice == "yahoo":
        from brokers.yahoo_client import YahooFinanceProvider
        from data.cache import CachedProvider

        return CachedProvider(YahooFinanceProvider(request_delay_sec=settings.scan.request_delay_sec))
    raise ValueError(f"Unknown data provider '{choice}'. Use 'yahoo_nse', 'yahoo' or 'simulated'.")


def nse_client_of(provider):
    """The NSE website client behind a provider, or None (Yahoo-only / simulated)."""
    return getattr(provider, "nse", None)
