"""Strategy framework.

Every strategy is evaluated bar-by-bar with `evaluate(ind, i)` which may only
read rows 0..i of the indicator frame. Indicators are causal, so the same code
path powers both live scanning (i = last completed candle) and backtesting
(i = each historical candle) – there is no separate 'backtest logic' that could
peek ahead.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import time

import pandas as pd

from market.regime import Regime


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    if tick <= 0:
        return price
    q = price / tick
    if mode == "up":
        q = math.ceil(q - 1e-9)
    elif mode == "down":
        q = math.floor(q + 1e-9)
    else:
        q = round(q)
    return round(q * tick, 2)


@dataclass
class Signal:
    symbol: str
    strategy: str
    strategy_name: str
    direction: str  # LONG | SHORT
    setup_type: str  # breakout | pullback | reversal | breakdown
    bar_index: int
    bar_ts: pd.Timestamp
    trigger: float
    structural_stop: float
    reasons: list[str] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)
    key_levels: dict = field(default_factory=dict)
    provisional: bool = False
    valid_bars: int = 3


@dataclass
class StrategyContext:
    symbol: str
    interval: str = "5m"
    tick: float = 0.05
    allow_short: bool = True
    no_new_entries_after: time = time(14, 45)
    first_entry_after: time = time(9, 20)


class Strategy(ABC):
    key: str = "base"
    name: str = "Base"
    setup_type: str = "breakout"
    directions: tuple[str, ...] = ("LONG", "SHORT")
    suitable_regimes: tuple[Regime, ...] = ()
    entry_rules: str = ""
    confirmation_rules: str = ""
    stop_method: str = ""
    target_method: str = "T1 = 1.5R, T2 = 2.5R, T3 = 3.5R (only with ADX > 25), each capped by the nearest opposing structural level."
    invalidation_rules: str = ""
    default_params: dict = {}

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    # ---- hooks
    def prepare(self, ind: pd.DataFrame) -> pd.DataFrame:
        """Add strategy-specific *causal* columns. Called once per frame."""
        return ind

    @abstractmethod
    def evaluate(self, ind: pd.DataFrame, i: int, ctx: StrategyContext) -> Signal | None:
        ...

    # ---- helpers
    @staticmethod
    def _ok_time(ts: pd.Timestamp, ctx: StrategyContext) -> bool:
        t = ts.tz_convert("Asia/Kolkata").time() if ts.tzinfo else ts.time()
        return ctx.first_entry_after <= t <= ctx.no_new_entries_after

    @staticmethod
    def _bar_end_time(ind: pd.DataFrame, i: int) -> pd.Timestamp:
        return ind.index[i]

    def _make(self, ind, i, ctx, direction, trigger_raw, stop_raw, reasons, confirmations, invalidations,
              levels=None) -> Signal:
        tick = ctx.tick
        if direction == "LONG":
            trig = round_to_tick(trigger_raw + tick, tick, "up")
            stop = round_to_tick(stop_raw, tick, "down")
        else:
            trig = round_to_tick(trigger_raw - tick, tick, "down")
            stop = round_to_tick(stop_raw, tick, "up")
        return Signal(symbol=ctx.symbol, strategy=self.key, strategy_name=self.name, direction=direction,
                      setup_type=self.setup_type if direction == "LONG" or self.setup_type != "breakout" else "breakdown",
                      bar_index=i, bar_ts=ind.index[i], trigger=trig, structural_stop=stop, reasons=reasons,
                      confirmations=confirmations, invalidations=invalidations, key_levels=levels or {},
                      valid_bars=self.params.get("valid_bars", 3))

    def describe(self) -> dict:
        return {
            "key": self.key, "name": self.name, "setup_type": self.setup_type, "directions": list(self.directions),
            "entry": self.entry_rules, "confirmation": self.confirmation_rules, "stop_loss": self.stop_method,
            "targets": self.target_method, "invalidation": self.invalidation_rules,
            "suitable_regimes": [r.value for r in self.suitable_regimes], "params": self.params,
        }


def valid(*vals) -> bool:
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) and not pd.isna(v) for v in vals)
