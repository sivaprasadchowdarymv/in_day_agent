"""Market-wide extras from free NSE sources: real breadth, India VIX, sector
index changes, holiday calendar, market status, circuit hitters, bulk/block
deals, index constituents. Every function returns (value, warnings) and never
fabricates: on failure the value is None/empty and the reason is reported."""
from __future__ import annotations

import json

import pandas as pd

from brokers.nse_web import NSEWebClient, NSEWebError
from config.logging_setup import get_logger
from config.markets import NSE_HOLIDAYS
from config.settings import STORAGE_DIR

log = get_logger("extras")
HOLIDAY_CACHE = STORAGE_DIR / "nse_holidays.json"

# allIndices names used by NSE for our sector keys (several aliases tolerated)
SECTOR_INDEX_NAMES = {
    "BANK": ["NIFTY BANK"], "IT": ["NIFTY IT"], "AUTO": ["NIFTY AUTO"], "PHARMA": ["NIFTY PHARMA"],
    "FMCG": ["NIFTY FMCG"], "METAL": ["NIFTY METAL"], "REALTY": ["NIFTY REALTY"], "ENERGY": ["NIFTY ENERGY"],
    "INFRA": ["NIFTY INFRASTRUCTURE", "NIFTY INFRA"], "MEDIA": ["NIFTY MEDIA"],
    "FINSERV": ["NIFTY FINANCIAL SERVICES", "NIFTY FIN SERVICE"],
    "CONSUMER": ["NIFTY INDIA CONSUMPTION", "NIFTY CONSUMPTION"],
}


def _row(df: pd.DataFrame, names: list[str]) -> pd.Series | None:
    if df is None or df.empty:
        return None
    up = df["index"].astype(str).str.upper().str.strip()
    for n in names:
        m = df[up == n.upper()]
        if not m.empty:
            return m.iloc[0]
    return None


def market_snapshot(nse: NSEWebClient) -> tuple[dict, list[str]]:
    """{'breadth': {...}, 'vix': float, 'indices': df, 'sector_changes': {key: pct}, 'timestamp': str}"""
    try:
        df = nse.all_indices()
    except NSEWebError as exc:
        return {}, [f"NSE allIndices unavailable: {exc}"]
    out: dict = {"indices": df, "timestamp": df.attrs.get("timestamp")}
    warn: list[str] = []
    r = _row(df, ["NIFTY 500"]) if not df.empty else None
    if r is not None and r["advances"] is not None and r["declines"] is not None:
        out["breadth"] = {"source": "NSE allIndices – NIFTY 500 constituents", "advances": int(r["advances"]),
                          "declines": int(r["declines"]), "unchanged": int(r["unchanged"] or 0)}
    else:
        warn.append("NIFTY 500 advance/decline not present in NSE response")
    v = _row(df, ["INDIA VIX"])
    if v is not None and v["last"]:
        out["vix"] = float(v["last"])
    out["sector_changes"] = {}
    for key, names in SECTOR_INDEX_NAMES.items():
        s = _row(df, names)
        if s is not None and s["change_pct"] is not None:
            out["sector_changes"][key] = float(s["change_pct"])
    n = _row(df, ["NIFTY 50"])
    if n is not None and n["change_pct"] is not None:
        out["nifty_change_pct"] = float(n["change_pct"])
    return out, warn


def refresh_holidays(nse: NSEWebClient | None) -> tuple[int, list[str]]:
    """Load NSE trading holidays into config.markets.NSE_HOLIDAYS (cached on disk)."""
    warn: list[str] = []
    days: list[dict] = []
    if nse is not None:
        try:
            days = nse.holidays()
            if days:
                HOLIDAY_CACHE.write_text(json.dumps(days, indent=1), encoding="utf-8")
        except (NSEWebError, OSError) as exc:
            warn.append(f"NSE holiday list unavailable: {exc}")
    if not days and HOLIDAY_CACHE.exists():
        try:
            days = json.loads(HOLIDAY_CACHE.read_text(encoding="utf-8"))
            warn.append("Using cached NSE holiday list")
        except (OSError, ValueError):
            pass
    for d in days:
        NSE_HOLIDAYS.add(d["date"])
    return len(days), warn


def circuit_hitters(nse: NSEWebClient) -> tuple[dict[str, set[str]], list[str]]:
    try:
        return nse.price_band_hitters(), []
    except NSEWebError as exc:
        return {"upper": set(), "lower": set()}, [f"Price-band hitters unavailable: {exc}"]


def deals_for(nse: NSEWebClient, symbols: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Today's bulk/block deals for the given symbols (informational warnings)."""
    out: dict[str, list[str]] = {}
    warn: list[str] = []
    for kind, fn in (("Bulk", nse.bulk_deals), ("Block", nse.block_deals)):
        try:
            df = fn()
        except (NSEWebError, ValueError) as exc:
            warn.append(f"{kind} deals unavailable: {exc}")
            continue
        sym_col = next((c for c in df.columns if c.lower() == "symbol"), None)
        if sym_col is None:
            continue
        for _, r in df[df[sym_col].astype(str).str.upper().isin(symbols)].iterrows():
            date = r.get("Date", "")
            side = r.get("Buy/Sell", "")
            qty = r.get("Quantity Traded", "")
            client = str(r.get("Client Name", ""))[:40]
            out.setdefault(str(r[sym_col]).upper(), []).append(f"{kind} deal {date}: {client} {side} {qty}")
    return out, warn
