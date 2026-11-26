"""Strategy signals, trade plans, position sizing, costs and risk limits."""
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from config.settings import CostConfig, TradingProfile
from data.market_hours import TZ
from indicators.compute import compute_all
from risk.position_sizing import size_position
from risk.risk_limits import DayState, check_account_limits, return_correlation
from risk.trade_plan import PlanStatus, build_trade_plan
from risk.transaction_costs import brokerage_per_order, round_trip_costs
from strategies.base import Signal, StrategyContext, round_to_tick
from strategies.strategy_engine import STRATEGY_CLASSES, all_strategies, get_strategy, latest_signals
from tests.conftest import make_session


def _orb_frame():
    d1 = make_session("2026-11-23", 100 + np.sin(np.arange(75) / 5), vols=[10_000] * 75)
    # day 2: OR 09:15-09:30 between ~100 and ~101, drift, then volume breakout above OR high
    closes = [100.2, 100.8, 100.4, 100.5, 100.6, 100.7, 100.6, 100.8, 101.6]
    vols = [20_000, 15_000, 12_000, 9_000, 9_000, 9_000, 9_000, 9_000, 40_000]
    d2 = make_session("2026-11-24", closes, vols=vols, spread=0.1)
    return compute_all(pd.concat([d1, d2]), "5m")


def test_orb_long_signal_and_no_repeat():
    ind = _orb_frame()
    s = get_strategy("orb")
    ctx = StrategyContext("TEST")
    i = len(ind) - 1
    sig = s.evaluate(ind, i, ctx)
    assert sig is not None and sig.direction == "LONG"
    assert sig.trigger > ind["high"].iloc[i] and sig.structural_stop < ind["or_high"].iloc[i]
    # no signal before the opening range is complete
    first_bar_day2 = ind.index.get_loc(pd.Timestamp("2026-11-24 09:20", tz=TZ))
    assert s.evaluate(ind, first_bar_day2, ctx) is None


def test_shorts_disabled_blocks_short_signals(sim):
    ind = compute_all(sim.get_candles("TATASTEEL", "5m", 10).df, "5m")
    ctx = StrategyContext("TATASTEEL", allow_short=False)
    for s in all_strategies():
        prep = s.prepare(ind)
        for i in range(60, len(prep), 3):
            sig = s.evaluate(prep, i, ctx)
            assert sig is None or sig.direction == "LONG"


def test_strategies_no_lookahead(sim):
    """A signal at bar i must be identical whether or not future bars exist."""
    df = sim.get_candles("HDFCBANK", "5m", 8).df
    full = compute_all(df, "5m")
    ctx = StrategyContext("HDFCBANK")
    for cls in STRATEGY_CLASSES:
        s = cls()
        pf = s.prepare(full)
        for i in range(80, len(df) - 1, 7):
            pt = s.prepare(compute_all(df.iloc[: i + 1], "5m"))
            a, b = s.evaluate(pf, i, ctx), s.evaluate(pt, i, ctx)
            assert (a is None) == (b is None), (cls.key, i)
            if a:
                assert a.trigger == b.trigger and a.structural_stop == b.structural_stop


def test_every_strategy_documented():
    for cls in STRATEGY_CLASSES:
        d = cls().describe()
        for k in ("entry", "confirmation", "stop_loss", "targets", "invalidation", "suitable_regimes"):
            assert d[k], (cls.key, k)
    assert len(STRATEGY_CLASSES) == 8


def test_latest_signals_respects_validity(sim):
    ind = compute_all(sim.get_candles("SBIN", "5m", 8).df, "5m")
    sigs = latest_signals(ind, all_strategies(), StrategyContext("SBIN"), search_bars=3)
    for s in sigs:
        assert (len(ind) - 1) - s.bar_index <= s.valid_bars


def test_round_to_tick():
    assert round_to_tick(100.03, 0.05, "up") == 100.05
    assert round_to_tick(100.03, 0.05, "down") == 100.0
    assert round_to_tick(100.025, 0.05) in (100.0, 100.05)


# ---------------------------------------------------------------- sizing
def test_position_size_formula_default_profile():
    p = TradingProfile()  # ₹30,000, 0.5%, 25%
    r = size_position(entry=500, stop=495, profile=p)
    assert r.risk_budget == 150
    assert r.risk_based_qty == 30 and r.capital_based_qty == 15
    assert r.quantity == 15 and r.limiting_factor == "capital allocation"
    assert r.max_planned_loss == 75 and r.capital_required == 7500


def test_position_size_risk_limited_and_zero():
    p = TradingProfile()
    r = size_position(100, 98, p)
    assert r.quantity == 75 and r.limiting_factor == "risk budget" and r.max_planned_loss == 150
    assert size_position(10_000, 9_900, p).quantity == 0  # price above allocation
    assert size_position(100, 100, p).quantity == 0


def test_capital_cap_enforced():
    p = TradingProfile(capital=49_999)
    assert p.validate() == []
    assert TradingProfile(capital=60_000).validate()


# ---------------------------------------------------------------- costs
def test_brokerage_rules():
    c = CostConfig()
    assert brokerage_per_order(50_000, c) == 20  # flat cap
    assert brokerage_per_order(7_500, c) == pytest.approx(7.5)  # 0.1%
    assert brokerage_per_order(1_000, c) == pytest.approx(5.0)  # minimum ₹5
    assert brokerage_per_order(100, c) == pytest.approx(2.5)  # lower of ₹5 and 2.5%


def test_round_trip_costs_components():
    c = CostConfig()
    b = round_trip_costs(500, 505, 15, c)
    assert b.stt == pytest.approx(505 * 15 * 0.025 / 100)
    assert b.stamp == pytest.approx(500 * 15 * 0.003 / 100)
    assert b.gst == pytest.approx((b.brokerage + b.exchange_txn + b.sebi) * 0.18)
    assert b.total_with_slippage > b.total_charges > 0 and b.is_estimate


# ---------------------------------------------------------------- trade plan
def _signal(ind, direction="LONG", trigger=None, stop=None):
    i = len(ind) - 1
    r = ind.iloc[i]
    return Signal("TEST", "orb", "B. ORB", direction, "breakout", i, ind.index[i],
                  trigger if trigger is not None else round(r["high"] + 0.05, 2),
                  stop if stop is not None else round(r["low"] - 0.5, 2), ["r"], ["c"], ["inv"])


def test_trade_plan_long_targets_and_rr():
    ind = _orb_frame()
    sig = _signal(ind)
    plan = build_trade_plan(sig, ind, TradingProfile(), CostConfig(), current_price=float(ind["close"].iloc[-1]),
                            price_label="DELAYED")
    assert plan.stop_loss < plan.entry_trigger <= plan.entry_limit < plan.target1
    assert plan.risk_per_share == pytest.approx(plan.entry_limit - plan.stop_loss, abs=0.01)
    if plan.target2:
        assert plan.target2 > plan.target1
    assert plan.risk_reward >= 1.5 - 1e-6 or any("Risk/reward" in x for x in plan.issues)
    assert plan.quantity * plan.entry_limit <= TradingProfile().max_allocation + 1e-6
    assert plan.max_planned_loss <= TradingProfile().risk_budget + 1e-6


def test_trade_plan_missed_and_invalidated():
    ind = _orb_frame()
    sig = _signal(ind)
    p, c = TradingProfile(), CostConfig()
    far = build_trade_plan(sig, ind, p, c, current_price=sig.trigger * 1.02, price_label="DELAYED")
    assert far.status == PlanStatus.MISSED
    dead = build_trade_plan(sig, ind, p, c, current_price=sig.structural_stop - 1, price_label="DELAYED")
    assert dead.status == PlanStatus.INVALIDATED
    waiting = build_trade_plan(sig, ind, p, c, current_price=sig.trigger - 0.2, price_label="DELAYED")
    assert waiting.status == PlanStatus.WAITING
    late = build_trade_plan(sig, ind, p, c, current_price=sig.trigger - 0.2, price_label="DELAYED",
                            now_time=time(15, 0))
    assert late.status == PlanStatus.TOO_LATE
    expired = build_trade_plan(sig, ind, p, c, current_price=sig.trigger - 0.2, price_label="DELAYED",
                               bars_since_signal=10)
    assert expired.status == PlanStatus.EXPIRED


def test_trade_plan_invalid_stop_and_tiny_stop():
    ind = _orb_frame()
    bad = _signal(ind, trigger=101.0, stop=102.0)
    plan = build_trade_plan(bad, ind, TradingProfile(), CostConfig(), 100.9, "DELAYED")
    assert not plan.valid and any("Invalid stop" in x for x in plan.issues)
    tiny = _signal(ind, trigger=101.0, stop=100.99)
    plan2 = build_trade_plan(tiny, ind, TradingProfile(), CostConfig(), 100.9, "DELAYED")
    atr = ind["atr14"].iloc[-1]
    assert plan2.entry_limit - plan2.stop_loss >= 0.3 * atr - 0.06


def test_trade_plan_target_capped_by_level():
    ind = _orb_frame()
    sig = _signal(ind)
    risk_guess = sig.trigger - sig.structural_stop
    lvl = [{"price": sig.trigger + 0.5 * risk_guess, "touches": 3, "kind": "R"}]
    plan = build_trade_plan(sig, ind, TradingProfile(), CostConfig(), float(ind["close"].iloc[-1]), "DELAYED",
                            levels=lvl)
    assert plan.target1 < lvl[0]["price"]
    assert any("Risk/reward" in x for x in plan.issues)


def test_short_plan_mirrors():
    ind = _orb_frame()
    i = len(ind) - 1
    r = ind.iloc[i]
    sig = Signal("TEST", "breakdown", "H", "SHORT", "breakdown", i, ind.index[i], round(r["low"] - 0.05, 2),
                 round(r["high"] + 0.5, 2))
    plan = build_trade_plan(sig, ind, TradingProfile(), CostConfig(), float(r["close"]), "DELAYED")
    assert plan.target1 < plan.entry_limit <= plan.entry_trigger < plan.stop_loss


# ---------------------------------------------------------------- risk limits
def test_account_limits():
    p = TradingProfile()
    now = datetime(2026, 11, 24, 12, 0, tzinfo=TZ)
    assert check_account_limits(DayState(), p, now).allowed
    assert not check_account_limits(DayState(realized_pnl=-500), p, now).allowed
    assert not check_account_limits(DayState(trades_taken=3), p, now).allowed
    st = DayState(consecutive_losses=2, last_loss_at=now - timedelta(minutes=10))
    g = check_account_limits(st, p, now)
    assert not g.allowed and "Cooldown" in g.reasons[0]
    st.last_loss_at = now - timedelta(minutes=45)
    assert check_account_limits(st, p, now).allowed
    assert not check_account_limits(DayState(open_exposure=30_000), p, now).allowed


def test_return_correlation():
    a = pd.Series(np.cumprod(1 + np.random.default_rng(1).normal(0, 0.01, 60)))
    assert return_correlation(a, a * 2) == pytest.approx(1.0)
    assert return_correlation(a.iloc[:5], a.iloc[:5]) is None
