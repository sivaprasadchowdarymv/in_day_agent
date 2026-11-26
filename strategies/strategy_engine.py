"""Strategy registry and evaluation helpers."""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyContext
from strategies.breakout import BreakdownBearishMomentum, RelativeVolumeBreakout, SupportResistanceBreakout
from strategies.ema import EMATrendContinuation
from strategies.momentum import MomentumContinuation
from strategies.orb import OpeningRangeBreakout
from strategies.pullback import PullbackEntry
from strategies.vwap import VWAPBreakoutRetest

STRATEGY_CLASSES: list[type[Strategy]] = [
    VWAPBreakoutRetest, OpeningRangeBreakout, EMATrendContinuation, RelativeVolumeBreakout,
    SupportResistanceBreakout, MomentumContinuation, PullbackEntry, BreakdownBearishMomentum,
]


def all_strategies(enabled: list[str] | None = None, params: dict | None = None) -> list[Strategy]:
    params = params or {}
    out = []
    for cls in STRATEGY_CLASSES:
        if enabled is None or cls.key in enabled:
            out.append(cls(**params.get(cls.key, {})))
    return out


def get_strategy(key: str, **params) -> Strategy:
    for cls in STRATEGY_CLASSES:
        if cls.key == key:
            return cls(**params)
    raise KeyError(key)


def latest_signals(ind: pd.DataFrame, strategies: list[Strategy], ctx: StrategyContext,
                   search_bars: int = 3) -> list[Signal]:
    """Signals generated on any of the last `search_bars` COMPLETED candles that
    are still within their validity window. Most recent signal per strategy."""
    out = []
    n = len(ind)
    if n == 0:
        return out
    for s in strategies:
        prepared = s.prepare(ind)
        for i in range(n - 1, max(n - 1 - search_bars, -1), -1):
            sig = s.evaluate(prepared, i, ctx)
            if sig is not None:
                if (n - 1) - i <= sig.valid_bars:
                    out.append(sig)
                break
    return out
