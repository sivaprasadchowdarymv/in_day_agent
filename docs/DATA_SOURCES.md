# Data sources and integration

## Yahoo Finance (default, free)
- **Library:** `yfinance` (unofficial; no API key). NSE stocks use the `.NS` suffix. Indices use `^NSEI` (NIFTY 50), `^NSEBANK` (BANK NIFTY), `^INDIAVIX`, sector indices such as `^CNXIT`, and the ETF proxies NIFTYBEES and BANKBEES for VWAP.
- **Timeliness:** data is **delayed**. The app labels today's candles `DELAYED` and older candles `HISTORICAL`.
- **History limits:**
  - 1m: about 7 days
  - 5m, 15m and 30m: about 60 days
  - 1h: about 730 days
  - 3m: built by resampling 1m candles inside each session
- **Completed candles only:** a candle still forming is dropped unless the caller explicitly asks for provisional data.
- **Resilience:**
  - rate limiting (configurable delay between requests)
  - 3 retries with exponential backoff
  - an in-memory cache (60 s for intraday)
  - an SQLite candle cache with incremental top-up
  - If the upstream fails, cached candles are returned labelled **HISTORICAL** with a warning, so the freshness check rejects them for live use.
- **Gaps:** there is no bid/ask, no market depth and no delivery data. These features are disabled and labelled, never invented.

## NSE public website (default with Yahoo: provider `yahoo_nse`)
`brokers/nse_web.py` reads the JSON and CSV files behind nseindia.com's public pages. It uses a cookie session from the home page, browser-like headers, at least 1 s between requests, a short cache, a cookie refresh on HTTP 401/403, and a 60 s back-off after repeated failures.

| Data | Endpoint / file | Used for |
|---|---|---|
| Quote, circuit band, company/industry | `/api/quote-equity?symbol=X` | Verified LTP, MISSED/INVALIDATED status, near-circuit rejection |
| Order book, delivery % | `/api/quote-equity?symbol=X&section=trade_info` | Real bid/ask → spread rejection; delivery % shown (not scored) |
| All indices | `/api/allIndices` | NIFTY 500 advance/decline (regime), India VIX, live sector changes |
| Market status | `/api/marketStatus` | Settings health check |
| Holidays | `/api/holiday-master?type=trading` | Trading calendar (cached to `storage/nse_holidays.json`) |
| Price-band hitters | `/api/live-analysis-price-band-hitter` | Rejects stocks at upper/lower circuit today |
| Index constituents | `niftyindices.com/IndexConstituent/ind_nifty*list.csv` (NSE archives fallback) | NIFTY 50/100/200/500/Next 50/Midcap 100 universes |
| Equity master | `nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | Full NSE EQ universe |
| Bulk / block deals | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` | Event warnings on candidates |

Composite quote rules (`brokers/composite.py`):

- Candles always come from Yahoo.
- The quote comes from NSE.
  - A timestamp within 3 minutes while the market is open is labelled **LIVE**.
  - A same-day quote older than that is **DELAYED**.
  - An earlier-day quote is **HISTORICAL**.
- Yahoo is queried as a cross-check. If the two previous closes differ by more than 0.5%, a warning is attached.
- If NSE fails, the Yahoo quote is used and the reason is shown on the card.

These endpoints are unofficial. NSE may change or block them, and use is subject to NSE's website terms. For commercial or production use, license data from NSE Data & Analytics or a broker API. **Settings → Test all free data sources** shows what works on your network.

## Simulated (demo)
`brokers/simulated.py` produces deterministic synthetic data for demos and tests:

- It is only used when selected explicitly in Settings.
- Every output is labelled **SIMULATED** and a red banner appears on every page.
- An optional **demo clock** replays a past moment so you can explore outside market hours.

## Instrument universe
- **Seed list:** `config/universe_seed.csv` holds about 70 hand-picked liquid large caps with sector tags. It is not an official index list.
- **Full NSE list:** upload `EQUITY_L.csv` from nseindia.com (Market Data → Securities available for trading). The parser:
  - keeps only the `EQ` series
  - removes ETFs, BeES, liquid, gilt, bond, gold, silver, InvIT, REIT and fund names
  - validates symbols and removes duplicates
- **Watchlists:** a CSV with a `symbol` column, or symbols typed in.
- **Liquidity filter:** uses daily data – 20-day average traded value, average volume, price band, ATR%, history length and a frozen-price check for circuits or halts.
- **Request budget:** scans have a configurable size limit. Partial scans are always labelled.

## Adding a licensed real-time broker feed later
1. Create `brokers/<broker>_client.py` implementing `MarketDataProvider.get_candles()` and `get_quote()`, plus `realtime_label = DataLabel.LIVE` if the feed really is real-time.
2. Follow that broker's official documentation for authentication. Keep secrets in environment variables only.
3. Register the provider in `brokers/factory.py` and add it to the Settings radio button.

Strategies, ranking, risk and the UI need no changes. Order placement is intentionally not part of this release.
