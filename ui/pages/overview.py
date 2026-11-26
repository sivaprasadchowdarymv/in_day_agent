"""Dashboard and Market Overview pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import state
from ui.components.charts import bar_chart, candle_chart
from ui.components.widgets import data_banner, disclaimer, fmt, no_trade


def _paper_pnl() -> tuple[float, int]:
    try:
        from paper_trading.engine import PaperTradingEngine

        stt = PaperTradingEngine(state.provider(), state.settings()).day_state()
        return stt.realized_pnl, stt.trades_taken
    except Exception:  # noqa: BLE001
        return 0.0, 0


def dashboard() -> None:
    st.title("📊 Dashboard")
    s = state.settings()
    out = st.session_state.get("last_scan")
    data_banner(out.data_label if out else state.provider().realtime_label.value,
                f"Provider: {state.provider().name}")
    c = st.columns(4)
    c[0].metric("Trading capital", fmt(s.profile.capital, 0, "₹"))
    c[1].metric("Risk per trade", f"{s.profile.risk_per_trade_pct}% = ₹{s.profile.risk_budget:,.0f}")
    c[2].metric("Daily planned risk (max loss)", fmt(s.profile.max_daily_loss, 0, "₹"))
    pnl, n = _paper_pnl()
    c[3].metric("Paper P&L today (net est.)", fmt(pnl, 2, "₹"), f"{n} trade(s)", delta_color="off")
    if out is None:
        st.info("No scan has been run in this session yet. Run one here or from **Automatic Stock Scanner**.")
        if st.button("▶️ Run market scan now", type="primary"):
            from ui.runner import run_scan

            run_scan("seed", s.scan.max_symbols_per_scan, s.scan.interval, None, False, False)
            st.rerun()
        disclaimer()
        return
    reg = out.regime
    st.subheader(f"Market regime: {reg.regime.value}")
    cols = st.columns(len(reg.indices) + 1)
    for col, ix in zip(cols, reg.indices):
        col.metric(ix.name, fmt(ix.ltp), f"{ix.change_pct:+.2f}%" if ix.change_pct is not None else "n/a")
    cols[-1].metric("India VIX", fmt(reg.vix))
    c = st.columns(4)
    b = out.breadth
    c[0].metric("Stocks scanned", f"{out.scanned} / {out.universe_size}")
    c[1].metric("Liquidity-eligible", out.eligible)
    c[2].metric("Setups found / passed", f"{len(out.candidates)} / {sum(x.passed for x in out.candidates)}")
    c[3].metric("Breadth A/D", f"{b.advances}/{b.declines}", f"ratio {b.ratio:.2f}" if b.ratio else None)
    st.caption(out.coverage_note + " · " + b.coverage_note)
    mb = reg.market_breadth
    if mb:
        st.caption(f"Market-wide breadth: {mb['advances']} advances / {mb['declines']} declines / "
                   f"{mb['unchanged']} unchanged — {mb['source']}")
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("#### Top-three shortlist")
        if out.selected:
            st.dataframe(pd.DataFrame([{"Rank": i + 1, "Symbol": x.symbol, "Dir": x.plan.direction,
                                        "Strategy": x.signal.strategy_name, "Score": round(x.total, 1),
                                        "Trigger": x.plan.entry_trigger, "Stop": x.plan.stop_loss,
                                        "T1": x.plan.target1, "Qty": x.plan.quantity, "Status": x.plan.status}
                                       for i, x in enumerate(out.selected)]), hide_index=True)
        else:
            no_trade(out.no_trade_reason or "")
    with right:
        st.markdown("#### Sector strength (vs NIFTY, today)")
        if not out.sectors.empty:
            st.plotly_chart(bar_chart(out.sectors, "sector", "rs_1d", height=300), key="dash_sector")
        else:
            st.caption("Sector data unavailable.")
    if out.warnings:
        with st.expander(f"⚠️ {len(out.warnings)} data warnings"):
            for w in out.warnings[:60]:
                st.write("•", w)
    disclaimer()


def market_overview() -> None:
    st.title("🌐 Market Overview")
    p = state.provider()
    data_banner(p.realtime_label.value, p.name)
    if st.button("🔄 Refresh market context", type="primary") or "market_ctx" not in st.session_state:
        from market.regime import compute_regime
        from market.sector_strength import compute_sector_strength

        with st.spinner("Fetching NIFTY 50, BANK NIFTY, VIX and sector indices…"):
            try:
                reg = compute_regime(p)
                sec, w = compute_sector_strength(p, reg.sector_changes_live, reg.nifty_change_live)
                st.session_state["market_ctx"] = (reg, sec, w)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load market data: {exc}")
                return
    reg, sec, w = st.session_state["market_ctx"]
    st.subheader(f"Regime: {reg.regime.value}  (score {reg.score:+.2f})")
    st.caption(" · ".join(reg.evidence))
    c = st.columns(3)
    c[0].metric("India VIX", fmt(reg.vix), help=f"Source: {reg.vix_source or 'n/a'}")
    if reg.market_breadth:
        mb = reg.market_breadth
        c[1].metric("Advances / Declines", f"{mb['advances']} / {mb['declines']}", help=mb["source"])
        c[2].metric("Unchanged", mb["unchanged"])
    else:
        c[1].caption("Market-wide breadth needs the 'Yahoo + NSE website' source.")
    for ix in reg.indices:
        with st.container(border=True):
            c = st.columns(6)
            c[0].metric(ix.name, fmt(ix.ltp), f"{ix.change_pct:+.2f}%" if ix.change_pct is not None else None)
            c[1].metric("Gap", f"{ix.gap_pct:+.2f}%" if ix.gap_pct is not None else "—")
            c[2].metric("Prev close", fmt(ix.prev_close))
            c[3].metric("PDH / PDL", f"{fmt(ix.pdh, 0)} / {fmt(ix.pdl, 0)}")
            c[4].metric("VWAP", ix.vwap_relation or "n/a", help=ix.vwap_source or "Index volume not available")
            c[5].metric("ADX", fmt(ix.adx, 1))
            st.caption(f"Data: {ix.label} · last candle {ix.last_ts} · " + " · ".join(ix.evidence))
    if not sec.empty:
        st.plotly_chart(bar_chart(sec, "index", "rs_1d", "Relative strength vs NIFTY 50 today (%)"), key="mo_rs")
        st.dataframe(sec.round(2), hide_index=True)
    out = st.session_state.get("last_scan")
    if out:
        b = out.breadth
        st.markdown(f"**Breadth (last scan):** {b.advances} advances · {b.declines} declines · {b.unchanged} unchanged "
                    f"— {b.coverage_note}")
    else:
        st.caption("Advance/decline breadth is computed from the stocks you scan (no free full-market breadth feed).")
    with st.expander("NIFTY 50 chart (5m)"):
        try:
            from indicators.compute import compute_all

            cs = p.get_candles("^NSEI", "5m", 4, is_index=True)
            st.plotly_chart(candle_chart(compute_all(cs.df, "5m"), "NIFTY 50 (5m)", show=("ema9", "ema21")),
                            key="mo_nifty")
            st.caption("Index VWAP is shown on the Dashboard via ETF proxy because index candles carry no volume.")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Chart unavailable: {exc}")
    if w or reg.warnings:
        with st.expander("Warnings"):
            for x in list(reg.warnings) + list(w):
                st.write("•", x)
    disclaimer()
