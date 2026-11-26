"""Instrument discovery: universe loading, filtering, watchlists, liquidity."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.markets import EXCLUDE_NAME_KEYWORDS, INTRADAY_SERIES
from config.settings import PROJECT_ROOT, ScanConfig

SEED_PATH = PROJECT_ROOT / "config" / "universe_seed.csv"
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


@dataclass
class Instrument:
    symbol: str
    name: str
    sector: str = "UNKNOWN"
    series: str = "EQ"
    isin: str | None = None
    source: str = "seed"


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().upper().replace(" ", "_") for c in df.columns]
    return df


def is_valid_symbol(sym: str) -> bool:
    return bool(_SYMBOL_RE.match(sym or ""))


def load_seed_universe() -> list[Instrument]:
    df = _norm_cols(pd.read_csv(SEED_PATH))
    return [Instrument(r.SYMBOL.strip().upper(), r.NAME, r.SECTOR, r.SERIES, source="seed")
            for r in df.itertuples()]


def parse_nse_equity_master(raw: bytes | str, sector_map: dict[str, str] | None = None) -> tuple[list[Instrument], dict]:
    """Parse NSE's EQUITY_L.csv (from nseindia.com > Market Data > Securities
    available for trading). Returns instruments plus a filter report."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    df = _norm_cols(pd.read_csv(io.StringIO(text)))
    required = {"SYMBOL", "NAME_OF_COMPANY", "SERIES"}
    if not required.issubset(df.columns):
        raise ValueError(f"Not an NSE equity master file; expected columns {sorted(required)}")
    report = {"total": len(df)}
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    df["SERIES"] = df["SERIES"].astype(str).str.strip().str.upper()
    df = df[df["SERIES"].isin(INTRADAY_SERIES)]
    report["after_series_filter"] = len(df)
    name_up = df["NAME_OF_COMPANY"].astype(str).str.upper()
    mask = np.ones(len(df), dtype=bool)
    for kw in EXCLUDE_NAME_KEYWORDS:
        mask &= ~name_up.str.contains(rf"\b{kw}\b", regex=True).to_numpy()
    df = df[mask]
    report["after_name_filter"] = len(df)
    df = df[df["SYMBOL"].map(is_valid_symbol)]
    df = df.drop_duplicates("SYMBOL")
    report["final"] = len(df)
    sector_map = sector_map or {i.symbol: i.sector for i in load_seed_universe()}
    isin_col = "ISIN_NUMBER" if "ISIN_NUMBER" in df.columns else None
    out = [Instrument(r["SYMBOL"], str(r["NAME_OF_COMPANY"]).strip(), sector_map.get(r["SYMBOL"], "UNKNOWN"),
                      r["SERIES"], str(r[isin_col]).strip() if isin_col else None, source="nse_master")
           for _, r in df.iterrows()]
    return out, report


def parse_watchlist_csv(raw: bytes | str) -> tuple[list[str], list[str]]:
    """Accept a CSV with a 'symbol' column (or a single column of symbols).
    Returns (valid_symbols, rejected_entries)."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text), header=None, dtype=str)
    first = [str(x).strip().lower() for x in df.iloc[0].tolist()]
    if "symbol" in first:
        col = first.index("symbol")
        values = df.iloc[1:, col].tolist()
    else:
        values = df.iloc[:, 0].tolist()
    valid, rejected = [], []
    for v in values:
        s = str(v).strip().upper().removesuffix(".NS")
        if not s or s == "NAN":
            continue
        (valid if is_valid_symbol(s) else rejected).append(s)
    return list(dict.fromkeys(valid)), rejected


def parse_manual_symbols(text: str) -> tuple[list[str], list[str]]:
    toks = [t.strip().upper().removesuffix(".NS") for t in re.split(r"[,\s;]+", text or "") if t.strip()]
    valid = [t for t in toks if is_valid_symbol(t)]
    return list(dict.fromkeys(valid)), [t for t in toks if not is_valid_symbol(t)]


SECTOR_KEYWORDS = [
    ("BANK", ("bank",)), ("IT", ("information technology", "it -", "software", "it services")),
    ("AUTO", ("automobile", "auto ")), ("PHARMA", ("healthcare", "pharma", "hospital")),
    ("FMCG", ("fast moving consumer", "fmcg")), ("METAL", ("metal", "mining")), ("REALTY", ("realty",)),
    ("ENERGY", ("oil", "gas", "power", "energy", "consumable fuels")),
    ("INFRA", ("capital goods", "construction", "cement", "infrastructure")),
    ("MEDIA", ("telecommunication", "media", "entertainment")),
    ("FINSERV", ("financial services", "insurance", "finance")),
    ("CONSUMER", ("consumer services", "consumer durables", "textiles", "retail")),
]


def map_nse_sector(text: str | None) -> str:
    """Map NSE/NIFTY 'Industry' strings to the app's sector keys (UNKNOWN if no match)."""
    t = f" {(text or '').lower()} "
    for key, words in SECTOR_KEYWORDS:
        if any(w in t for w in words):
            return key
    return "UNKNOWN"


def instruments_from_constituents(df: pd.DataFrame, source: str) -> list[Instrument]:
    seed = {i.symbol: i.sector for i in load_seed_universe()}
    out = []
    for _, r in df.iterrows():
        sym = str(r["Symbol"]).strip().upper()
        if not is_valid_symbol(sym):
            continue
        if "Series" in df.columns and str(r["Series"]).strip().upper() != "EQ":  # BE = trade-for-trade, no intraday
            continue
        sector = seed.get(sym) or map_nse_sector(r.get("Industry"))
        out.append(Instrument(sym, str(r["Company Name"]).strip(), sector, "EQ",
                              str(r.get("ISIN Code", "")).strip() or None, source=source))
    return out


# ---------------------------------------------------------------- liquidity
@dataclass
class LiquidityReport:
    symbol: str
    eligible: bool
    avg_traded_value_cr: float | None = None
    avg_volume: float | None = None
    last_close: float | None = None
    atr_pct: float | None = None
    history_days: int = 0
    est_spread_pct: float | None = None
    spread_is_estimate: bool = True
    circuit_suspected: bool = False
    reasons: list[str] = field(default_factory=list)


def estimate_spread_pct(daily: pd.DataFrame) -> float | None:
    """Corwin–Schultz high/low spread estimator (rough, labelled as estimate)."""
    if len(daily) < 3:
        return None
    h, l = np.log(daily["high"].to_numpy()), np.log(daily["low"].to_numpy())
    beta = (h[1:] - l[1:]) ** 2 + (h[:-1] - l[:-1]) ** 2
    h2 = np.maximum(daily["high"].to_numpy()[1:], daily["high"].to_numpy()[:-1])
    l2 = np.minimum(daily["low"].to_numpy()[1:], daily["low"].to_numpy()[:-1])
    gamma = np.log(h2 / l2) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s = np.clip(s, 0, None)
    return float(np.nanmean(s[-20:]) * 100)


def assess_liquidity(symbol: str, daily: pd.DataFrame, cfg: ScanConfig) -> LiquidityReport:
    rep = LiquidityReport(symbol=symbol, eligible=False, history_days=len(daily))
    if daily is None or daily.empty:
        rep.reasons.append("No daily history")
        return rep
    tail = daily.tail(20)
    rep.avg_traded_value_cr = float((tail["close"] * tail["volume"]).mean() / 1e7)
    rep.avg_volume = float(tail["volume"].mean())
    rep.last_close = float(daily["close"].iloc[-1])
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - daily["close"].shift()).abs(),
                    (daily["low"] - daily["close"].shift()).abs()], axis=1).max(axis=1)
    rep.atr_pct = float(tr.tail(14).mean() / rep.last_close * 100)
    rep.est_spread_pct = estimate_spread_pct(daily)
    last = daily.iloc[-1]
    rep.circuit_suspected = bool(last["high"] == last["low"] and last["volume"] > 0)
    if rep.history_days < cfg.min_history_days:
        rep.reasons.append(f"Only {rep.history_days} days of history (< {cfg.min_history_days})")
    if rep.avg_traded_value_cr < cfg.min_avg_traded_value_cr:
        rep.reasons.append(f"Avg traded value ₹{rep.avg_traded_value_cr:.1f} Cr < ₹{cfg.min_avg_traded_value_cr} Cr")
    if rep.avg_volume < cfg.min_avg_volume:
        rep.reasons.append(f"Avg volume {rep.avg_volume:,.0f} < {cfg.min_avg_volume:,.0f}")
    if not (cfg.min_price <= rep.last_close <= cfg.max_price):
        rep.reasons.append(f"Price ₹{rep.last_close:.2f} outside ₹{cfg.min_price}–₹{cfg.max_price}")
    if not (cfg.min_atr_pct <= rep.atr_pct <= cfg.max_atr_pct):
        rep.reasons.append(f"ATR {rep.atr_pct:.2f}% outside {cfg.min_atr_pct}–{cfg.max_atr_pct}%")
    # High/low spread estimates are too noisy to reject on; the hard spread
    # check happens later only when a real bid/ask quote is available.
    if rep.circuit_suspected:
        rep.reasons.append("Price frozen (high = low): possible circuit / halt")
    rep.eligible = not rep.reasons
    return rep
