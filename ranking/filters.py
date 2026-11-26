"""Mandatory rejection rules. A candidate failing ANY rule is never recommended."""
from __future__ import annotations

from market.regime import Regime
from risk.trade_plan import PlanStatus, TradePlan


def mandatory_rejections(*, data_fresh: bool, freshness_msg: str, data_label: str, liquidity_ok: bool,
                         liquidity_reasons: list[str], plan: TradePlan, min_rr: float, live_spread_pct: float | None,
                         max_spread_pct: float, provisional: bool, regime: Regime, strategy_regimes: tuple,
                         symbol_verified: bool, circuit_suspected: bool, score: float, min_score: float,
                         allow_short: bool, account_gate_reasons: list[str], near_circuit: str | None = None,
                         circuit_hitter: str | None = None) -> list[str]:
    r: list[str] = []
    if near_circuit:
        r.append(f"Price within 0.5% of the {near_circuit} circuit limit (exchange price band)")
    if circuit_hitter:
        r.append(f"Listed by NSE as an {circuit_hitter} price-band hitter today")
    if data_label in ("UNAVAILABLE",):
        r.append("Market data unavailable")
    if not data_fresh:
        r.append(f"Stale data: {freshness_msg}")
    if not symbol_verified:
        r.append("Symbol not verified with the data provider")
    if not liquidity_ok:
        r.append("Inadequate liquidity: " + "; ".join(liquidity_reasons))
    if circuit_suspected:
        r.append("Possible circuit / trading halt detected")
    if live_spread_pct is not None and live_spread_pct > max_spread_pct:
        r.append(f"Excessive spread {live_spread_pct:.2f}% > {max_spread_pct}%")
    if provisional:
        r.append("Unconfirmed breakout: signal candle not yet complete")
    for issue in plan.issues:
        r.append(issue)
    if plan.risk_reward < min_rr - 1e-6 and not any("Risk/reward" in x for x in r):
        r.append(f"Poor risk/reward {plan.risk_reward:.2f}")
    if plan.status in (PlanStatus.MISSED, PlanStatus.INVALIDATED, PlanStatus.EXPIRED, PlanStatus.TOO_LATE):
        r.append(f"Setup status {plan.status}")
    if plan.direction == "SHORT" and not allow_short:
        r.append("Short selling disabled in settings")
    if regime in (Regime.HIGH_VOLATILITY, Regime.UNKNOWN):
        r.append(f"Conflicting/unclear market conditions: {regime.value}")
    elif plan.direction == "LONG" and regime == Regime.STRONG_BEARISH:
        r.append("Conflicting market: long setup in a strong bearish market")
    elif plan.direction == "SHORT" and regime == Regime.STRONG_BULLISH:
        r.append("Conflicting market: short setup in a strong bullish market")
    elif strategy_regimes and regime not in strategy_regimes:
        r.append(f"Strategy not suited to {regime.value} regime")
    if score < min_score:
        r.append(f"Score {score:.0f} below minimum {min_score:.0f}")
    r.extend(account_gate_reasons)
    return r
