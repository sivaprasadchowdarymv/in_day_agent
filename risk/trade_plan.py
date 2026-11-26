"""Entry / stop-loss / target engine. Deterministic; uses ATR, swings, VWAP,
pivots and S/R. Never chases: a trade beyond its entry range is MISSED."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time

import pandas as pd

from config.settings import CostConfig, TradingProfile
from risk.position_sizing import size_position
from risk.transaction_costs import round_trip_costs
from strategies.base import Signal, round_to_tick, valid


class PlanStatus:
    WAITING = "WAITING_FOR_TRIGGER"
    IN_RANGE = "IN_ENTRY_RANGE"
    MISSED = "MISSED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    TOO_LATE = "TOO_LATE"


@dataclass
class TradePlan:
    symbol: str
    direction: str
    strategy: str
    strategy_name: str
    setup_type: str
    current_price: float | None
    price_label: str
    price_timestamp: str | None
    entry_trigger: float
    entry_limit: float
    stop_loss: float
    target1: float
    target2: float | None
    target3: float | None
    risk_per_share: float
    reward_per_share_t1: float
    risk_reward: float
    net_risk_reward: float | None
    quantity: int
    capital_required: float
    max_planned_loss: float
    est_costs_at_target: float
    est_costs_at_stop: float
    est_net_profit_t1: float
    est_net_loss_at_stop: float
    status: str
    valid: bool
    issues: list[str] = field(default_factory=list)
    target_notes: list[str] = field(default_factory=list)
    stop_notes: list[str] = field(default_factory=list)
    entry_notes: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    limiting_factor: str = ""
    signal_time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _obstacles(row: pd.Series, levels: list[dict], direction: str, entry: float) -> list[tuple[float, str]]:
    """Major opposing levels only: previous-day high/low, today's high/low and
    clustered S/R with at least 2 touches. Pivots and single swings are shown
    for context but do not cap targets (too dense to be meaningful)."""
    cand = []
    for k in ("pdh", "pdl", "day_high", "day_low"):
        v = row.get(k)
        if valid(v):
            cand.append((float(v), k.upper()))
    for lv in levels or []:
        if lv.get("touches", 0) >= 2:
            cand.append((float(lv["price"]), f"S/R({lv['touches']} touches)"))
    if direction == "LONG":
        out = [(p, n) for p, n in cand if p > entry * 1.0005]
        return sorted(out)
    out = [(p, n) for p, n in cand if p < entry * 0.9995]
    return sorted(out, reverse=True)


def build_trade_plan(sig: Signal, ind: pd.DataFrame, profile: TradingProfile, costs: CostConfig,
                     current_price: float | None, price_label: str, price_ts=None,
                     levels: list[dict] | None = None, bars_since_signal: int = 0,
                     now_time: time | None = None, available_allocation: float | None = None) -> TradePlan:
    row = ind.iloc[sig.bar_index]
    atr = float(row["atr14"])
    tick = profile.tick_size
    long = sig.direction == "LONG"
    s = 1 if long else -1
    issues: list[str] = []
    entry_notes, stop_notes, target_notes = [], [], []

    trigger = sig.trigger
    band = max(0.15 * atr, trigger * 0.001)
    limit = round_to_tick(trigger + s * band, tick, "up" if long else "down")
    entry_notes.append(f"Trigger {trigger:.2f} = signal candle {'high' if long else 'low'} ± 1 tick; "
                       f"limit {limit:.2f} = trigger {'+' if long else '−'} max(0.15×ATR, 0.1%) to avoid chasing.")
    if sig.setup_type == "pullback":
        entry_notes.append("Pullback setup: enter only on resumption (break of reversal candle).")
    else:
        entry_notes.append("Breakout setup: entry only after a COMPLETED candle closed beyond the level.")

    stop = sig.structural_stop
    stop_notes.append(f"Structural stop {stop:.2f} from strategy rule.")
    min_risk = 0.3 * atr
    if abs(limit - stop) < min_risk:
        stop = round_to_tick(limit - s * min_risk, tick, "down" if long else "up")
        stop_notes.append(f"Widened to 0.3×ATR ({min_risk:.2f}) from the limit price so normal noise does not stop the trade.")
    if long and stop >= trigger or (not long) and stop <= trigger:
        issues.append("Invalid stop: on the wrong side of entry")
    risk = abs(limit - stop)  # sized on worst acceptable fill (limit price)
    if risk > profile.max_stop_atr * atr:
        issues.append(f"Stop distance {risk:.2f} exceeds {profile.max_stop_atr}×ATR "
                      f"({profile.max_stop_atr * atr:.2f}) – invalid stop")

    # targets: R-multiples capped by structure
    obs = _obstacles(row, levels or [], sig.direction, limit)
    r_mult = profile.min_risk_reward
    t1_raw = limit + s * r_mult * risk
    buffer = 0.05 * atr
    t1 = t1_raw
    blocking = [(p, n) for p, n in obs if (p - limit) * s < r_mult * risk]
    if blocking:
        p, n = blocking[0]
        t1 = p - s * buffer
        target_notes.append(f"T1 capped just before {n} level {p:.2f} (would block a {r_mult}R target).")
    else:
        target_notes.append(f"T1 = {r_mult}R from limit price; no opposing level in the way.")
    t2_raw = limit + s * 2.5 * risk
    between = [(p, n) for p, n in obs if (p - t1) * s > 0 and (p - t2_raw) * s < 0]
    t2 = (between[0][0] - s * buffer) if between else t2_raw
    if between:
        target_notes.append(f"T2 capped before {between[0][1]} {between[0][0]:.2f}.")
    else:
        target_notes.append("T2 = 2.5R.")
    t3 = None
    adx = row.get("adx")
    t3_raw = limit + s * 3.5 * risk
    if valid(adx) and adx > 25 and not [(p, n) for p, n in obs if (p - t2) * s > 0 and (p - t3_raw) * s < 0]:
        t3 = t3_raw
        target_notes.append(f"T3 = 3.5R justified by strong trend (ADX {adx:.0f}) and no level before it.")
    if (t2 - t1) * s <= 0:
        t2 = None
    # R-multiple targets round away from entry (keeps the promised R); targets
    # capped by structure round toward entry (stay in front of the level).
    away, toward = ("up", "down") if long else ("down", "up")
    t1 = round_to_tick(t1, tick, toward if blocking else away)
    t2 = round_to_tick(t2, tick, toward if between else away) if t2 is not None else None
    t3 = round_to_tick(t3, tick, away) if t3 is not None else None

    reward = (t1 - limit) * s
    rr = reward / risk if risk > 0 else 0.0
    if rr < profile.min_risk_reward - 1e-6:
        issues.append(f"Risk/reward {rr:.2f} below minimum {profile.min_risk_reward}")

    size = size_position(limit, stop, profile, available_allocation)
    if size.quantity == 0:
        issues.append(size.note or "Quantity is zero")
    q = size.quantity
    if long:
        c_t = round_trip_costs(limit, t1, max(q, 1), costs)
        c_s = round_trip_costs(limit, stop, max(q, 1), costs)
    else:
        c_t = round_trip_costs(t1, limit, max(q, 1), costs)
        c_s = round_trip_costs(stop, limit, max(q, 1), costs)
    net_profit = reward * q - c_t.total_with_slippage if q else 0.0
    net_loss = risk * q + c_s.total_with_slippage if q else 0.0
    net_rr = (net_profit / net_loss) if q and net_loss > 0 else None
    if q and net_profit <= 0:
        issues.append("Costs consume the entire expected reward at T1")
    elif net_rr is not None and net_rr < profile.min_net_risk_reward:
        issues.append(f"After estimated costs, reward/risk at T1 is only {net_rr:.2f} "
                      f"(< {profile.min_net_risk_reward})")

    # live status relative to current verified price
    status = PlanStatus.WAITING
    if current_price is None:
        issues.append("No verified current price")
    else:
        beyond_limit = (current_price - limit) * s > 0
        through_stop = (current_price - stop) * s <= 0
        triggered = (current_price - trigger) * s >= 0
        if through_stop:
            status = PlanStatus.INVALIDATED
        elif beyond_limit:
            status = PlanStatus.MISSED
        elif triggered:
            status = PlanStatus.IN_RANGE
    if bars_since_signal > sig.valid_bars and status in (PlanStatus.WAITING,):
        status = PlanStatus.EXPIRED
    if now_time is not None:
        h, m = map(int, profile.no_new_entries_after.split(":"))
        if now_time > time(h, m):
            status = PlanStatus.TOO_LATE

    invalidation = list(sig.invalidations) + [
        f"Price trades {'below' if long else 'above'} stop {stop:.2f}",
        f"Not triggered within {sig.valid_bars} candles of the signal",
        f"No entry after {profile.no_new_entries_after} IST; square off by {profile.square_off_time}",
    ]
    return TradePlan(
        symbol=sig.symbol, direction=sig.direction, strategy=sig.strategy, strategy_name=sig.strategy_name,
        setup_type=sig.setup_type, current_price=current_price, price_label=price_label,
        price_timestamp=str(price_ts) if price_ts is not None else None, entry_trigger=trigger, entry_limit=limit,
        stop_loss=stop, target1=t1, target2=t2, target3=t3, risk_per_share=round(risk, 2),
        reward_per_share_t1=round(reward, 2), risk_reward=round(rr, 2),
        net_risk_reward=round(net_rr, 2) if net_rr is not None else None, quantity=q,
        capital_required=round(size.capital_required, 2), max_planned_loss=round(size.max_planned_loss, 2),
        est_costs_at_target=round(c_t.total_with_slippage, 2) if q else 0.0,
        est_costs_at_stop=round(c_s.total_with_slippage, 2) if q else 0.0,
        est_net_profit_t1=round(net_profit, 2), est_net_loss_at_stop=round(net_loss, 2), status=status,
        valid=not issues, issues=issues, target_notes=target_notes, stop_notes=stop_notes,
        entry_notes=entry_notes, invalidation=invalidation, limiting_factor=size.limiting_factor,
        signal_time=str(sig.bar_ts))
