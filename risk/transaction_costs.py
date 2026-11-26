"""Estimated intraday equity costs (all values are ESTIMATES; the contract note
is the only source of actual charges)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from config.settings import CostConfig


@dataclass
class CostBreakdown:
    buy_value: float
    sell_value: float
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    slippage: float
    total_charges: float  # statutory + brokerage (excl. slippage)
    total_with_slippage: float
    is_estimate: bool = True

    def to_dict(self) -> dict:
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(self).items()}


def brokerage_per_order(value: float, c: CostConfig) -> float:
    if value <= 0:
        return 0.0
    b = min(c.brokerage_flat, value * c.brokerage_pct / 100.0)
    if b < c.brokerage_min:
        b = min(c.brokerage_min, value * 0.025)
    return b


def round_trip_costs(buy_price: float, sell_price: float, qty: int, c: CostConfig,
                     half_spread_bps: float | None = None) -> CostBreakdown:
    """Costs for one intraday round trip (works for long or short: one buy, one sell)."""
    bv, sv = buy_price * qty, sell_price * qty
    brokerage = brokerage_per_order(bv, c) + brokerage_per_order(sv, c)
    stt = sv * c.stt_sell_pct / 100.0
    exch = (bv + sv) * c.exchange_txn_pct / 100.0
    sebi = (bv + sv) * c.sebi_pct / 100.0
    stamp = bv * c.stamp_buy_pct / 100.0
    gst = (brokerage + exch + sebi) * c.gst_pct / 100.0
    hs = c.default_half_spread_bps if half_spread_bps is None else half_spread_bps
    slippage = (bv + sv) * (c.slippage_bps_per_side + hs) / 10_000.0
    total = brokerage + stt + exch + sebi + stamp + gst
    return CostBreakdown(bv, sv, brokerage, stt, exch, sebi, stamp, gst, slippage, total, total + slippage)


def per_side_slippage(price: float, c: CostConfig, half_spread_bps: float | None = None) -> float:
    hs = c.default_half_spread_bps if half_spread_bps is None else half_spread_bps
    return price * (c.slippage_bps_per_side + hs) / 10_000.0
