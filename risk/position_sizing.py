"""Position sizing for a small (< ₹50,000) cash account. No leverage."""
from __future__ import annotations

import math
from dataclasses import dataclass

from config.settings import TradingProfile


@dataclass
class SizeResult:
    quantity: int
    risk_budget: float
    risk_per_share: float
    risk_based_qty: int
    capital_based_qty: int
    available_allocation: float
    capital_required: float
    max_planned_loss: float
    limiting_factor: str
    note: str = ""


def size_position(entry: float, stop: float, profile: TradingProfile,
                  available_allocation: float | None = None) -> SizeResult:
    """Risk Budget = Capital × Risk% / 100
    Risk per Share = |Entry − Stop|
    Risk-Based Qty = floor(Risk Budget / Risk per Share)
    Capital-Based Qty = floor(Available Allocation / Entry)
    Final Qty = min(both)."""
    if entry <= 0 or stop <= 0:
        raise ValueError("Entry and stop must be positive")
    risk_budget = profile.capital * profile.risk_per_trade_pct / 100.0
    rps = abs(entry - stop)
    alloc = profile.max_allocation if available_allocation is None else min(available_allocation, profile.max_allocation)
    alloc = max(alloc, 0.0)
    if rps <= 0:
        return SizeResult(0, risk_budget, rps, 0, 0, alloc, 0.0, 0.0, "invalid stop", "Stop equals entry")
    rq = math.floor(risk_budget / rps + 1e-9)
    cq = math.floor(alloc / entry + 1e-9)
    q = max(min(rq, cq), 0)
    limiting = "risk budget" if rq <= cq else "capital allocation"
    note = ""
    if q == 0:
        note = ("Stop distance too wide for the risk budget" if rq == 0 else
                "Share price exceeds the per-trade capital allocation")
    return SizeResult(q, risk_budget, rps, rq, cq, alloc, q * entry, q * rps, limiting, note)
