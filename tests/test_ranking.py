"""Scoring, mandatory filters and top-N selection."""
import pytest

from config.settings import CostConfig, ScoringWeights, TradingProfile
from indicators.compute import compute_all
from market.regime import Regime, classify, direction_alignment, IndexState
from ranking.filters import mandatory_rejections
from ranking.ranker import Candidate, select_top
from ranking.scoring import score_candidate
from risk.risk_limits import DayState
from risk.trade_plan import build_trade_plan
from strategies.base import Signal


def _ind(sim, sym="RELIANCE"):
    return compute_all(sim.get_candles(sym, "5m", 6).df, "5m")


def _plan(ind, direction="LONG", price=None):
    i = len(ind) - 1
    r = ind.iloc[i]
    if direction == "LONG":
        sig = Signal("X", "orb", "B", "LONG", "breakout", i, ind.index[i], round(r["high"] + 0.05, 2),
                     round(r["low"] - r["atr14"], 2))
    else:
        sig = Signal("X", "orb", "B", "SHORT", "breakdown", i, ind.index[i], round(r["low"] - 0.05, 2),
                     round(r["high"] + r["atr14"], 2))
    return sig, build_trade_plan(sig, ind, TradingProfile(), CostConfig(), price or float(r["close"]), "DELAYED")


def test_score_bounds_and_weights(sim):
    ind = _ind(sim)
    for d in ("LONG", "SHORT"):
        sc = score_candidate(ind.iloc[-1], ind.iloc[-2], d, "breakout", False, 0.5, "r", 0.5, "s", 50.0, None, True,
                             ScoringWeights())
        assert 0 <= sc.total <= 100
        assert sum(c.weight for c in sc.components) == pytest.approx(100)
        assert all(c.evidence for c in sc.components)
    w = ScoringWeights(trend=40, momentum=0, vwap=0, relative_volume=0, price_action=0, market_sector=0, liquidity=0)
    assert w.normalised()["trend"] == pytest.approx(100)


def test_mandatory_rejections_cover_rules(sim):
    ind = _ind(sim)
    sig, plan = _plan(ind)
    base = dict(data_fresh=True, freshness_msg="", data_label="DELAYED", liquidity_ok=True, liquidity_reasons=[],
                plan=plan, min_rr=1.5, live_spread_pct=None, max_spread_pct=0.15, provisional=False,
                regime=Regime.MILD_BULLISH, strategy_regimes=(), symbol_verified=True, circuit_suspected=False,
                score=80, min_score=55, allow_short=True, account_gate_reasons=[])
    plan.issues, plan.risk_reward, plan.status = [], 2.0, "WAITING_FOR_TRIGGER"
    assert mandatory_rejections(**base) == []
    cases = {
        "data_fresh": (False, "Stale"), "liquidity_ok": (False, "liquidity"), "symbol_verified": (False, "verified"),
        "circuit_suspected": (True, "circuit"), "live_spread_pct": (0.5, "spread"), "provisional": (True, "Unconfirmed"),
        "regime": (Regime.STRONG_BEARISH, "Conflicting"), "score": (10, "Score"),
        "account_gate_reasons": (["Daily loss limit hit"], "Daily loss"), "data_label": ("UNAVAILABLE", "unavailable"),
    }
    for k, (val, word) in cases.items():
        r = mandatory_rejections(**{**base, k: val})
        assert any(word.lower() in x.lower() for x in r), k
    assert mandatory_rejections(**{**base, "regime": Regime.HIGH_VOLATILITY})
    plan.status = "MISSED"
    assert any("MISSED" in x for x in mandatory_rejections(**base))
    plan.status = "WAITING_FOR_TRIGGER"
    plan.risk_reward = 1.0
    assert any("risk/reward" in x.lower() for x in mandatory_rejections(**base))


def _cand(sym, sector, score, cost=5000, rej=None, direction="LONG"):
    class P:
        pass

    class S:
        pass

    p = P()
    p.capital_required, p.direction = cost, direction
    s = S()
    s.total = score
    return Candidate(sym, sym, sector, "scanner", None, p, s, rej or [])


def test_select_top_never_forces_and_limits():
    prof = TradingProfile()
    assert select_top([], prof)[0] == []
    assert select_top([_cand("A", "IT", 90, rej=["x"])], prof)[0] == []
    cands = [_cand("A", "IT", 90), _cand("B", "IT", 85), _cand("C", "BANK", 80), _cand("D", "AUTO", 75),
             _cand("E", "PHARMA", 70)]
    sel, skipped = select_top(cands, prof)
    assert [c.symbol for c in sel] == ["A", "C", "D"]
    assert any("sector" in w for c in skipped for w in c.warnings)


def test_select_top_exposure_limit():
    prof = TradingProfile()  # max exposure 75% of 30k = 22.5k
    cands = [_cand("A", "IT", 90, 7500), _cand("B", "BANK", 85, 7500), _cand("C", "AUTO", 80, 7600)]
    sel, _ = select_top(cands, prof)
    assert len(sel) == 2
    sel, _ = select_top(cands, prof, DayState(open_exposure=20_000))
    assert sel == []


def test_regime_classification_and_alignment():
    up = IndexState("NIFTY 50", "^NSEI", ltp=1, score=0.8)
    assert classify([up], 12)[0] == Regime.STRONG_BULLISH
    side = IndexState("NIFTY 50", "^NSEI", ltp=1, score=0.1)
    assert classify([side], 12)[0] == Regime.SIDEWAYS
    assert classify([side], 25)[0] == Regime.HIGH_VOLATILITY
    dn = IndexState("NIFTY 50", "^NSEI", ltp=1, score=-0.4)
    assert classify([dn], None)[0] == Regime.MILD_BEARISH
    assert classify([], None)[0] == Regime.UNKNOWN
    assert direction_alignment(Regime.STRONG_BEARISH, "LONG") == 0.0
    assert direction_alignment(Regime.STRONG_BEARISH, "SHORT") == 1.0


def test_regime_with_simulated_provider(sim):
    from market.regime import compute_regime
    from market.sector_strength import compute_sector_strength, sector_alignment

    r = compute_regime(sim)
    assert r.regime in Regime and len(r.indices) == 2 and r.indices[0].ltp
    df, _ = compute_sector_strength(sim)
    assert not df.empty and list(df["rank"]) == list(range(1, len(df) + 1))
    top = df.iloc[0]["sector"]
    assert sector_alignment(df, top, "LONG")[0] == 1.0
    assert sector_alignment(df, "NOPE", "LONG")[0] == 0.5


def test_breadth_reports_partial_coverage():
    from market.breadth import compute_breadth

    b = compute_breadth({"A": 1.0, "B": -0.5, "C": 0.0}, universe_size=10)
    assert (b.advances, b.declines, b.unchanged) == (1, 1, 1)
    assert "partial" in b.coverage_note
