"""Market regime engine: classifies the session from NIFTY 50 / BANK NIFTY
intraday + daily structure and volatility. Deterministic and explainable."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from brokers.base import DataLabel, DataProviderError, MarketDataProvider
from config.logging_setup import get_logger
from config.markets import BENCHMARKS, VOLATILITY_INDEX
from indicators.compute import compute_all
from indicators.trend import ema

log = get_logger("regime")

# Liquid ETFs used as *volume proxies* because Yahoo index candles carry no volume.
VWAP_PROXIES = {"^NSEI": "NIFTYBEES", "^NSEBANK": "BANKBEES"}


class Regime(str, Enum):
    STRONG_BULLISH = "Strong bullish"
    MILD_BULLISH = "Mild bullish"
    SIDEWAYS = "Sideways"
    MILD_BEARISH = "Mild bearish"
    STRONG_BEARISH = "Strong bearish"
    HIGH_VOLATILITY = "High volatility / uncertain"
    UNKNOWN = "Unknown (insufficient data)"


BULLISH = {Regime.STRONG_BULLISH, Regime.MILD_BULLISH}
BEARISH = {Regime.STRONG_BEARISH, Regime.MILD_BEARISH}


@dataclass
class IndexState:
    name: str
    ticker: str
    ltp: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    gap_pct: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    pdh: float | None = None
    pdl: float | None = None
    vwap_relation: str | None = None  # "above" | "below" | None
    vwap_source: str | None = None
    ema_trend: str | None = None
    supertrend: str | None = None
    adx: float | None = None
    daily_trend: str | None = None
    range_vs_atr: float | None = None
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)
    label: str = DataLabel.UNAVAILABLE.value
    last_ts: str | None = None


@dataclass
class RegimeResult:
    regime: Regime
    score: float
    indices: list[IndexState]
    vix: float | None
    evidence: list[str]
    warnings: list[str]
    data_label: str
    market_breadth: dict | None = None  # real NSE breadth (NIFTY 500) when available
    sector_changes_live: dict | None = None
    vix_source: str | None = None
    nifty_change_live: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["regime"] = self.regime.value
        return d


def _sign(x: float, thr: float) -> int:
    return 1 if x > thr else (-1 if x < -thr else 0)


def analyse_index(name: str, ticker: str, intraday: pd.DataFrame, daily: pd.DataFrame,
                  proxy_intraday: pd.DataFrame | None = None, label: str = "DELAYED") -> IndexState:
    st = IndexState(name=name, ticker=ticker, label=label)
    if intraday is None or intraday.empty or daily is None or len(daily) < 25:
        st.evidence.append("Insufficient data")
        return st
    ind = compute_all(intraday, "5m")
    last_day = ind.index[-1].date()
    today = ind[ind.index.date == last_day]
    row = ind.iloc[-1]
    st.last_ts = str(ind.index[-1])
    st.ltp = float(row["close"])
    d_hist = daily[daily.index.date < last_day]
    st.prev_close = float(d_hist["close"].iloc[-1]) if not d_hist.empty else None
    st.pdh = float(row["pdh"]) if pd.notna(row["pdh"]) else None
    st.pdl = float(row["pdl"]) if pd.notna(row["pdl"]) else None
    st.day_open = float(today["open"].iloc[0])
    st.day_high = float(today["high"].max())
    st.day_low = float(today["low"].min())
    pts: list[int] = []
    if st.prev_close:
        st.change_pct = (st.ltp / st.prev_close - 1) * 100
        st.gap_pct = (st.day_open / st.prev_close - 1) * 100
        g = _sign(st.gap_pct, 0.5)
        pts.append(g)
        st.evidence.append(f"Opening gap {st.gap_pct:+.2f}%")
    from_open = (st.ltp / st.day_open - 1) * 100
    pts.append(_sign(from_open, 0.4))
    st.evidence.append(f"Move from open {from_open:+.2f}%")
    # VWAP via ETF proxy
    if proxy_intraday is not None and not proxy_intraday.empty:
        pind = compute_all(proxy_intraday, "5m")
        pr = pind.iloc[-1]
        if pd.notna(pr["vwap"]):
            st.vwap_relation = "above" if pr["close"] >= pr["vwap"] else "below"
            st.vwap_source = "ETF proxy (" + VWAP_PROXIES.get(ticker, "?") + ")"
            pts.append(1 if st.vwap_relation == "above" else -1)
            st.evidence.append(f"Price {st.vwap_relation} session VWAP ({st.vwap_source})")
    if st.vwap_relation is None:
        st.evidence.append("Index VWAP unavailable (no volume in source) – not used")
    if st.pdh and st.ltp > st.pdh:
        pts.append(1)
        st.evidence.append("Trading above previous-day high")
    elif st.pdl and st.ltp < st.pdl:
        pts.append(-1)
        st.evidence.append("Trading below previous-day low")
    else:
        pts.append(0)
        st.evidence.append("Inside previous-day range")
    if pd.notna(row["ema9"]) and pd.notna(row["ema21"]):
        st.ema_trend = "up" if row["ema9"] > row["ema21"] else "down"
        pts.append(1 if st.ema_trend == "up" else -1)
        st.evidence.append(f"5m EMA9 {'>' if st.ema_trend == 'up' else '<'} EMA21")
    if row["supertrend_dir"] != 0:
        st.supertrend = "up" if row["supertrend_dir"] > 0 else "down"
        pts.append(1 if st.supertrend == "up" else -1)
    st.adx = float(row["adx"]) if pd.notna(row["adx"]) else None
    dc = d_hist["close"] if not d_hist.empty else daily["close"]
    e20 = ema(dc, 20)
    if len(e20.dropna()) >= 2:
        up = dc.iloc[-1] > e20.iloc[-1] and e20.iloc[-1] > e20.iloc[-5]
        dn = dc.iloc[-1] < e20.iloc[-1] and e20.iloc[-1] < e20.iloc[-5]
        st.daily_trend = "up" if up else ("down" if dn else "flat")
        pts.append(1 if up else (-1 if dn else 0))
        st.evidence.append(f"Daily trend vs EMA20: {st.daily_trend}")
    tr = pd.concat([d_hist["high"] - d_hist["low"]], axis=1).max(axis=1).tail(14).mean() if not d_hist.empty else np.nan
    if pd.notna(tr) and tr > 0:
        st.range_vs_atr = (st.day_high - st.day_low) / tr
    raw = float(np.mean(pts)) if pts else 0.0
    if st.adx is not None and st.adx < 18:
        raw *= 0.6
        st.evidence.append(f"Low ADX ({st.adx:.0f}) – trend strength weak")
    st.score = raw
    return st


def classify(indices: list[IndexState], vix: float | None, vix_high: float = 20.0,
             breadth: dict | None = None) -> tuple[Regime, float, list[str]]:
    usable = [s for s in indices if s.ltp is not None]
    if not usable:
        return Regime.UNKNOWN, 0.0, ["No index data available"]
    weights = {"^NSEI": 1.0, "^NSEBANK": 0.6}
    score = sum(s.score * weights.get(s.ticker, 0.5) for s in usable) / sum(weights.get(s.ticker, 0.5) for s in usable)
    ev = [f"Composite index score {score:+.2f} (−1 bearish … +1 bullish)"]
    if breadth and breadth.get("declines") is not None:
        adv, dec = breadth["advances"], max(breadth["declines"], 1)
        ratio = adv / dec
        adj = 0.15 if ratio >= 1.5 else (-0.15 if ratio <= 0.67 else 0.0)
        score = max(-1.0, min(1.0, score + adj))
        ev.append(f"Market breadth A/D {adv}/{breadth['declines']} (ratio {ratio:.2f}, {breadth['source']})"
                  + (f" → score {adj:+.2f}" if adj else ""))
    wild = any(s.range_vs_atr and s.range_vs_atr > 1.8 for s in usable) or any(
        s.gap_pct is not None and abs(s.gap_pct) > 1.5 for s in usable)
    if vix is not None:
        ev.append(f"India VIX {vix:.2f}")
    if (vix is not None and vix >= vix_high) or wild:
        ev.append("Volatility elevated (VIX high, abnormal gap, or range > 1.8× daily ATR)")
        if abs(score) < 0.75:
            return Regime.HIGH_VOLATILITY, score, ev
    if score >= 0.6:
        r = Regime.STRONG_BULLISH
    elif score >= 0.25:
        r = Regime.MILD_BULLISH
    elif score > -0.25:
        r = Regime.SIDEWAYS
    elif score > -0.6:
        r = Regime.MILD_BEARISH
    else:
        r = Regime.STRONG_BEARISH
    return r, score, ev


def compute_regime(provider: MarketDataProvider, interval: str = "5m") -> RegimeResult:
    states, warnings, labels = [], [], set()
    for name, ticker in BENCHMARKS.items():
        try:
            intra = provider.get_candles(ticker, interval, 6, is_index=True)
            daily = provider.get_candles(ticker, "1d", 90, is_index=True)
            proxy = None
            if ticker in VWAP_PROXIES:
                try:
                    proxy = provider.get_candles(VWAP_PROXIES[ticker], interval, 3).df
                except DataProviderError as exc:
                    warnings.append(f"VWAP proxy for {name} unavailable: {exc}")
            labels.add(intra.label.value)
            warnings.extend(intra.warnings)
            states.append(analyse_index(name, ticker, intra.df, daily.df, proxy, intra.label.value))
        except DataProviderError as exc:
            warnings.append(f"{name}: {exc}")
            states.append(IndexState(name=name, ticker=ticker, evidence=[f"Data error: {exc}"]))
    vix, vix_src, breadth, sector_live, nifty_live = None, None, None, None, None
    nse = getattr(provider, "nse", None)
    if nse is not None:
        from data.market_extras import market_snapshot

        snap, w = market_snapshot(nse)
        warnings.extend(w)
        if snap:
            breadth = snap.get("breadth")
            sector_live = snap.get("sector_changes") or None
            nifty_live = snap.get("nifty_change_pct")
            if snap.get("vix") is not None:
                vix, vix_src = snap["vix"], "NSE allIndices"
    if vix is None:
        try:
            vcs = provider.get_candles(VOLATILITY_INDEX["INDIA VIX"], "1d", 10, is_index=True, include_provisional=True)
            if not vcs.empty:
                vix, vix_src = float(vcs.df["close"].iloc[-1]), provider.name
        except DataProviderError as exc:
            warnings.append(f"India VIX unavailable: {exc}")
    regime, score, ev = classify(states, vix, breadth=breadth)
    label = "/".join(sorted(labels)) or DataLabel.UNAVAILABLE.value
    return RegimeResult(regime, score, states, vix, ev, warnings, label, breadth, sector_live, vix_src, nifty_live)


def direction_alignment(regime: Regime, direction: str) -> float:
    """0..1 alignment of a trade direction with the regime."""
    long = direction == "LONG"
    table = {
        Regime.STRONG_BULLISH: (1.0, 0.0), Regime.MILD_BULLISH: (0.8, 0.25),
        Regime.SIDEWAYS: (0.5, 0.5), Regime.MILD_BEARISH: (0.25, 0.8),
        Regime.STRONG_BEARISH: (0.0, 1.0), Regime.HIGH_VOLATILITY: (0.2, 0.2), Regime.UNKNOWN: (0.3, 0.3),
    }
    lv, sv = table[regime]
    return lv if long else sv
