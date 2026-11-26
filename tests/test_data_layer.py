"""Yahoo adapter (mocked), instrument mapping, candle validation, freshness, cache."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from brokers.base import DataLabel, DataProviderError
from data.market_hours import TZ
from data.validation import clean_candles, freshness, intraday_completeness, validate_candles
from tests.conftest import make_session


# ---------------------------------------------------------------- yahoo adapter
class FakeTicker:
    calls = []
    fail_times = 0
    frame = None

    def __init__(self, sym):
        self.sym = sym

    def history(self, **kw):
        FakeTicker.calls.append((self.sym, kw))
        if FakeTicker.fail_times > 0:
            FakeTicker.fail_times -= 1
            raise ConnectionError("network down")
        return FakeTicker.frame.copy()


def _yahoo_frame(day="2026-11-24", n=30, freq="1min"):
    idx = pd.date_range(pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata"), periods=n, freq=freq)
    c = np.linspace(100, 103, n)
    return pd.DataFrame({"Open": c - 0.1, "High": c + 0.3, "Low": c - 0.3, "Close": c, "Volume": 1000,
                         "Dividends": 0.0, "Stock Splits": 0.0}, index=idx)


@pytest.fixture
def yahoo(monkeypatch):
    import yfinance

    from brokers import yahoo_client

    FakeTicker.calls, FakeTicker.fail_times = [], 0
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr(yahoo_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(yahoo_client, "now_ist", lambda: datetime(2026, 11, 24, 9, 45, 30, tzinfo=TZ))
    return yahoo_client.YahooFinanceProvider(request_delay_sec=0)


def test_symbol_mapping():
    from brokers.yahoo_client import to_yahoo_symbol

    assert to_yahoo_symbol("reliance") == "RELIANCE.NS"
    assert to_yahoo_symbol("M&M") == "M&M.NS"
    assert to_yahoo_symbol("^NSEI") == "^NSEI"
    assert to_yahoo_symbol("TCS.NS") == "TCS.NS"


def test_yahoo_candles_normalised_and_provisional_dropped(yahoo):
    FakeTicker.frame = _yahoo_frame(n=31)  # last candle 09:45 still forming at 09:45:30
    cs = yahoo.get_candles("RELIANCE", "1m", 2)
    assert list(cs.df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(cs.df.index.tz) == "Asia/Kolkata"
    assert cs.df.index[-1] == pd.Timestamp("2026-11-24 09:44", tz=TZ)
    assert cs.label == DataLabel.DELAYED
    assert FakeTicker.calls[0][0] == "RELIANCE.NS"


def test_yahoo_include_provisional(yahoo):
    FakeTicker.frame = _yahoo_frame(n=31)
    cs = yahoo.get_candles("SBIN", "1m", 2, include_provisional=True)
    assert cs.includes_provisional and cs.df.index[-1] == pd.Timestamp("2026-11-24 09:45", tz=TZ)


def test_yahoo_3m_resampled_from_1m(yahoo):
    FakeTicker.frame = _yahoo_frame(n=30)
    cs = yahoo.get_candles("SBIN", "3m", 2)
    assert (cs.df.index.minute % 3 == 0).all()
    first = FakeTicker.frame.iloc[:3]
    assert cs.df["volume"].iloc[0] == first["Volume"].sum()
    assert cs.df["high"].iloc[0] == pytest.approx(first["High"].max())


def test_yahoo_retry_then_success(yahoo):
    FakeTicker.frame = _yahoo_frame()
    FakeTicker.fail_times = 2
    cs = yahoo.get_candles("ITC", "1m", 2)
    assert not cs.empty and len(FakeTicker.calls) == 3


def test_yahoo_failure_raises_not_fake_data(yahoo):
    FakeTicker.frame = _yahoo_frame()
    FakeTicker.fail_times = 10
    with pytest.raises(DataProviderError):
        yahoo.get_candles("ITC", "1m", 2)


def test_yahoo_history_limit_warning(yahoo):
    FakeTicker.frame = _yahoo_frame()
    cs = yahoo.get_candles("ITC", "1m", 30)
    assert any("limits" in w for w in cs.warnings)


def test_yahoo_old_data_labelled_historical(yahoo):
    FakeTicker.frame = _yahoo_frame(day="2026-11-20")
    cs = yahoo.get_candles("ITC", "1m", 7)
    assert cs.label == DataLabel.HISTORICAL


# ---------------------------------------------------------------- instruments
def test_seed_universe_loads():
    from data.instrument_master import load_seed_universe

    u = load_seed_universe()
    assert len(u) > 50 and all(i.symbol == i.symbol.upper() for i in u)
    assert len({i.symbol for i in u}) == len(u)


def test_nse_master_filters():
    from data.instrument_master import parse_nse_equity_master

    csv = ("SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ISIN NUMBER\n"
           "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,INE002A01018\n"
           "NIFTYBEES,Nippon India ETF Nifty 50 BeES,EQ,08-JAN-2002,INF204KB14I2\n"
           "XYZBOND,XYZ Bond 2030,N1,01-JAN-2020,INE000000000\n"
           "TINYCO,Tiny Company Limited,BE,01-JAN-2020,INE000000001\n"
           "BAD SYMBOL!,Bad,EQ,01-JAN-2020,INE0\n")
    inst, rep = parse_nse_equity_master(csv)
    syms = [i.symbol for i in inst]
    assert syms == ["RELIANCE"]
    assert rep["total"] == 5 and rep["final"] == 1
    assert inst[0].sector == "ENERGY" and inst[0].isin == "INE002A01018"


def test_nse_master_rejects_wrong_file():
    from data.instrument_master import parse_nse_equity_master

    with pytest.raises(ValueError):
        parse_nse_equity_master("a,b\n1,2\n")


def test_watchlist_and_manual_parsing():
    from data.instrument_master import parse_manual_symbols, parse_watchlist_csv

    v, r = parse_watchlist_csv("symbol,qty\nreliance.ns,1\nTCS,2\nbad sym!,3\nTCS,4\n")
    assert v == ["RELIANCE", "TCS"] and r == ["BAD SYM!"]
    v, r = parse_watchlist_csv("INFY\nM&M\n")
    assert v == ["INFY", "M&M"]
    v, r = parse_manual_symbols("sbin, itc; ##")
    assert v == ["SBIN", "ITC"] and r == ["##"]


def test_liquidity_assessment(sim, settings):
    from data.instrument_master import assess_liquidity

    d = sim.get_candles("RELIANCE", "1d", 60).df
    rep = assess_liquidity("RELIANCE", d, settings.scan)
    assert rep.avg_traded_value_cr > 0 and rep.history_days >= 50
    thin = d.copy()
    thin["volume"] = 10
    rep2 = assess_liquidity("X", thin, settings.scan)
    assert not rep2.eligible and any("traded value" in r for r in rep2.reasons)
    frozen = d.copy()
    frozen.iloc[-1, frozen.columns.get_loc("high")] = frozen["low"].iloc[-1]
    frozen.iloc[-1, frozen.columns.get_loc("open")] = frozen["low"].iloc[-1]
    frozen.iloc[-1, frozen.columns.get_loc("close")] = frozen["low"].iloc[-1]
    assert assess_liquidity("X", frozen, settings.scan).circuit_suspected


# ---------------------------------------------------------------- validation
def test_clean_candles_drops_bad_rows():
    df = make_session("2026-11-24", [100, 101, 102, 103])
    df.iloc[1, df.columns.get_loc("high")] = 50  # impossible: high < close
    df = pd.concat([df, df.iloc[[2]]])  # duplicate
    out = clean_candles(df)
    assert len(out) == 3 and out.index.is_monotonic_increasing and not out.index.has_duplicates


def test_validate_reports_issues_and_completeness():
    d1 = make_session("2026-11-23", np.linspace(100, 101, 75))
    d2 = make_session("2026-11-24", np.linspace(101, 102, 10))
    full = pd.concat([d1, d2])
    rep = validate_candles(full, "5m")
    assert rep.ok and rep.completeness == pytest.approx(1.0)
    gappy = pd.concat([d1.iloc[::2], d2])
    assert intraday_completeness(gappy, "5m") < 0.6
    assert not validate_candles(pd.DataFrame(), "5m").ok


def test_freshness_detects_stale_data():
    from brokers.base import CandleSet

    df = make_session("2026-11-24", np.linspace(100, 101, 20))  # last candle 10:50
    cs = CandleSet("X", "5m", df, DataLabel.DELAYED, "test", datetime.now(TZ))
    ok, _ = freshness(cs, 25, at=datetime(2026, 11, 24, 11, 0, 10, tzinfo=TZ))
    assert ok
    ok, msg = freshness(cs, 25, at=datetime(2026, 11, 24, 12, 30, tzinfo=TZ))
    assert not ok and "stale" in msg
    sim_cs = CandleSet("X", "5m", df, DataLabel.SIMULATED, "sim", datetime.now(TZ))
    assert freshness(sim_cs, 25, at=datetime(2026, 11, 24, 15, 0, tzinfo=TZ))[0]


def test_candle_cache_upsert_no_duplicates(tmp_path):
    from data.cache import CandleCache

    c = CandleCache(tmp_path / "c.sqlite")
    df = make_session("2026-11-24", [100, 101, 102])
    assert c.upsert("X", "5m", df) == 3
    c.upsert("X", "5m", df)
    out = c.load("X", "5m")
    assert len(out) == 3 and out["close"].tolist() == [100, 101, 102]


def test_cached_provider_serves_cache_labelled_on_failure(tmp_path, sim):
    from brokers.base import MarketDataProvider
    from data.cache import CandleCache, CachedProvider

    class Flaky(MarketDataProvider):
        name = "flaky"
        fail = False

        def get_candles(self, *a, **k):
            if self.fail:
                raise DataProviderError("down")
            return sim.get_candles(*a, **k)

        def get_quote(self, *a, **k):
            return sim.get_quote(*a, **k)

    inner = Flaky()
    cp = CachedProvider(inner, CandleCache(tmp_path / "c.sqlite"))
    first = cp.get_candles("SBIN", "1d", 30)
    inner.fail = True
    second = cp.get_candles("SBIN", "1d", 3000)
    assert second.label == DataLabel.HISTORICAL and any("CACHED" in w for w in second.warnings)
    assert len(second.df) == len(first.df)
    with pytest.raises(DataProviderError):
        cp.get_candles("NEVERSEEN", "1d", 30)


def test_simulated_is_always_labelled(sim):
    cs = sim.get_candles("TCS", "5m", 3)
    q = sim.get_quote("TCS")
    assert cs.label == DataLabel.SIMULATED and q.label == DataLabel.SIMULATED
    with pytest.raises(DataProviderError):
        sim.get_candles("FAKESYM", "5m", 3)


def test_provider_factory_explicit(settings):
    from brokers.factory import make_provider
    from brokers.simulated import SimulatedProvider

    assert isinstance(make_provider(settings), SimulatedProvider)
    settings.app.data_provider = "nonsense"
    with pytest.raises(ValueError):
        make_provider(settings)
