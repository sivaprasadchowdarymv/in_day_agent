"""Scanner, Combined AI Analysis and Top-3 pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from strategies.strategy_engine import STRATEGY_CLASSES
from ui import state
from ui.components.widgets import (candidates_table, data_banner, disclaimer, download_df, no_trade,
                                   recommendation_card)
from ui.runner import run_scan, verified_screenshot_symbols

INTERVALS = ["5m", "15m", "3m", "1m", "30m", "1h"]


def _universe_controls(prefix: str) -> tuple[str, int]:
    s = state.settings()
    modes = {"seed": "Liquid large-cap seed list (bundled)", "index": "NIFTY index (auto-download)",
             "master_auto": "All NSE EQ stocks (auto-download)", "master": "NSE equity master (upload EQUITY_L.csv)",
             "watchlist": "My watchlist (CSV / typed)"}
    mode = st.radio("Universe", list(modes), format_func=modes.get, horizontal=True, key=f"{prefix}_mode")
    from brokers.factory import nse_client_of
    from brokers.nse_web import INDEX_CONSTITUENTS

    nse = nse_client_of(state.provider())
    if mode in ("index", "master_auto") and nse is None:
        st.warning("Auto-download needs the 'Yahoo + NSE website' data source (Settings). The seed list will be used.")
    if mode == "index":
        st.session_state["index_name"] = st.selectbox("Index", list(INDEX_CONSTITUENTS), index=2, key=f"{prefix}_idx")
        st.caption("Constituents are downloaded from niftyindices.com / NSE archives at scan time.")
    if mode == "master_auto":
        st.caption("Downloads NSE's EQUITY_L.csv (≈2,000 EQ stocks). Use a request budget – full scans are slow on "
                   "free data and every request is rate-limited.")
    if mode == "master":
        up = st.file_uploader("NSE EQUITY_L.csv (nseindia.com → Market Data → Securities available for trading)",
                              type=["csv"], key=f"{prefix}_master")
        if up is not None:
            from data.instrument_master import parse_nse_equity_master

            try:
                inst, rep = parse_nse_equity_master(up.getvalue())
                st.session_state["master"] = inst
                st.success(f"Loaded {rep['final']} EQ instruments (from {rep['total']} rows; ETFs/debt/non-EQ removed).")
            except ValueError as exc:
                st.error(str(exc))
        if not st.session_state.get("master"):
            st.warning("Upload the master file, or the scan will fall back to the seed list.")
    if mode == "watchlist":
        up = st.file_uploader("Watchlist CSV (column 'symbol')", type=["csv"], key=f"{prefix}_wl")
        txt = st.text_input("…or type symbols", ", ".join(st.session_state.get("watchlist", [])), key=f"{prefix}_wlt")
        from data.instrument_master import parse_manual_symbols, parse_watchlist_csv

        syms: list[str] = []
        if up is not None:
            v, r = parse_watchlist_csv(up.getvalue())
            syms += v
            if r:
                st.warning(f"Ignored invalid entries: {', '.join(r[:10])}")
        v, r = parse_manual_symbols(txt)
        syms += v
        st.session_state["watchlist"] = list(dict.fromkeys(syms))
    total = {"seed": len(state.seed_universe_map()), "master": len(st.session_state.get("master") or []),
             "watchlist": len(st.session_state.get("watchlist") or []),
             "index": int(st.session_state.get("index_name", "NIFTY 100").split()[-1])
             if st.session_state.get("index_name", "NIFTY 100").split()[-1].isdigit() else 100,
             "master_auto": 2000}[mode] or len(state.seed_universe_map())
    limit = st.slider("Max symbols to scan (API request budget)", 5, max(total, 5),
                      min(s.scan.max_symbols_per_scan, max(total, 5)), key=f"{prefix}_limit")
    if limit < total:
        st.caption(f"⚠️ Partial scan: {limit} of {total}. Results will say so explicitly.")
    return mode, limit


def scanner_page() -> None:
    st.title("🔎 Automatic Stock Scanner")
    s = state.settings()
    data_banner(state.provider().realtime_label.value, state.provider().name)
    with st.container(border=True):
        mode, limit = _universe_controls("scan")
        c = st.columns(3)
        interval = c[0].selectbox("Candle interval", INTERVALS, index=INTERVALS.index(s.scan.interval)
                                  if s.scan.interval in INTERVALS else 0)
        strategies = c[1].multiselect("Strategies", [x.key for x in STRATEGY_CLASSES],
                                      default=[x.key for x in STRATEGY_CLASSES],
                                      format_func=lambda k: next(x.name for x in STRATEGY_CLASSES if x.key == k))
        news = c[2].toggle("Check news / corporate events", value=False,
                           help="Free RSS sources; news only adds warnings, never raises a score.")
        llm_ok = state.llm().available()
        explain = c[2].toggle("AI-written explanations", value=False, disabled=not llm_ok,
                              help="Requires a free Gemini/Groq/OpenRouter key in Settings")
        inc = st.toggle(f"Include verified screenshot symbols ({len(verified_screenshot_symbols())})", value=True)
        if st.button("▶️ Run scan", type="primary"):
            run_scan(mode, limit, interval, strategies, news, explain, inc)
    out = st.session_state.get("last_scan")
    if not out:
        disclaimer()
        return
    data_banner(out.data_label, f"Regime: {out.regime.regime.value}")
    st.caption(out.coverage_note)
    c = st.columns(4)
    c[0].metric("Scanned", out.scanned)
    c[1].metric("Liquidity-eligible", out.eligible)
    c[2].metric("Setups found", len(out.candidates))
    c[3].metric("Passed all checks", sum(x.passed for x in out.candidates))
    df = candidates_table(out.candidates)
    if not df.empty:
        f = st.radio("Show", ["All", "Passed only", "Rejected only"], horizontal=True)
        if f == "Passed only":
            df = df[df["Passed"] == "✅"]
        elif f == "Rejected only":
            df = df[df["Passed"] == "❌"]
        st.dataframe(df.sort_values("Score", ascending=False), hide_index=True)
        download_df(df, "candidates.csv")
    else:
        st.info("No strategy produced a setup on the scanned stocks.")
    with st.expander("Stocks skipped by liquidity / data filters"):
        rows = [{"Symbol": a.symbol, "Reason": a.skipped_reason,
                 "Avg value ₹Cr": round(a.liquidity.avg_traded_value_cr, 1) if a.liquidity and
                 a.liquidity.avg_traded_value_cr else None,
                 "ATR %": round(a.liquidity.atr_pct, 2) if a.liquidity and a.liquidity.atr_pct else None}
                for a in out.analyses if a.skipped_reason]
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    disclaimer()


def combined_page() -> None:
    st.title("🧠 Combined AI Analysis")
    st.write("Merges automatic scanner candidates, **verified** screenshot symbols and manually added symbols, then "
             "re-computes everything on fresh market data before ranking.")
    s = state.settings()
    shots = verified_screenshot_symbols()
    c = st.columns(3)
    c[0].metric("Verified screenshot symbols", len(shots))
    c[1].metric("Manual symbols", len(st.session_state.get("manual_symbols", [])))
    c[2].metric("Universe scan limit", s.scan.max_symbols_per_scan)
    from data.instrument_master import parse_manual_symbols

    txt = st.text_input("Add symbols manually (comma separated)", ", ".join(st.session_state.get("manual_symbols", [])))
    v, r = parse_manual_symbols(txt)
    st.session_state["manual_symbols"] = v
    if r:
        st.warning(f"Invalid: {', '.join(r)}")
    scan_uni = st.toggle("Also scan the automatic universe", value=True)
    if st.button("▶️ Run combined analysis", type="primary"):
        run_scan("seed", s.scan.max_symbols_per_scan, s.scan.interval, None, False, False, True, scan_uni)
    out = st.session_state.get("last_scan")
    if not out:
        disclaimer()
        return
    st.markdown("#### Workflow state transitions")
    st.dataframe(pd.DataFrame(out.state_log), hide_index=True)
    st.markdown("#### Candidates by origin")
    df = candidates_table(out.candidates)
    if not df.empty:
        st.dataframe(df.groupby(["Origin", "Passed"]).size().rename("count").reset_index(), hide_index=True)
        st.dataframe(df.sort_values("Score", ascending=False), hide_index=True)
    if shots:
        with st.expander("Screenshot evidence used"):
            st.json(shots, expanded=False)
    disclaimer()


def top3_page() -> None:
    st.title("🏆 Top 3 Recommendations")
    out = st.session_state.get("last_scan")
    if not out:
        st.info("Run a scan first (Scanner or Combined AI Analysis).")
        disclaimer()
        return
    data_banner(out.data_label, f"Scan #{out.run_id} · regime {out.regime.regime.value}")
    if not out.account_gate.allowed:
        st.error("Risk limits active: " + "; ".join(out.account_gate.reasons))
    if not out.selected:
        no_trade(out.no_trade_reason or "")
        near = sorted(out.candidates, key=lambda c: -c.total)[:5]
        if near:
            with st.expander("Closest setups and why they were rejected"):
                for c in near:
                    st.markdown(f"**{c.symbol}** · {c.signal.strategy_name} · score {c.total:.0f}")
                    for r in c.rejections:
                        st.markdown(f"- ❌ {r}")
        disclaimer()
        return
    st.caption(f"{len(out.selected)} setup(s) passed every mandatory check. Fewer than three is normal – the system "
               "never forces recommendations.")

    def paper_button(c):
        if st.button(f"📝 Paper-trade {c.symbol}", key=f"pt_{c.symbol}"):
            from paper_trading.engine import PaperTradingEngine

            try:
                tid = PaperTradingEngine(state.provider(), state.settings()).create_from_plan(
                    c.plan, c.sector, getattr(c, "recommendation_id", None))
                st.success(f"Paper order #{tid} created (simulated – no real order).")
            except ValueError as exc:
                st.error(str(exc))

    for i, c in enumerate(out.selected, 1):
        recommendation_card(i, c, out.regime.regime.value, out.explanations.get(c.symbol), paper_button)
        if c.news:
            with st.expander(f"📰 Recent news for {c.symbol} (does not affect score)"):
                for n in c.news:
                    st.markdown(f"- [{n['title']}]({n['link']}) — {n['source']}, {n['published']} · {n['event_type']}")
    disclaimer()
