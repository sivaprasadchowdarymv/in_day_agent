"""Client for NSE's free public website data (nseindia.com JSON endpoints and
nsearchives / niftyindices CSV files).

IMPORTANT
* These endpoints power NSE's public web pages. They are free but UNOFFICIAL
  as an API: NSE may change, rate-limit or block them at any time. Every parser
  here reads fields defensively; a missing field becomes None, never a guess.
* NSE requires browser-like headers and cookies obtained from the home page.
* Be gentle: requests are rate-limited (default ≥ 1 s apart) and cached.
* Use is subject to NSE's website terms. For production or commercial use,
  license data from NSE Data & Analytics or use a broker API.
"""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote as urlquote

import pandas as pd
import requests

from config.logging_setup import get_logger
from data.market_hours import TZ, now_ist

log = get_logger("nse_web")

BASE = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"
NIFTYINDICES = "https://www.niftyindices.com"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

ENDPOINTS = {
    "quote": "/api/quote-equity?symbol={symbol}",
    "trade_info": "/api/quote-equity?symbol={symbol}&section=trade_info",
    "all_indices": "/api/allIndices",
    "market_status": "/api/marketStatus",
    "holidays": "/api/holiday-master?type=trading",
    "price_band_hitters": "/api/live-analysis-price-band-hitter",
}
ARCHIVE_FILES = {
    "equity_master": ARCHIVES + "/content/equities/EQUITY_L.csv",
    "bulk_deals": ARCHIVES + "/content/equities/bulk.csv",
    "block_deals": ARCHIVES + "/content/equities/block.csv",
}
INDEX_CONSTITUENTS = {
    "NIFTY 50": "ind_nifty50list.csv",
    "NIFTY NEXT 50": "ind_niftynext50list.csv",
    "NIFTY 100": "ind_nifty100list.csv",
    "NIFTY 200": "ind_nifty200list.csv",
    "NIFTY 500": "ind_nifty500list.csv",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv",
}


class NSEWebError(RuntimeError):
    pass


def _f(v) -> float | None:
    """Parse NSE numbers that may arrive as '1,234.50', '-', '' or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "NA", "N/A", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _ts(s: str | None) -> datetime | None:
    """NSE timestamps look like '26-Nov-2026 15:30:00' (IST)."""
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ parsed records
@dataclass
class NSEQuote:
    symbol: str
    company: str | None
    ltp: float | None
    prev_close: float | None
    open: float | None
    day_high: float | None
    day_low: float | None
    vwap: float | None
    change_pct: float | None
    lower_band: float | None
    upper_band: float | None
    price_band_pct: str | None
    last_update: datetime | None
    sector: str | None
    industry: str | None
    basic_industry: str | None
    status: str | None
    raw_keys: list[str] = field(default_factory=list)


@dataclass
class NSETradeInfo:
    symbol: str
    bid: list[tuple[float, float]]
    ask: list[tuple[float, float]]
    total_buy_qty: float | None
    total_sell_qty: float | None
    delivery_pct: float | None
    traded_qty: float | None
    delivery_qty: float | None

    @property
    def best_bid(self) -> float | None:
        prices = [p for p, q in self.bid if p and q]
        return max(prices) if prices else None

    @property
    def best_ask(self) -> float | None:
        prices = [p for p, q in self.ask if p and q]
        return min(prices) if prices else None


def parse_quote(symbol: str, j: dict) -> NSEQuote:
    pi = j.get("priceInfo") or {}
    md = j.get("metadata") or {}
    info = j.get("info") or {}
    ind = j.get("industryInfo") or {}
    sec = j.get("securityInfo") or {}
    hl = pi.get("intraDayHighLow") or {}
    return NSEQuote(
        symbol=symbol.upper(), company=info.get("companyName"), ltp=_f(pi.get("lastPrice")),
        prev_close=_f(pi.get("previousClose")), open=_f(pi.get("open")), day_high=_f(hl.get("max")),
        day_low=_f(hl.get("min")), vwap=_f(pi.get("vwap")), change_pct=_f(pi.get("pChange")),
        lower_band=_f(pi.get("lowerCP")), upper_band=_f(pi.get("upperCP")),
        price_band_pct=str(pi.get("pPriceBand")) if pi.get("pPriceBand") is not None else None,
        last_update=_ts(md.get("lastUpdateTime")), sector=ind.get("sector") or ind.get("macro"),
        industry=ind.get("industry") or md.get("industry"), basic_industry=ind.get("basicIndustry"),
        status=sec.get("tradingStatus"), raw_keys=sorted(j.keys()))


def parse_trade_info(symbol: str, j: dict) -> NSETradeInfo:
    ob = j.get("marketDeptOrderBook") or {}
    dp = j.get("securityWiseDP") or {}

    def side(rows):
        out = []
        for r in rows or []:
            p, q = _f(r.get("price")), _f(r.get("quantity"))
            if p is not None and q is not None:
                out.append((p, q))
        return out

    return NSETradeInfo(symbol.upper(), side(ob.get("bid")), side(ob.get("ask")), _f(ob.get("totalBuyQuantity")),
                        _f(ob.get("totalSellQuantity")), _f(dp.get("deliveryToTradedQuantity")),
                        _f(dp.get("quantityTraded")), _f(dp.get("deliveryQuantity")))


def parse_all_indices(j: dict) -> pd.DataFrame:
    rows = []
    for r in j.get("data") or []:
        rows.append({"index": r.get("index"), "symbol": r.get("indexSymbol"), "last": _f(r.get("last")),
                     "change_pct": _f(r.get("percentChange")), "prev_close": _f(r.get("previousClose")),
                     "open": _f(r.get("open")), "high": _f(r.get("high")), "low": _f(r.get("low")),
                     "advances": _f(r.get("advances")), "declines": _f(r.get("declines")),
                     "unchanged": _f(r.get("unchanged"))})
    df = pd.DataFrame(rows)
    df.attrs["timestamp"] = j.get("timestamp")
    return df


def parse_holidays(j: dict) -> list[dict]:
    rows = j.get("CM") or j.get("cm") or []
    out = []
    for r in rows:
        d = _ts(r.get("tradingDate"))
        if d:
            out.append({"date": d.strftime("%Y-%m-%d"), "description": r.get("description", "")})
    return out


def parse_market_status(j: dict) -> dict | None:
    for m in j.get("marketState") or []:
        if str(m.get("market", "")).lower().startswith("capital"):
            return {"status": m.get("marketStatus"), "trade_date": m.get("tradeDate"),
                    "message": m.get("marketStatusMessage")}
    return None


def parse_price_band_hitters(j: dict) -> dict[str, set[str]]:
    out = {"upper": set(), "lower": set()}
    for side in ("upper", "lower"):
        block = (j.get(side) or {}).get("AllSec") or {}
        for r in block.get("data") or []:
            if r.get("symbol"):
                out[side].add(str(r["symbol"]).upper())
    return out


def parse_index_constituents(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    need = {"Symbol", "Company Name"}
    if not need.issubset(df.columns):
        raise NSEWebError(f"Unexpected constituent file columns: {list(df.columns)}")
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    return df


def parse_deals(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    return df


# ------------------------------------------------------------------ client
class NSEWebClient:
    def __init__(self, min_interval_sec: float = 1.0, timeout: int = 10, max_retries: int = 2,
                 session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update(HEADERS)
        self.min_interval = min_interval_sec
        self.timeout = timeout
        self.max_retries = max_retries
        self._last = 0.0
        self._lock = threading.Lock()
        self._warm = False
        self._cache: dict[str, tuple[float, object]] = {}
        self.disabled_until = 0.0  # back off after repeated blocks

    def _wait(self):
        with self._lock:
            d = time.monotonic() - self._last
            if d < self.min_interval:
                time.sleep(self.min_interval - d)
            self._last = time.monotonic()

    def _warmup(self):
        if self._warm:
            return
        self._wait()
        try:
            self.s.get(BASE, timeout=self.timeout, headers={"Accept": "text/html"})
            self._warm = True
        except requests.RequestException as exc:
            raise NSEWebError(f"NSE home page unreachable: {type(exc).__name__}") from exc

    def _get(self, url: str, kind: str = "json", ttl: float = 30.0):
        if time.monotonic() < self.disabled_until:
            raise NSEWebError("NSE website temporarily backed off after repeated blocks")
        hit = self._cache.get(url)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                if url.startswith(BASE):
                    self._warmup()
                self._wait()
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code in (401, 403):
                    self._warm = False  # refresh cookies and retry
                    last = NSEWebError(f"HTTP {r.status_code} (blocked / cookies expired)")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    last = NSEWebError(f"HTTP {r.status_code}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                data = r.json() if kind == "json" else r.content.decode("utf-8", "ignore")
                if kind == "json" and not isinstance(data, dict):
                    raise NSEWebError("Unexpected JSON shape")
                self._cache[url] = (time.monotonic(), data)
                return data
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(1.0 * (attempt + 1))
        self.disabled_until = time.monotonic() + 60
        raise NSEWebError(f"NSE request failed: {last}")

    # ---- JSON endpoints
    def quote(self, symbol: str) -> NSEQuote:
        return parse_quote(symbol, self._get(BASE + ENDPOINTS["quote"].format(symbol=urlquote(symbol.upper())), ttl=15))

    def trade_info(self, symbol: str) -> NSETradeInfo:
        return parse_trade_info(symbol, self._get(
            BASE + ENDPOINTS["trade_info"].format(symbol=urlquote(symbol.upper())), ttl=15))

    def all_indices(self) -> pd.DataFrame:
        return parse_all_indices(self._get(BASE + ENDPOINTS["all_indices"], ttl=30))

    def market_status(self) -> dict | None:
        return parse_market_status(self._get(BASE + ENDPOINTS["market_status"], ttl=60))

    def holidays(self) -> list[dict]:
        return parse_holidays(self._get(BASE + ENDPOINTS["holidays"], ttl=86400))

    def price_band_hitters(self) -> dict[str, set[str]]:
        return parse_price_band_hitters(self._get(BASE + ENDPOINTS["price_band_hitters"], ttl=60))

    # ---- CSV archives
    def equity_master_csv(self) -> str:
        return self._get(ARCHIVE_FILES["equity_master"], kind="text", ttl=86400)

    def index_constituents(self, index_name: str) -> pd.DataFrame:
        fname = INDEX_CONSTITUENTS[index_name]
        last = None
        for url in (f"{NIFTYINDICES}/IndexConstituent/{fname}", f"{ARCHIVES}/content/indices/{fname}"):
            try:
                return parse_index_constituents(self._get(url, kind="text", ttl=86400))
            except (NSEWebError, ValueError) as exc:
                last = exc
        raise NSEWebError(f"Constituents for {index_name} unavailable: {last}")

    def bulk_deals(self) -> pd.DataFrame:
        return parse_deals(self._get(ARCHIVE_FILES["bulk_deals"], kind="text", ttl=3600))

    def block_deals(self) -> pd.DataFrame:
        return parse_deals(self._get(ARCHIVE_FILES["block_deals"], kind="text", ttl=3600))


def quote_age_minutes(q: NSEQuote, at: datetime | None = None) -> float | None:
    if q.last_update is None:
        return None
    return ((at or now_ist()) - q.last_update).total_seconds() / 60


_client: NSEWebClient | None = None


def get_client() -> NSEWebClient:
    global _client
    if _client is None:
        _client = NSEWebClient()
    return _client
