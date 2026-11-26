"""Order/position simulation shared by the backtester and paper trading.

Conservative fill assumptions (documented in docs/BACKTESTING.md):
* Entry is a stop-limit order: fills at the trigger (or the open if the bar
  gaps past the trigger but stays within the limit). A gap beyond the limit
  cancels the order (no chasing).
* Adverse slippage is applied to entry and stop exits; target exits are limit
  orders (no slippage).
* If a bar touches both stop and target, the STOP is assumed to fill first.
* On the entry bar only the stop is checked (intrabar order unknown).
* Gap through the stop exits at the bar's open (worse than the stop).
* Optional partial exit at T1 (default 50%) then stop moves to breakeven.
* Positions are squared off at the square-off time or the session's last bar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from config.settings import CostConfig
from risk.transaction_costs import brokerage_per_order


@dataclass
class SimOrder:
    symbol: str
    strategy: str
    direction: str
    trigger: float
    limit: float
    stop: float
    target1: float
    target2: float | None
    qty: int
    created_index: int
    expires_after_bars: int
    signal_ts: pd.Timestamp | None = None
    sector: str = "UNKNOWN"


@dataclass
class SimPosition:
    order: SimOrder
    entry_price: float
    entry_ts: pd.Timestamp
    entry_index: int
    open_qty: int
    stop: float
    exits: list[tuple[float, int, str, pd.Timestamp]] = field(default_factory=list)
    t1_done: bool = False

    @property
    def closed(self) -> bool:
        return self.open_qty <= 0


def _slip(price: float, bps: float) -> float:
    return price * bps / 10_000.0


def try_fill(order: SimOrder, bar: pd.Series, idx: int, ts: pd.Timestamp, slip_bps: float) -> tuple[str, SimPosition | None]:
    """Returns (status, position). status: 'filled' | 'pending' | 'missed' | 'expired'."""
    if idx - order.created_index > order.expires_after_bars:
        return "expired", None
    long = order.direction == "LONG"
    o, h, l = bar["open"], bar["high"], bar["low"]
    if long:
        if o > order.limit:
            return "missed", None
        if o >= order.trigger:
            px = o
        elif h >= order.trigger:
            px = order.trigger
        else:
            return "pending", None
        px = min(px + _slip(px, slip_bps), max(order.limit, px))
    else:
        if o < order.limit:
            return "missed", None
        if o <= order.trigger:
            px = o
        elif l <= order.trigger:
            px = order.trigger
        else:
            return "pending", None
        px = max(px - _slip(px, slip_bps), min(order.limit, px))
    pos = SimPosition(order, round(px, 2), ts, idx, order.qty, order.stop)
    # entry bar: only stop is checked
    if (long and l <= pos.stop) or ((not long) and h >= pos.stop):
        exit_px = pos.stop - _slip(pos.stop, slip_bps) if long else pos.stop + _slip(pos.stop, slip_bps)
        pos.exits.append((round(exit_px, 2), pos.open_qty, "STOP (entry bar)", ts))
        pos.open_qty = 0
    return "filled", pos


def manage_bar(pos: SimPosition, bar: pd.Series, ts: pd.Timestamp, slip_bps: float, partial_fraction: float = 0.5,
               square_off: bool = False) -> list[str]:
    """Process one bar for an open position. Returns event descriptions."""
    if pos.closed:
        return []
    ev: list[str] = []
    o = pos.order
    long = o.direction == "LONG"
    s = 1 if long else -1
    op, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

    def close(px, qty, reason):
        pos.exits.append((round(px, 2), qty, reason, ts))
        pos.open_qty -= qty
        ev.append(f"{reason} {qty} @ {px:.2f}")

    # gap through stop
    if (long and op <= pos.stop) or ((not long) and op >= pos.stop):
        close(op - s * _slip(op, slip_bps), pos.open_qty, "STOP (gap)")
        return ev
    stop_hit = (long and l <= pos.stop) or ((not long) and h >= pos.stop)
    if stop_hit:
        reason = "BREAKEVEN STOP" if pos.t1_done and abs(pos.stop - pos.entry_price) < 1e-6 else "STOP"
        close(pos.stop - s * _slip(pos.stop, slip_bps), pos.open_qty, reason)
        return ev
    if not pos.t1_done:
        hit_t1 = (long and h >= o.target1) or ((not long) and l <= o.target1)
        if hit_t1:
            fill = max(op, o.target1) if long else min(op, o.target1)
            if o.target2 is None or pos.open_qty < 2:
                close(fill, pos.open_qty, "TARGET1")
                return ev
            q = max(1, math.floor(pos.open_qty * partial_fraction))
            close(fill, q, "TARGET1 (partial)")
            pos.t1_done = True
            pos.stop = pos.entry_price
            ev.append(f"Stop moved to breakeven {pos.entry_price:.2f}")
    if pos.t1_done and o.target2 is not None and not pos.closed:
        if (long and h >= o.target2) or ((not long) and l <= o.target2):
            fill = max(op, o.target2) if long else min(op, o.target2)
            close(fill, pos.open_qty, "TARGET2")
            return ev
    if square_off and not pos.closed:
        close(c - s * _slip(c, slip_bps), pos.open_qty, "SQUARE-OFF")
    return ev


def position_costs(pos: SimPosition, costs: CostConfig) -> float:
    """Statutory charges + brokerage for all legs (slippage already in prices)."""
    long = pos.order.direction == "LONG"
    entry_val = pos.entry_price * pos.order.qty
    total = brokerage_per_order(entry_val, costs)
    buy_val = entry_val if long else 0.0
    sell_val = 0.0 if long else entry_val
    exch_base = entry_val
    for px, q, _, _ in pos.exits:
        v = px * q
        total += brokerage_per_order(v, costs)
        exch_base += v
        if long:
            sell_val += v
        else:
            buy_val += v
    brokerage = total
    stt = sell_val * costs.stt_sell_pct / 100
    stamp = buy_val * costs.stamp_buy_pct / 100
    exch = exch_base * costs.exchange_txn_pct / 100
    sebi = exch_base * costs.sebi_pct / 100
    gst = (brokerage + exch + sebi) * costs.gst_pct / 100
    return brokerage + stt + stamp + exch + sebi + gst


def position_pnl(pos: SimPosition) -> float:
    s = 1 if pos.order.direction == "LONG" else -1
    return sum((px - pos.entry_price) * q * s for px, q, _, _ in pos.exits)
