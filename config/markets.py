"""Static market reference data: index tickers, sector map, session times.

Yahoo Finance tickers for NSE indices. If Yahoo renames a ticker the data layer
reports it as unavailable rather than substituting anything.
"""
from __future__ import annotations

from datetime import time

IST = "Asia/Kolkata"
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)

BENCHMARKS = {
    "NIFTY 50": "^NSEI",
    "BANK NIFTY": "^NSEBANK",
}
VOLATILITY_INDEX = {"INDIA VIX": "^INDIAVIX"}

# sector key -> (display name, Yahoo ticker)
SECTOR_INDICES = {
    "BANK": ("NIFTY BANK", "^NSEBANK"),
    "IT": ("NIFTY IT", "^CNXIT"),
    "AUTO": ("NIFTY AUTO", "^CNXAUTO"),
    "PHARMA": ("NIFTY PHARMA", "^CNXPHARMA"),
    "FMCG": ("NIFTY FMCG", "^CNXFMCG"),
    "METAL": ("NIFTY METAL", "^CNXMETAL"),
    "REALTY": ("NIFTY REALTY", "^CNXREALTY"),
    "ENERGY": ("NIFTY ENERGY", "^CNXENERGY"),
    "INFRA": ("NIFTY INFRA", "^CNXINFRA"),
    "MEDIA": ("NIFTY MEDIA", "^CNXMEDIA"),
    "FINSERV": ("NIFTY FIN SERVICE", "NIFTY_FIN_SERVICE.NS"),
    "CONSUMER": ("NIFTY CONSUMPTION", "^CNXCONSUM"),
}

# NSE trading holidays: maintain this list yearly from the official NSE circular.
# Empty entries mean the app only knows weekends; it will still detect "no fresh
# data" through staleness checks and never fabricate candles.
NSE_HOLIDAYS: set[str] = set()

# Words that identify non-equity / unsuitable instruments in the NSE master
EXCLUDE_NAME_KEYWORDS = (
    "ETF", "BEES", "LIQUID", "GILT", "BOND", "NIFTY", "SENSEX", "GOLD", "SILVER",
    "INAV", "INVIT", "REIT", "TRUST", "FUND",
)
ALLOWED_SERIES = {"EQ", "BE"}  # BE = trade-for-trade; excluded from intraday later
INTRADAY_SERIES = {"EQ"}
