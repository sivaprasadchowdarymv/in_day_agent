"""Backtesting page."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from strategies.strategy_engine import STRATEGY_CLASSES
from ui import state
from ui.components.charts import equity_curve
from ui.components.widgets import data_banner, disclaimer, download_df


def backtest_page() -> None:
    st.title("🧪 Backtesting")
    st.warning("Backtests replay history with estimated costs and conservative fills. They do not predict or "
               "guarantee future results. Free intraday history is limited (≈60 days for 5m/15m, ≈7 days for 1m).")
    s = state.settings()
    with st.container(border=True):
        c = st.columns([3, 3])
        syms = c[0].multiselect("Symbols", sorted(state.universe_map()), default=["RELIANCE", "SBIN", "INFY"])
        strats = c[1].multiselect("Strategies", [x.key for x in STRATEGY_CLASSES],
                                  default=["orb", "vwap_retest", "sr_breakout"],
                                  format_func=lambda k: next(x.name for x in STRATEGY_CLASSES if x.key == k))
        c = st.columns(4)
        interval = c[0].selectbox("Interval", ["5m", "15m", "30m", "1h"])
        days = c[1].number_input("Days of history", 5, 59 if interval != "1h" else 700, 30)
        mode = c[2].selectbox("Evaluation", ["Full period", "Out-of-sample split", "Walk-forward"])
        ratio = c[3].slider("Train ratio (OOS)", 0.5, 0.9, 0.7)
        go = st.button("▶️ Run backtest", type="primary")
    if not go:
        disclaimer()
        return
    if not syms or not strats:
        st.error("Pick at least one symbol and one strategy.")
        return
    from agents.agents import BacktestingAgent
    from backtesting.engine import BacktestConfig, out_of_sample, walk_forward

    with st.spinner("Downloading history and replaying candles…"):
        r = BacktestingAgent(state.provider(), s).run((syms, strats, interval, int(days)))
    if not r.ok:
        st.error("; ".join(r.errors))
        return
    res, data = r.output
    for w in r.warnings:
        st.caption(f"⚠️ {w}")
    data_banner("HISTORICAL" if state.provider().realtime_label.value != "SIMULATED" else "SIMULATED",
                f"{sum(len(d) for d in data.values()):,} candles replayed")
    m = res.metrics
    if m.get("trades", 0) == 0:
        st.info(f"No trades. Signals: {res.signals}, rejected by plan rules (R:R, costs, stop width): "
                f"{res.rejected_plans}.")
    else:
        c = st.columns(6)
        c[0].metric("Trades", m["trades"])
        c[1].metric("Win rate", f"{m['win_rate_pct']}%")
        c[2].metric("Profit factor", m["profit_factor"] if m["profit_factor"] is not None else "—")
        c[3].metric("Expectancy / trade", f"₹{m['expectancy_inr']:,.2f}")
        c[4].metric("Net P&L", f"₹{m['net_pnl_inr']:,.2f}")
        c[5].metric("Max drawdown", f"₹{m['max_drawdown_inr']:,.2f}")
        c = st.columns(6)
        c[0].metric("Avg win", f"₹{m['avg_win_inr']:,.2f}")
        c[1].metric("Avg loss", f"₹{m['avg_loss_inr']:,.2f}")
        c[2].metric("Sharpe (daily, rf=0)", m["sharpe_daily_annualised"] or "—")
        c[3].metric("Sortino", m["sortino_daily_annualised"] or "—")
        c[4].metric("Avg hold", f"{m['avg_holding_minutes']} min")
        c[5].metric("Total costs", f"₹{m['total_costs_inr']:,.2f}")
        st.plotly_chart(equity_curve(res.trades, "exit_ts", "net_pnl"), key="bt_eq")
        st.markdown("#### By strategy")
        st.dataframe(pd.DataFrame(res.per_strategy).T.drop(columns=["disclaimer"], errors="ignore"))
        st.markdown("#### Trades")
        st.dataframe(res.trades, hide_index=True)
        download_df(res.trades, "backtest_trades.csv")
    st.caption(f"Signals: {res.signals} · rejected plans: {res.rejected_plans}")
    for n in res.notes:
        st.caption("ℹ️ " + n)
    cfg = BacktestConfig(s.profile, s.costs, interval, allow_short=s.profile.allow_short)
    if mode == "Out-of-sample split":
        with st.spinner("Running out-of-sample evaluation…"):
            o = out_of_sample(data, strats, cfg, ratio)
        st.markdown(f"#### Out-of-sample (split at {o['split_at']})")
        st.dataframe(pd.DataFrame({"in_sample": o["in_sample"], "out_of_sample": o["out_of_sample"]}))
    elif mode == "Walk-forward":
        key = strats[0]
        grid = {"valid_bars": [2, 3, 4]}
        with st.spinner(f"Walk-forward on {key} with grid {grid}…"):
            wf = walk_forward(data, key, grid, cfg, n_folds=3)
        st.markdown(f"#### Walk-forward ({key}, anchored, 3 folds)")
        st.json(wf, expanded=False)
    try:
        from database.db import session_scope
        from database.models import BacktestRun

        with session_scope() as ss:
            ss.add(BacktestRun(strategies=strats, symbols=syms, interval=interval,
                               data_label=state.provider().realtime_label.value, params={"days": int(days)},
                               metrics={k: v for k, v in m.items()},
                               trades=res.trades.astype(str).to_dict("records")[:500] if not res.trades.empty else []))
    except Exception as exc:  # noqa: BLE001
        st.caption(f"(Backtest not saved: {exc})")
    disclaimer()
