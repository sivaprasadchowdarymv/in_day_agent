"""Indicator correctness, VWAP session reset and absence of look-ahead."""
import numpy as np
import pandas as pd
import pytest

from indicators.compute import compute_all
from indicators.momentum import macd, rsi
from indicators.price_action import candle_patterns, confirmed_swings, previous_day_levels, support_resistance
from indicators.trend import adx, atr, ema, supertrend
from indicators.volatility import bollinger, opening_range
from indicators.volume import relative_volume_bar, session_vwap
from tests.conftest import make_session


def test_ema_matches_recursive_definition():
    s = pd.Series(np.arange(1, 31, dtype=float))
    e = ema(s, 9)
    a = 2 / 10
    manual = [s.iloc[0]]
    for v in s.iloc[1:]:
        manual.append(a * v + (1 - a) * manual[-1])
    assert e.iloc[8:].to_numpy() == pytest.approx(np.array(manual)[8:])
    assert e.iloc[:8].isna().all()


def test_rsi_extremes_and_range():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert rsi(up, 14).dropna().eq(100).all()
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert rsi(down, 14).dropna().max() < 1e-9
    rnd = pd.Series(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 300)))
    r = rsi(rnd, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_matches_wilder_recursion():
    closes = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                        46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45])
    d = closes.diff()
    ag = al = None
    out = []
    for k, x in enumerate(d):
        if np.isnan(x):
            out.append(np.nan)
            continue
        g, l = max(x, 0), max(-x, 0)
        ag = g if ag is None else ag + (g - ag) / 14
        al = l if al is None else al + (l - al) / 14
        out.append(100 - 100 / (1 + ag / al) if al else 100.0)
    ours = rsi(closes, 14)
    valid = ours.notna()
    assert valid.sum() > 5
    assert ours[valid].to_numpy() == pytest.approx(np.array(out)[valid.to_numpy()], rel=1e-9)


def test_atr_constant_range():
    df = make_session("2026-11-24", [100] * 40, spread=1.0)
    assert atr(df, 14).dropna().iloc[-1] == pytest.approx(2.0)


def test_macd_sign_follows_trend():
    up = pd.Series(np.linspace(100, 130, 80))
    m = macd(up)
    assert m["macd"].dropna().iloc[-1] > 0


def test_supertrend_and_adx_on_trend():
    df = make_session("2026-11-24", np.linspace(100, 140, 75), spread=0.3)
    st = supertrend(df)
    assert st["supertrend_dir"].iloc[-1] == 1 and st["supertrend"].iloc[-1] < df["close"].iloc[-1]
    a = adx(df)
    assert a["adx"].iloc[-1] > 25 and a["plus_di"].iloc[-1] > a["minus_di"].iloc[-1]


def test_vwap_resets_each_session():
    d1 = make_session("2026-11-23", [100, 102, 104], vols=[100, 100, 100], spread=0)
    d2 = make_session("2026-11-24", [200, 202], vols=[100, 300], spread=0)
    both = pd.concat([d1, d2])
    tp = (both["high"] + both["low"] + both["close"]) / 3
    v = session_vwap(both)
    assert v.iloc[2] == pytest.approx(tp.iloc[:3].mean())
    # first bar of new session: VWAP equals its own typical price (no carry-over)
    assert v.iloc[3] == pytest.approx(tp.iloc[3])
    assert v.iloc[4] == pytest.approx((tp.iloc[3] * 100 + tp.iloc[4] * 300) / 400)


def test_vwap_nan_without_volume():
    d = make_session("2026-11-24", [100, 101], vols=[0, 0])
    assert session_vwap(d).isna().all()


def test_relative_volume_excludes_current_bar():
    d = make_session("2026-11-24", [100] * 25, vols=[1000] * 24 + [5000])
    assert relative_volume_bar(d, 20).iloc[-1] == pytest.approx(5.0)


def test_opening_range_unknown_until_complete():
    d = make_session("2026-11-24", [100, 101, 99, 100.5, 102], spread=0)
    o = opening_range(d, 15)
    assert o["or_high"].iloc[:2].isna().all()  # 09:15, 09:20 bars: range not finished
    assert o["or_high"].iloc[2] == pytest.approx(101) and o["or_low"].iloc[2] == pytest.approx(99)
    assert o["or_high"].iloc[4] == pytest.approx(101)


def test_previous_day_levels():
    d1 = make_session("2026-11-23", [100, 105, 98, 101], spread=0)
    d2 = make_session("2026-11-24", [102, 103], spread=0)
    p = previous_day_levels(pd.concat([d1, d2]))
    assert p["pdh"].iloc[-1] == 105 and p["pdl"].iloc[-1] == 98 and p["pdc"].iloc[-1] == 101
    assert p["pdh"].iloc[:4].isna().all()
    assert p["gap_pct"].iloc[-1] == pytest.approx((102 / 101 - 1) * 100)


def test_swings_are_confirmed_with_lag():
    closes = [100, 101, 102, 105, 102, 101, 100, 100, 100]
    d = make_session("2026-11-24", closes, spread=0)
    s = confirmed_swings(d, k=3)
    assert s["swing_high"].iloc[3:6].isna().all()  # peak at bar 3 unknown until bar 6
    assert s["swing_high"].iloc[6] == pytest.approx(105)


def test_support_resistance_clusters():
    rng = np.random.default_rng(2)
    base = np.tile([100, 101, 102, 103, 102, 101], 15) + rng.normal(0, 0.02, 90)
    d = make_session("2026-11-24", base, spread=0.05)
    lv = support_resistance(d)
    assert lv and max(l["touches"] for l in lv) >= 3


def test_candle_patterns():
    df = pd.DataFrame({"open": [10, 9.5], "high": [10.1, 10.6], "low": [9.4, 9.4], "close": [9.5, 10.5]},
                      index=pd.date_range("2026-11-24 09:15", periods=2, freq="5min", tz="Asia/Kolkata"))
    p = candle_patterns(df)
    assert bool(p["bull_engulf"].iloc[1])


def test_bollinger_contains_price_mostly():
    s = pd.Series(100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 200)))
    b = bollinger(s).dropna()
    inside = ((s.loc[b.index] <= b["bb_upper"]) & (s.loc[b.index] >= b["bb_lower"])).mean()
    assert inside > 0.85


def test_compute_all_is_causal(sim):
    """Indicators at bar t must not change when future bars are appended."""
    df = sim.get_candles("INFY", "5m", 6).df
    full = compute_all(df, "5m")
    for cut in (90, 200, len(df) - 3):
        part = compute_all(df.iloc[:cut], "5m")
        a, b = full.iloc[cut - 1], part.iloc[-1]
        for col in full.columns:
            x, y = a[col], b[col]
            if isinstance(x, (bool, np.bool_)):
                assert bool(x) == bool(y), col
            elif pd.isna(x) or pd.isna(y):
                assert pd.isna(x) and pd.isna(y), col
            else:
                assert x == pytest.approx(y, rel=1e-9, abs=1e-9), col
