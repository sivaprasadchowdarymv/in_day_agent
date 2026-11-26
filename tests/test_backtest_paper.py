"""Backtest fill logic, metrics, no-lookahead replay, and paper-trading lifecycle."""
from datetime import datetime

import pandas as pd
import pytest

from backtesting.engine import BacktestConfig, out_of_sample, run_backtest, walk_forward
from backtesting.metrics import compute_metrics
from backtesting.simulator import SimOrder, manage_bar, position_costs, position_pnl, try_fill
from config.settings import CostConfig, TradingProfile
from data.market_hours import TZ

TS = pd.Timestamp("2026-11-24 10:00", tz=TZ)


def bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "volume": 1000})


def order(direction="LONG"):
    if direction == "LONG":
        return SimOrder("X", "orb", "LONG", 100.0, 100.5, 98.0, 103.0, 105.0, 10, 0, 3, TS)
    return SimOrder("X", "orb", "SHORT", 100.0, 99.5, 102.0, 97.0, 95.0, 10, 0, 3, TS)


def test_fill_at_trigger_with_slippage():
    st, pos = try_fill(order(), bar(99.5, 100.2, 99.4, 100.1), 1, TS, slip_bps=5)
    assert st == "filled" and pos.entry_price == pytest.approx(100.05)


def test_gap_beyond_limit_is_missed_not_chased():
    st, pos = try_fill(order(), bar(101.0, 102, 100.9, 101.5), 1, TS, slip_bps=0)
    assert st == "missed" and pos is None


def test_gap_within_limit_fills_at_open():
    st, pos = try_fill(order(), bar(100.3, 101, 100.2, 100.8), 1, TS, slip_bps=0)
    assert pos.entry_price == pytest.approx(100.3)


def test_order_expiry():
    assert try_fill(order(), bar(99, 99.5, 98.9, 99.2), 10, TS, 0)[0] == "expired"


def test_entry_bar_stop_only():
    st, pos = try_fill(order(), bar(99.5, 104, 97.5, 100), 1, TS, 0)  # touches target AND stop
    assert pos.closed and pos.exits[0][2].startswith("STOP")


def test_stop_and_target_same_bar_assumes_stop():
    _, pos = try_fill(order(), bar(99.5, 100.2, 99.4, 100.1), 1, TS, 0)
    manage_bar(pos, bar(100, 103.5, 97.5, 101), TS, 0)
    assert pos.closed and pos.exits[-1][2] == "STOP"


def test_gap_through_stop_exits_at_open():
    _, pos = try_fill(order(), bar(99.5, 100.2, 99.4, 100.1), 1, TS, 0)
    manage_bar(pos, bar(96.0, 96.5, 95.5, 96.2), TS, 0)
    assert pos.exits[-1][0] == pytest.approx(96.0) and "gap" in pos.exits[-1][2]


def test_partial_t1_then_breakeven_then_t2():
    _, pos = try_fill(order(), bar(99.5, 100.2, 99.4, 100.1), 1, TS, 0)
    manage_bar(pos, bar(101, 103.2, 100.8, 103), TS, 0)
    assert pos.t1_done and pos.open_qty == 5 and pos.stop == pos.entry_price
    manage_bar(pos, bar(103, 105.5, 102.5, 105), TS, 0)
    assert pos.closed and pos.exits[-1][2] == "TARGET2"
    assert position_pnl(pos) == pytest.approx(5 * 3 + 5 * 5)
    assert position_costs(pos, CostConfig()) > 0


def test_breakeven_stop_after_t1():
    _, pos = try_fill(order(), bar(99.5, 100.2, 99.4, 100.1), 1, TS, 0)
    manage_bar(pos, bar(101, 103.2, 100.8, 103), TS, 0)
    manage_bar(pos, bar(102, 102.5, 99.0, 99.5), TS, 0)
    assert pos.closed and pos.exits[-1][2] == "BREAKEVEN STOP"


def test_short_mirror_and_square_off():
    st, pos = try_fill(order("SHORT"), bar(100.5, 100.6, 99.9, 100.0), 1, TS, 0)
    assert st == "filled" and pos.entry_price == pytest.approx(100.0)
    manage_bar(pos, bar(99.8, 99.9, 98.5, 98.6), TS, 0, square_off=True)
    assert pos.closed and pos.exits[-1][2] == "SQUARE-OFF"
    assert position_pnl(pos) > 0


def test_metrics_math():
    t = pd.DataFrame({"net_pnl": [100, -50, 200, -50], "gross_pnl": [110, -40, 210, -40], "costs": [10] * 4,
                      "r_multiple": [1, -0.5, 2, -0.5], "bars_held": [2, 4, 6, 8],
                      "exit_ts": pd.date_range("2026-11-20 10:00", periods=4, freq="D", tz=TZ)})
    m = compute_metrics(t, 30_000, 5)
    assert m["trades"] == 4 and m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(3.0)
    assert m["expectancy_inr"] == pytest.approx(50.0)
    assert m["max_drawdown_inr"] == pytest.approx(-50.0)
    assert m["avg_holding_minutes"] == pytest.approx(25.0)
    assert "not a guarantee" in m["disclaimer"]
    assert compute_metrics(pd.DataFrame(), 30_000)["trades"] == 0


def test_backtest_runs_and_is_causal(sim):
    s = TradingProfile()
    s.min_net_risk_reward = 0.0  # allow more trades for the replay test
    cfg = BacktestConfig(s, CostConfig(), "15m")
    data = {sym: sim.get_candles(sym, "15m", 30).df for sym in ("RELIANCE", "SBIN", "ITC")}
    full = run_backtest(data, ["ema_trend", "sr_breakout", "vwap_retest", "orb"], cfg)
    assert full.signals > 0 and "trades" in full.metrics
    # replaying only the first 20 sessions must reproduce exactly the trades that closed in them
    days = sorted(set(data["RELIANCE"].index.date))
    cut = days[20]
    part = run_backtest({k: v[v.index.date < cut] for k, v in data.items()},
                        ["ema_trend", "sr_breakout", "vwap_retest", "orb"], cfg)
    if not full.trades.empty:
        early = full.trades[pd.to_datetime(full.trades["exit_ts"]).dt.date < cut].reset_index(drop=True)
        cols = ["symbol", "strategy", "entry_ts", "entry_price", "net_pnl"]
        pd.testing.assert_frame_equal(early[cols], part.trades[cols].reset_index(drop=True)
                                      if not part.trades.empty else early[cols].iloc[0:0])


def test_oos_and_walk_forward_structure(sim):
    cfg = BacktestConfig(TradingProfile(), CostConfig(), "15m")
    data = {"SBIN": sim.get_candles("SBIN", "15m", 25).df}
    o = out_of_sample(data, ["sr_breakout"], cfg, 0.6)
    assert set(o) == {"in_sample", "out_of_sample", "split_at"}
    wf = walk_forward(data, "sr_breakout", {"min_rvol": [1.2, 1.5]}, cfg, n_folds=2, min_train_trades=0)
    assert "folds" in wf and len(wf["folds"]) == 2


# ---------------------------------------------------------------- paper trading
class _Plan:
    def __init__(self, **kw):
        self.__dict__.update(dict(symbol="SBIN", direction="LONG", strategy="orb", entry_trigger=100.0,
                                  entry_limit=100.5, stop_loss=98.0, target1=103.0, target2=105.0, quantity=10,
                                  price_label="SIMULATED"))
        self.__dict__.update(kw)


def _bars(rows, start="2026-11-24 10:00"):
    idx = pd.date_range(pd.Timestamp(start, tz=TZ), periods=len(rows), freq="1min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=100)


def test_paper_lifecycle(sim, settings, monkeypatch):
    from database.db import session_scope
    from database.models import PaperTrade
    from paper_trading import engine as pe

    monkeypatch.setattr(pe, "now_ist", lambda: datetime(2026, 11, 24, 9, 59, tzinfo=TZ))
    eng = pe.PaperTradingEngine(sim, settings)
    tid = eng.create_from_plan(_Plan(), sector="BANK")
    bars = _bars([(99.5, 100.2, 99.4, 100.1), (100.5, 103.2, 100.4, 103.0), (103, 105.2, 102.8, 105.0)])
    with session_scope() as s:
        t = s.get(PaperTrade, tid)
        eng.update_trade(t, bars, "SIMULATED", datetime(2026, 11, 24, 10, 3, tzinfo=TZ))
    df = eng.trades_df()
    row = df.iloc[0]
    assert row["status"] == "CLOSED" and "TARGET2" in row["exit_reason"]
    assert row["realized_gross"] == pytest.approx(5 * (103 - row["entry_price"]) + 5 * (105 - row["entry_price"]))
    assert row["realized_net"] < row["realized_gross"] and row["data_label"] == "SIMULATED"
    perf = eng.performance()
    assert perf["closed_trades"] == 1 and "orb" in perf["by_strategy"]
    assert b"symbol" in eng.export_csv()
    st = eng.day_state(datetime(2026, 11, 24).date())
    assert st.trades_taken == 1 and st.realized_pnl > 0


def test_paper_missed_expired_cancel(sim, settings, monkeypatch):
    from database.db import session_scope
    from database.models import PaperTrade
    from paper_trading import engine as pe

    monkeypatch.setattr(pe, "now_ist", lambda: datetime(2026, 11, 24, 9, 59, tzinfo=TZ))
    eng = pe.PaperTradingEngine(sim, settings)
    a = eng.create_from_plan(_Plan())
    b = eng.create_from_plan(_Plan(), valid_minutes=2)
    c = eng.create_from_plan(_Plan())
    with session_scope() as s:
        eng.update_trade(s.get(PaperTrade, a), _bars([(101.5, 102, 101.2, 101.8)]), "SIMULATED",
                         datetime(2026, 11, 24, 10, 1, tzinfo=TZ))
        eng.update_trade(s.get(PaperTrade, b), _bars([(99, 99.2, 98.9, 99)] * 5), "SIMULATED",
                         datetime(2026, 11, 24, 10, 5, tzinfo=TZ))
    eng.close_manual(c)
    st = eng.trades_df().set_index("id")["status"]
    assert st[a] == "EXPIRED" and st[b] == "EXPIRED" and st[c] == "CANCELLED"


def test_paper_stop_loss_and_manual_close(sim, settings, monkeypatch):
    from database.db import session_scope
    from database.models import PaperTrade
    from paper_trading import engine as pe

    monkeypatch.setattr(pe, "now_ist", lambda: datetime(2026, 11, 24, 9, 59, tzinfo=TZ))
    eng = pe.PaperTradingEngine(sim, settings)
    a = eng.create_from_plan(_Plan())
    b = eng.create_from_plan(_Plan())
    with session_scope() as s:
        eng.update_trade(s.get(PaperTrade, a), _bars([(99.5, 100.2, 99.4, 100.1), (99.5, 99.6, 97.5, 97.8)]),
                         "SIMULATED", datetime(2026, 11, 24, 10, 2, tzinfo=TZ))
        eng.update_trade(s.get(PaperTrade, b), _bars([(99.5, 100.2, 99.4, 100.1)]), "SIMULATED",
                         datetime(2026, 11, 24, 10, 1, tzinfo=TZ))
    eng.close_manual(b, price=101.0)
    df = eng.trades_df().set_index("id")
    assert df.loc[a, "exit_reason"] == "STOP" and df.loc[a, "realized_net"] < 0
    assert df.loc[b, "exit_reason"] == "MANUAL EXIT" and df.loc[b, "status"] == "CLOSED"


def test_paper_rejects_zero_quantity(sim, settings):
    from paper_trading.engine import PaperTradingEngine

    with pytest.raises(ValueError):
        PaperTradingEngine(sim, settings).create_from_plan(_Plan(quantity=0))
