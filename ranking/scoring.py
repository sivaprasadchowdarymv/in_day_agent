"""Transparent 0–100 candidate score. Each component is 0..1 with written
evidence; weights are configurable. The score ranks setup QUALITY – it is not
a probability of profit."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config.settings import ScoringWeights
from strategies.base import valid


@dataclass
class Component:
    name: str
    value: float  # 0..1
    weight: float  # points available
    evidence: list[str] = field(default_factory=list)

    @property
    def points(self) -> float:
        return round(self.value * self.weight, 2)


@dataclass
class ScoreCard:
    total: float
    components: list[Component]

    def to_dict(self) -> dict:
        return {"total": self.total, "components": {c.name: {"points": c.points, "max": round(c.weight, 1),
                                                              "evidence": c.evidence} for c in self.components}}


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def trend_component(r: pd.Series, long: bool) -> tuple[float, list[str]]:
    s, ev = 0.0, []
    sg = 1 if long else -1
    if valid(r["ema21"]) and (r["close"] - r["ema21"]) * sg > 0:
        s += 0.25; ev.append(f"Close {'above' if long else 'below'} EMA21")
    if valid(r["ema9"], r["ema21"]) and (r["ema9"] - r["ema21"]) * sg > 0:
        s += 0.25; ev.append("EMA9/EMA21 aligned")
    if valid(r["ema21"], r["ema50"]) and (r["ema21"] - r["ema50"]) * sg > 0:
        s += 0.2; ev.append("EMA21/EMA50 aligned")
    if r["supertrend_dir"] * sg > 0:
        s += 0.15; ev.append("Supertrend aligned")
    if valid(r["adx"]) and r["adx"] >= 20 and ((r["plus_di"] > r["minus_di"]) == long):
        s += 0.15; ev.append(f"ADX {r['adx']:.0f} with DI aligned")
    if not ev:
        ev.append("No trend alignment")
    return _clip(s), ev


def momentum_component(r: pd.Series, prev: pd.Series, long: bool) -> tuple[float, list[str]]:
    s, ev = 0.0, []
    rsi = r["rsi14"]
    if valid(rsi):
        if long:
            v = 1.0 if 55 <= rsi <= 72 else 0.6 if 50 <= rsi < 55 or 72 < rsi <= 78 else 0.25 if rsi > 78 else 0.0
        else:
            v = 1.0 if 28 <= rsi <= 45 else 0.6 if 45 < rsi <= 50 or 22 <= rsi < 28 else 0.25 if rsi < 22 else 0.0
        s += 0.4 * v; ev.append(f"RSI {rsi:.1f}" + (" (stretched)" if v == 0.25 else ""))
    if valid(r["macd_hist"]):
        sg = 1 if long else -1
        if r["macd_hist"] * sg > 0:
            s += 0.25; ev.append("MACD histogram aligned")
        if valid(prev["macd_hist"]) and (r["macd_hist"] - prev["macd_hist"]) * sg > 0:
            s += 0.15; ev.append("MACD histogram expanding")
    if valid(r["roc10"]) and r["roc10"] * (1 if long else -1) > 0:
        s += 0.2; ev.append(f"ROC10 {r['roc10']:+.2f}%")
    return _clip(s), ev or ["No momentum confirmation"]


def vwap_component(r: pd.Series, long: bool) -> tuple[float, list[str]]:
    if not valid(r["vwap"], r["atr14"]) or r["atr14"] <= 0:
        return 0.4, ["VWAP unavailable – neutral-low score"]
    d = (r["close"] - r["vwap"]) / r["atr14"] * (1 if long else -1)
    if d < 0:
        return 0.0, [f"Price on wrong side of VWAP ({d:+.2f} ATR)"]
    if d <= 1.5:
        return 1.0, [f"Price {d:.2f} ATR beyond VWAP (healthy)"]
    if d <= 2.5:
        return 0.7, [f"Price {d:.2f} ATR beyond VWAP (extended)"]
    return 0.35, [f"Price {d:.2f} ATR beyond VWAP (over-extended)"]


def rvol_component(r: pd.Series) -> tuple[float, list[str]]:
    v = r["rvol_tod"] if valid(r["rvol_tod"]) else (r["rvol_bar"] if valid(r["rvol_bar"]) else None)
    if v is None:
        return 0.3, ["Relative volume unavailable"]
    val = 1.0 if v >= 2 else 0.8 if v >= 1.5 else 0.55 if v >= 1.0 else 0.25 if v >= 0.7 else 0.0
    return val, [f"Relative volume {v:.2f}×"]


def price_action_component(r: pd.Series, setup_type: str, long: bool, provisional: bool) -> tuple[float, list[str]]:
    s, ev = 0.0, []
    if not provisional:
        s += 0.5; ev.append(f"{setup_type.title()} confirmed on a completed candle")
    pats_long = [("bull_engulf", "bullish engulfing"), ("hammer", "hammer"), ("strong_bull", "strong bullish candle")]
    pats_short = [("bear_engulf", "bearish engulfing"), ("shooting_star", "shooting star"),
                  ("strong_bear", "strong bearish candle")]
    for k, label in (pats_long if long else pats_short):
        if bool(r.get(k, False)):
            s += 0.25; ev.append(f"Candle pattern: {label}"); break
    if valid(r.get("day_high"), r.get("day_low")) and r["day_high"] > r["day_low"]:
        pos = (r["close"] - r["day_low"]) / (r["day_high"] - r["day_low"])
        pos = pos if long else 1 - pos
        s += 0.25 * pos; ev.append(f"Close in {'upper' if pos > 0.5 else 'lower'} part of the day's range ({pos:.0%})")
    return _clip(s), ev


def liquidity_component(avg_value_cr: float | None, spread_pct: float | None, spread_is_estimate: bool) -> tuple[float, list[str]]:
    if avg_value_cr is None:
        return 0.3, ["Liquidity unknown"]
    v = 1.0 if avg_value_cr >= 100 else 0.8 if avg_value_cr >= 25 else 0.6 if avg_value_cr >= 10 else 0.2
    ev = [f"Avg traded value ₹{avg_value_cr:,.0f} Cr/day"]
    if spread_pct is not None and not spread_is_estimate:
        if spread_pct > 0.1:
            v *= 0.6
        ev.append(f"Live spread {spread_pct:.3f}%")
    else:
        ev.append("Spread not available from data source (not scored)")
    return v, ev


def score_candidate(ind_row: pd.Series, prev_row: pd.Series, direction: str, setup_type: str, provisional: bool,
                    regime_align: float, regime_note: str, sector_align: float, sector_note: str,
                    avg_value_cr: float | None, spread_pct: float | None, spread_is_estimate: bool,
                    weights: ScoringWeights) -> ScoreCard:
    w = weights.normalised()
    long = direction == "LONG"
    comps = []
    v, e = trend_component(ind_row, long); comps.append(Component("Trend alignment", v, w["trend"], e))
    v, e = momentum_component(ind_row, prev_row, long); comps.append(Component("Momentum confirmation", v, w["momentum"], e))
    v, e = vwap_component(ind_row, long); comps.append(Component("VWAP position", v, w["vwap"], e))
    v, e = rvol_component(ind_row); comps.append(Component("Relative volume", v, w["relative_volume"], e))
    v, e = price_action_component(ind_row, setup_type, long, provisional)
    comps.append(Component("Breakout / price action", v, w["price_action"], e))
    ms = 0.6 * regime_align + 0.4 * sector_align
    comps.append(Component("Market & sector alignment", ms, w["market_sector"], [regime_note, sector_note]))
    v, e = liquidity_component(avg_value_cr, spread_pct, spread_is_estimate)
    comps.append(Component("Liquidity & execution", v, w["liquidity"], e))
    total = float(round(sum(c.points for c in comps), 1))
    return ScoreCard(total, comps)
