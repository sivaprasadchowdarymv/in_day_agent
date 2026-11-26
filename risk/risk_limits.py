"""Account-level risk guards: daily loss, trade count, consecutive losses,
cooldown, exposure, sector concentration and return correlation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from config.settings import TradingProfile


@dataclass
class DayState:
    realized_pnl: float = 0.0
    trades_taken: int = 0
    consecutive_losses: int = 0
    last_loss_at: datetime | None = None
    open_exposure: float = 0.0
    open_sectors: dict[str, int] = field(default_factory=dict)


@dataclass
class RiskGate:
    allowed: bool
    reasons: list[str]
    remaining_risk_budget: float
    available_allocation: float


def check_account_limits(state: DayState, profile: TradingProfile, now: datetime) -> RiskGate:
    reasons = []
    if state.realized_pnl <= -profile.max_daily_loss:
        reasons.append(f"Daily loss limit hit (₹{state.realized_pnl:,.0f} ≤ −₹{profile.max_daily_loss:,.0f})")
    if state.trades_taken >= profile.max_trades_per_day:
        reasons.append(f"Max trades per day reached ({state.trades_taken}/{profile.max_trades_per_day})")
    if state.consecutive_losses >= profile.max_consecutive_losses:
        if state.last_loss_at and now - state.last_loss_at < timedelta(minutes=profile.cooldown_minutes):
            mins = profile.cooldown_minutes - int((now - state.last_loss_at).total_seconds() // 60)
            reasons.append(f"Cooldown after {state.consecutive_losses} consecutive losses ({mins} min left)")
        elif state.consecutive_losses >= profile.max_consecutive_losses + 1:
            reasons.append("Consecutive-loss protection: stop trading for the day")
    max_exp = profile.capital * profile.max_open_exposure_pct / 100
    if state.open_exposure >= max_exp:
        reasons.append(f"Open exposure ₹{state.open_exposure:,.0f} at limit ₹{max_exp:,.0f}")
    remaining_loss = profile.max_daily_loss + min(state.realized_pnl, 0)
    return RiskGate(not reasons, reasons, max(0.0, remaining_loss),
                    max(0.0, min(profile.max_allocation, max_exp - state.open_exposure)))


def sector_ok(sector: str, state: DayState, chosen_sectors: list[str], profile: TradingProfile) -> bool:
    if sector in (None, "", "UNKNOWN"):
        return True
    used = state.open_sectors.get(sector, 0) + chosen_sectors.count(sector)
    return used < profile.max_positions_per_sector


def return_correlation(a: pd.Series, b: pd.Series, min_obs: int = 15) -> float | None:
    ra, rb = a.pct_change(), b.pct_change()
    j = pd.concat([ra, rb], axis=1, join="inner").dropna()
    if len(j) < min_obs:
        return None
    c = np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1]
    return None if np.isnan(c) else float(c)
