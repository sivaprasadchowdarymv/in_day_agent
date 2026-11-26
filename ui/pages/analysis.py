"""Technical Chart Analysis and Strategy Explorer pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from strategies.strategy_engine import STRATEGY_CLASSES, all_strategies, get_strategy
from ui import state
from ui.components.charts import candle_chart
from ui.components.widgets import data_banner, disclaimer

INTERVALS = ["5m", "15m", "3m", "1m", "30m", "1h", "1d"]


@st.cache_data(ttl=60, show_spinner=False)
def _load(kind: str, symbol: str, interval: str, days: int):
    from indicators.compute import compute_all

    cs = state.provider().get_candles(symbol, interval, days)
    return compute_all(cs.df, interval), cs.label.value, cs.warnings, str(cs.last_ts), cs.interval


def _symbol_picker(key: str) -> str:
    syms = sorted(state.universe_map())
    default = st.session_state.get("chart_symbol", "RELIANCE")
    return st.selectbox("Symbol", syms, index=syms.index(default) if default in syms else 0, key=key)


def chart_page() -> None:
    st.title("📈 Technical Chart Analysis")
    c = st.columns([2, 1, 1])
    with c[0]:
        sym = _symbol_picker("chart_sym")
    interval = c[1].selectbox("Interval", INTERVALS)
    days = c[2].number_input("Days", 2, 120 if interval != "1d" else 400, 5 if interval != "1d" else 200)
    overlays = st.multiselect("Overlays", ["ema9", "ema21", "ema50", "ema200", "vwap", "supertrend", "bb_upper",
                                           "bb_lower"], default=["ema9", "ema21", "vwap"])
    try:
        ind, label, warns, last_ts, _ = _load(state.settings().app.data_provider, sym, interval, int(days))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Data unavailable for {sym}: {exc}")
        return
    if ind.empty:
        st.warning("No completed candles available.")
        return
    from brokers.base import CandleSet, DataLabel
    from data.market_hours import now_ist
    from data.validation import freshness
    from indicators.price_action import support_resistance

    cs = CandleSet(sym, interval, ind[["open", "high", "low", "close", "volume"]], DataLabel(label), "", now_ist())
    fresh, msg = freshness(cs, state.settings().scan.stale_after_minutes)
    data_banner(label, msg)
    if not fresh:
        st.error("Data is STALE – indicators are shown for study only and cannot confirm signals.")
    levels = support_resistance(ind) if len(ind) > 20 else []
    st.plotly_chart(candle_chart(ind, f"{sym} · {interval}", levels=levels, show=tuple(overlays)), key="chart_main")
    last = ind.iloc[-1]
    cols = st.columns(6)
    for col, (k, lbl) in zip(cols, [("rsi14", "RSI 14"), ("adx", "ADX"), ("atr14", "ATR 14"), ("vwap", "VWAP"),
                                    ("rvol_tod", "Rel. volume"), ("macd_hist", "MACD hist")]):
        v = last.get(k)
        col.metric(lbl, "—" if pd.isna(v) else f"{v:,.2f}")
    with st.expander("Indicator snapshot (last completed candle)"):
        from indicators.compute import snapshot

        st.json(snapshot(ind), expanded=False)
    if interval != "1d":
        from strategies.base import StrategyContext
        from strategies.strategy_engine import latest_signals

        sigs = latest_signals(ind, all_strategies(), StrategyContext(sym, interval))
        st.markdown("#### Active strategy signals (last 3 completed candles)")
        if sigs:
            st.dataframe(pd.DataFrame([{"Strategy": x.strategy_name, "Dir": x.direction, "Candle": str(x.bar_ts),
                                        "Trigger": x.trigger, "Structural stop": x.structural_stop,
                                        "Reasons": "; ".join(x.reasons)} for x in sigs]), hide_index=True)
        else:
            st.caption("No strategy conditions are met right now.")
    if levels:
        st.caption("Support/resistance clusters: " + ", ".join(f"{l['price']:.2f} ({l['touches']}×)" for l in levels))
    for w in warns:
        st.caption(f"⚠️ {w}")
    disclaimer()


def strategy_page() -> None:
    st.title("🧩 Strategy Explorer")
    for cls in STRATEGY_CLASSES:
        d = cls().describe()
        with st.expander(f"{d['name']}  ·  {', '.join(d['directions'])}  ·  {d['setup_type']}"):
            st.markdown(f"**Entry:** {d['entry']}")
            st.markdown(f"**Confirmation:** {d['confirmation']}")
            st.markdown(f"**Stop-loss:** {d['stop_loss']}")
            st.markdown(f"**Targets:** {d['targets']}")
            st.markdown(f"**Invalidation:** {d['invalidation']}")
            st.markdown(f"**Suitable regimes:** {', '.join(d['suitable_regimes'])}")
            st.code(str(d["params"]), language="python")
    st.markdown("### Historical signals on a symbol")
    c = st.columns([2, 1, 1, 2])
    with c[0]:
        sym = _symbol_picker("strat_sym")
    interval = c[1].selectbox("Interval", ["5m", "15m", "30m"], key="strat_int")
    days = c[2].number_input("Days", 3, 59, 10, key="strat_days")
    key = c[3].selectbox("Strategy", [x.key for x in STRATEGY_CLASSES],
                         format_func=lambda k: next(x.name for x in STRATEGY_CLASSES if x.key == k))
    if st.button("Find signals", type="primary"):
        from strategies.base import StrategyContext

        try:
            ind, label, *_ = _load(state.settings().app.data_provider, sym, interval, int(days))
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
            return
        data_banner(label)
        s = get_strategy(key)
        prep = s.prepare(ind)
        ctx = StrategyContext(sym, interval, allow_short=state.settings().profile.allow_short)
        sigs = [x for i in range(len(prep)) if (x := s.evaluate(prep, i, ctx))]
        st.write(f"{len(sigs)} signal(s) on completed candles (evaluated bar-by-bar, no look-ahead).")
        fig = candle_chart(ind, f"{sym} · {s.name}", show=("ema9", "ema21", "vwap"), bars=len(ind))
        import plotly.graph_objects as go

        xs = [x.bar_ts.strftime("%d-%b %H:%M") for x in sigs]
        ys = [x.trigger for x in sigs]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name="Signals",
                                 marker=dict(size=11, symbol=["triangle-up" if x.direction == "LONG" else
                                                              "triangle-down" for x in sigs],
                                             color=["#27ae60" if x.direction == "LONG" else "#c0392b" for x in sigs])),
                      1, 1)
        st.plotly_chart(fig, key="strat_chart")
        if sigs:
            st.dataframe(pd.DataFrame([{"Candle": str(x.bar_ts), "Dir": x.direction, "Trigger": x.trigger,
                                        "Stop": x.structural_stop, "Reasons": "; ".join(x.reasons)} for x in sigs]),
                         hide_index=True)
    disclaimer()
