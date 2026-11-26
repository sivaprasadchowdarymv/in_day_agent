"""NSE public-website client (mocked HTTP), composite multi-source provider,
market extras (breadth, VIX, holidays, circuits, deals) and index universes."""
from datetime import datetime

import pytest

from brokers.base import DataLabel
from brokers.nse_web import (NSEWebClient, NSEWebError, parse_all_indices, parse_holidays, parse_index_constituents,
                             parse_market_status, parse_price_band_hitters, parse_quote, parse_trade_info)
from data.market_hours import TZ

QUOTE_JSON = {
    "info": {"symbol": "RELIANCE", "companyName": "Reliance Industries Limited"},
    "metadata": {"lastUpdateTime": "24-Nov-2026 13:06:30", "industry": "Refineries & Marketing"},
    "securityInfo": {"tradingStatus": "Active"},
    "industryInfo": {"macro": "Energy", "sector": "Oil Gas & Consumable Fuels", "industry": "Petroleum Products",
                     "basicIndustry": "Refineries & Marketing"},
    "priceInfo": {"lastPrice": 1301.5, "previousClose": 1290.0, "open": 1292.0, "vwap": 1298.2, "pChange": 0.89,
                  "lowerCP": "1161.00", "upperCP": "1419.00", "pPriceBand": "No Band",
                  "intraDayHighLow": {"min": 1288.1, "max": 1305.0, "value": 1301.5}},
}
TRADE_JSON = {
    "marketDeptOrderBook": {"totalBuyQuantity": 536527, "totalSellQuantity": 486427,
                            "bid": [{"price": 1301.4, "quantity": 1906}, {"price": 1301.3, "quantity": 50}],
                            "ask": [{"price": 1301.6, "quantity": 800}, {"price": 0, "quantity": 0}]},
    "securityWiseDP": {"quantityTraded": 1000000, "deliveryQuantity": 450000, "deliveryToTradedQuantity": 45.0},
}
INDICES_JSON = {"timestamp": "24-Nov-2026 13:06", "data": [
    {"index": "NIFTY 50", "indexSymbol": "NIFTY 50", "last": 25100.5, "percentChange": 0.42, "previousClose": 24995,
     "advances": "35", "declines": "15", "unchanged": "0"},
    {"index": "NIFTY 500", "indexSymbol": "NIFTY 500", "last": 23000, "percentChange": 0.3,
     "advances": "380", "declines": "115", "unchanged": "5"},
    {"index": "INDIA VIX", "indexSymbol": "INDIA VIX", "last": 13.4, "percentChange": -2.1},
    {"index": "NIFTY BANK", "indexSymbol": "NIFTY BANK", "last": 58000, "percentChange": 1.1},
    {"index": "NIFTY IT", "indexSymbol": "NIFTY IT", "last": 36000, "percentChange": "-0.6"},
]}
HOLIDAYS_JSON = {"CM": [{"tradingDate": "26-Jan-2026", "weekDay": "Monday", "description": "Republic Day"},
                        {"tradingDate": "bad-date", "description": "x"}], "FO": []}
STATUS_JSON = {"marketState": [{"market": "Capital Market", "marketStatus": "Open", "tradeDate": "24-Nov-2026",
                                "marketStatusMessage": "Normal Market is Open"}]}
BANDS_JSON = {"upper": {"AllSec": {"data": [{"symbol": "ABC", "series": "BE"}]}},
              "lower": {"AllSec": {"data": [{"symbol": "xyz"}]}}}
CONSTITUENTS = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
                "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
                "Some Pharma Ltd.,Healthcare,SOMEPHARMA,EQ,INE000000001\n"
                "Odd Co.,Telecommunication,ODDCO,BE,INE000000002\n")
DEALS = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n" \
        "24-NOV-2026,SBIN,State Bank,ABC FUND,BUY,1200000,812.5\n"


# ---------------------------------------------------------------- parsers
def test_parse_quote_fields():
    q = parse_quote("reliance", QUOTE_JSON)
    assert q.symbol == "RELIANCE" and q.ltp == 1301.5 and q.prev_close == 1290.0
    assert q.lower_band == 1161.0 and q.upper_band == 1419.0 and q.day_high == 1305.0
    assert q.last_update == datetime(2026, 11, 24, 13, 6, 30, tzinfo=TZ)
    assert q.sector == "Oil Gas & Consumable Fuels"


def test_parse_quote_missing_fields_are_none():
    q = parse_quote("X", {"priceInfo": {"lastPrice": "-", "lowerCP": ""}})
    assert q.ltp is None and q.lower_band is None and q.last_update is None


def test_parse_trade_info():
    t = parse_trade_info("RELIANCE", TRADE_JSON)
    assert t.best_bid == 1301.4 and t.best_ask == 1301.6 and t.delivery_pct == 45.0


def test_parse_indices_holidays_status_bands_constituents():
    df = parse_all_indices(INDICES_JSON)
    assert df.loc[df["index"] == "NIFTY IT", "change_pct"].iloc[0] == -0.6
    assert parse_holidays(HOLIDAYS_JSON) == [{"date": "2026-01-26", "description": "Republic Day"}]
    assert parse_market_status(STATUS_JSON)["status"] == "Open"
    assert parse_price_band_hitters(BANDS_JSON) == {"upper": {"ABC"}, "lower": {"XYZ"}}
    c = parse_index_constituents(CONSTITUENTS)
    assert list(c["Symbol"]) == ["HDFCBANK", "SOMEPHARMA", "ODDCO"]
    with pytest.raises(NSEWebError):
        parse_index_constituents("a,b\n1,2\n")


# ---------------------------------------------------------------- HTTP client
class FakeResp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.content = code, payload, text.encode()

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        if url.rstrip("/") == "https://www.nseindia.com":
            return FakeResp(200, None, "<html>")
        return self.responses.pop(0)


@pytest.fixture
def nosleep(monkeypatch):
    import brokers.nse_web as m

    monkeypatch.setattr(m.time, "sleep", lambda s: None)  # restored after each test


def _client(responses):
    return NSEWebClient(min_interval_sec=0, session=FakeSession(responses))


def test_client_refreshes_cookies_after_403(nosleep):
    c = _client([FakeResp(403), FakeResp(200, QUOTE_JSON)])
    q = c.quote("RELIANCE")
    assert q.ltp == 1301.5
    homes = [u for u in c.s.calls if u.rstrip("/") == "https://www.nseindia.com"]
    assert len(homes) == 2  # warmed up again after the block


def test_client_raises_and_backs_off(nosleep):
    c = _client([FakeResp(500), FakeResp(500), FakeResp(500)])
    with pytest.raises(NSEWebError):
        c.quote("RELIANCE")
    with pytest.raises(NSEWebError):
        c.all_indices()  # backed off without calling
    assert c.disabled_until > 0


def test_client_caches(nosleep):
    c = _client([FakeResp(200, INDICES_JSON)])
    a, b = c.all_indices(), c.all_indices()
    assert len(a) == len(b) == 5


# ---------------------------------------------------------------- composite
class FakeNSE:
    def __init__(self, fail=False, quote=None):
        self.fail = fail
        self._q = quote or QUOTE_JSON

    def quote(self, s):
        if self.fail:
            raise NSEWebError("blocked")
        return parse_quote(s, self._q)

    def trade_info(self, s):
        return parse_trade_info(s, TRADE_JSON)

    def all_indices(self):
        return parse_all_indices(INDICES_JSON)

    def holidays(self):
        return parse_holidays(HOLIDAYS_JSON)

    def market_status(self):
        return parse_market_status(STATUS_JSON)

    def price_band_hitters(self):
        return parse_price_band_hitters(BANDS_JSON)

    def index_constituents(self, name):
        return parse_index_constituents(CONSTITUENTS)

    def equity_master_csv(self):
        return "SYMBOL,NAME OF COMPANY,SERIES\nSBIN,State Bank of India,EQ\nNIFTYBEES,Nippon ETF Nifty BeES,EQ\n"

    def bulk_deals(self):
        from brokers.nse_web import parse_deals

        return parse_deals(DEALS)

    def block_deals(self):
        raise NSEWebError("no block file")


def test_composite_live_label_bidask_and_crosscheck(sim, monkeypatch):
    from brokers import composite

    monkeypatch.setattr(composite, "now_ist", lambda: datetime(2026, 11, 24, 13, 8, tzinfo=TZ))
    monkeypatch.setattr(composite, "is_market_open", lambda at=None: True)
    p = composite.CompositeProvider(sim, FakeNSE())
    q = p.get_quote("RELIANCE")
    assert q.label == DataLabel.LIVE and q.ltp == 1301.5 and q.bid == 1301.4 and q.ask == 1301.6
    assert q.spread_pct == pytest.approx(0.2 / 1301.5 * 100, rel=1e-3)
    assert q.upper_band == 1419.0 and q.delivery_pct == 45.0
    assert "NSE website" in q.sources and len(q.sources) == 2
    assert any("Cross-check" in w for w in q.warnings)  # simulated prev close ≠ NSE prev close
    # candles still come from the candle provider
    assert p.get_candles("RELIANCE", "5m", 2).label == DataLabel.SIMULATED


def test_composite_stale_quote_is_delayed_and_fallback(sim, monkeypatch):
    from brokers import composite

    monkeypatch.setattr(composite, "now_ist", lambda: datetime(2026, 11, 24, 14, 30, tzinfo=TZ))
    monkeypatch.setattr(composite, "is_market_open", lambda at=None: True)
    assert composite.CompositeProvider(sim, FakeNSE()).get_quote("RELIANCE").label == DataLabel.DELAYED
    q = composite.CompositeProvider(sim, FakeNSE(fail=True)).get_quote("RELIANCE")
    assert q.label == DataLabel.SIMULATED and any("NSE quote unavailable" in w for w in q.warnings)


def test_near_circuit_detection(sim, monkeypatch):
    from brokers import composite

    j = {**QUOTE_JSON, "priceInfo": {**QUOTE_JSON["priceInfo"], "lastPrice": 1418.0}}
    q = composite.CompositeProvider(sim, FakeNSE(quote=j), cross_check=False).get_quote("RELIANCE")
    assert q.near_circuit == "UPPER"


# ---------------------------------------------------------------- extras
def test_market_snapshot_breadth_vix_sectors():
    from data.market_extras import market_snapshot

    snap, w = market_snapshot(FakeNSE())
    assert snap["breadth"]["advances"] == 380 and snap["breadth"]["declines"] == 115
    assert snap["vix"] == 13.4 and snap["sector_changes"] == {"BANK": 1.1, "IT": -0.6}
    assert snap["nifty_change_pct"] == 0.42


def test_breadth_adjusts_regime():
    from market.regime import IndexState, Regime, classify

    st = IndexState("NIFTY 50", "^NSEI", ltp=1, score=0.15)
    assert classify([st], 12)[0] == Regime.SIDEWAYS
    r, s, ev = classify([st], 12, breadth={"advances": 380, "declines": 115, "unchanged": 5, "source": "t"})
    assert r == Regime.MILD_BULLISH and any("breadth" in e.lower() for e in ev)


def test_regime_uses_nse_vix_and_breadth(sim):
    from brokers.composite import CompositeProvider
    from market.regime import compute_regime
    from market.sector_strength import compute_sector_strength

    p = CompositeProvider(sim, FakeNSE())
    r = compute_regime(p)
    assert r.vix == 13.4 and r.vix_source == "NSE allIndices" and r.market_breadth["advances"] == 380
    df, _ = compute_sector_strength(p, r.sector_changes_live, r.nifty_change_live)
    bank = df[df["sector"] == "BANK"].iloc[0]
    assert bank["change_1d_pct"] == 1.1 and bank["source_1d"] == "NSE live"
    assert bank["rs_1d"] == pytest.approx(1.1 - 0.42)


def test_holidays_loaded_into_calendar(tmp_path, monkeypatch):
    from config.markets import NSE_HOLIDAYS
    from data import market_extras

    monkeypatch.setattr(market_extras, "HOLIDAY_CACHE", tmp_path / "h.json")
    n, _ = market_extras.refresh_holidays(FakeNSE())
    try:
        assert n == 1 and "2026-01-26" in NSE_HOLIDAYS
        from data.market_hours import is_trading_day

        assert not is_trading_day(datetime(2026, 1, 26, 10, tzinfo=TZ))
        NSE_HOLIDAYS.discard("2026-01-26")
        n2, w = market_extras.refresh_holidays(None)  # from disk cache
        assert n2 == 1 and "2026-01-26" in NSE_HOLIDAYS
    finally:
        NSE_HOLIDAYS.discard("2026-01-26")


def test_deals_and_hitters():
    from data.market_extras import circuit_hitters, deals_for

    d, w = deals_for(FakeNSE(), {"SBIN", "TCS"})
    assert "SBIN" in d and "ABC FUND" in d["SBIN"][0] and any("Block" in x for x in w)
    h, _ = circuit_hitters(FakeNSE())
    assert "ABC" in h["upper"]


def test_sector_mapping_and_index_universe():
    from agents.agents import InstrumentDiscoveryAgent
    from agents.schemas import UniverseRequest
    from data.instrument_master import map_nse_sector

    assert map_nse_sector("Financial Services") == "FINSERV"
    assert map_nse_sector("Healthcare") == "PHARMA"
    assert map_nse_sector("Something else") == "UNKNOWN"
    r = InstrumentDiscoveryAgent().run(UniverseRequest(mode="index", index_name="NIFTY 50", nse=FakeNSE(), limit=10))
    syms = {i.symbol: i.sector for i in r.output.instruments}
    assert syms == {"HDFCBANK": "BANK", "SOMEPHARMA": "PHARMA"}  # BE series excluded; seed sector preferred
    r2 = InstrumentDiscoveryAgent().run(UniverseRequest(mode="master_auto", nse=FakeNSE()))
    assert [i.symbol for i in r2.output.instruments] == ["SBIN"]
    r3 = InstrumentDiscoveryAgent().run(UniverseRequest(mode="index", nse=None))
    assert r3.warnings and len(r3.output.instruments) > 50


def test_circuit_rejection_rule(sim):
    from ranking.filters import mandatory_rejections
    from tests.test_ranking import _ind, _plan

    ind = _ind(sim)
    _, plan = _plan(ind)
    plan.issues, plan.risk_reward, plan.status = [], 2.0, "WAITING_FOR_TRIGGER"
    from market.regime import Regime

    base = dict(data_fresh=True, freshness_msg="", data_label="DELAYED", liquidity_ok=True, liquidity_reasons=[],
                plan=plan, min_rr=1.5, live_spread_pct=None, max_spread_pct=0.15, provisional=False,
                regime=Regime.MILD_BULLISH, strategy_regimes=(), symbol_verified=True, circuit_suspected=False,
                score=80, min_score=55, allow_short=True, account_gate_reasons=[])
    assert mandatory_rejections(**base) == []
    assert any("circuit" in x for x in mandatory_rejections(**base, near_circuit="UPPER"))
    assert any("price-band" in x for x in mandatory_rejections(**base, circuit_hitter="LOWER"))


def test_orchestrator_with_composite_sources(sim, settings):
    from agents.orchestrator import Orchestrator
    from agents.schemas import ScanRequest, UniverseRequest
    from brokers.composite import CompositeProvider

    p = CompositeProvider(sim, FakeNSE())
    out = Orchestrator(p, settings, persist=False).run(ScanRequest(UniverseRequest(limit=10)))
    assert out.regime.market_breadth["advances"] == 380
    assert out.state_log[-1]["to"] in ("DONE", "NO_TRADE")
    for c in out.candidates:
        assert c.quote is None or "NSE website" in c.quote.sources
