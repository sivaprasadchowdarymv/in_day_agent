"""BharatTrade AI – Streamlit entry point.  Run:  streamlit run app.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="BharatTrade AI", page_icon="📈", layout="wide")

from ui import state  # noqa: E402
from ui.components.widgets import inject_css  # noqa: E402
from ui.pages.analysis import chart_page, strategy_page  # noqa: E402
from ui.pages.backtest_page import backtest_page  # noqa: E402
from ui.pages.overview import dashboard, market_overview  # noqa: E402
from ui.pages.scanner import combined_page, scanner_page, top3_page  # noqa: E402
from ui.pages.screenshots import screenshot_page  # noqa: E402
from ui.pages.settings_page import settings_page  # noqa: E402
from ui.pages.trading import journal_page, paper_page, performance_page, risk_page  # noqa: E402

state.init_app()
inject_css()

with st.sidebar:
    s = state.settings()
    st.markdown("### 📈 BharatTrade AI")
    st.caption("Evidence-driven NSE intraday research · paper trading only")
    from data.market_hours import market_status, now_ist

    st.markdown(f"**{now_ist():%a %d %b, %H:%M} IST** · Market: **{market_status()}**")
    label = state.provider().realtime_label.value
    st.markdown(f"Data: **{state.provider().name}** · `{label}`")
    if s.app.data_provider == "simulated" and s.app.demo_clock:
        st.markdown(f"🕒 Demo clock: replaying **{s.app.demo_clock}** IST")
    st.markdown(f"Capital **₹{s.profile.capital:,.0f}** · risk/trade **₹{s.profile.risk_budget:,.0f}**")

pages = {
    "Overview": [
        st.Page(dashboard, title="Dashboard", icon="📊", url_path="dashboard", default=True),
        st.Page(market_overview, title="Market Overview", icon="🌐", url_path="market"),
    ],
    "Discover": [
        st.Page(scanner_page, title="Automatic Stock Scanner", icon="🔎", url_path="scanner"),
        st.Page(screenshot_page, title="Screenshot Upload Lab", icon="📷", url_path="screenshots"),
        st.Page(combined_page, title="Combined AI Analysis", icon="🧠", url_path="combined"),
        st.Page(top3_page, title="Top 3 Recommendations", icon="🏆", url_path="top3"),
    ],
    "Analyse": [
        st.Page(chart_page, title="Technical Chart Analysis", icon="📈", url_path="chart"),
        st.Page(strategy_page, title="Strategy Explorer", icon="🧩", url_path="strategies"),
        st.Page(backtest_page, title="Backtesting", icon="🧪", url_path="backtest"),
    ],
    "Trade (simulated)": [
        st.Page(risk_page, title="Risk Management", icon="🛡️", url_path="risk"),
        st.Page(paper_page, title="Paper Trading", icon="📝", url_path="paper"),
        st.Page(journal_page, title="Trade Journal", icon="📓", url_path="journal"),
        st.Page(performance_page, title="Performance Analytics", icon="📉", url_path="performance"),
    ],
    "System": [st.Page(settings_page, title="Settings & API Configuration", icon="⚙️", url_path="settings")],
}
st.navigation(pages, expanded=True).run()
