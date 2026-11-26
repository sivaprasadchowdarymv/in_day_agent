"""Run the orchestrator from the UI and keep the outcome in session state."""
from __future__ import annotations

import streamlit as st

from agents.orchestrator import Orchestrator
from agents.schemas import ScanRequest, UniverseRequest
from ui import state


def verified_screenshot_symbols() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in st.session_state.get("screenshots", {}).values():
        ver = item.get("verification")
        if item.get("include", True) and ver and ver.status.startswith("VERIFIED") and ver.symbol:
            ev = item["evidence"]
            summary = {"file": ev.filename, "symbol": ver.symbol, "timeframe": ev.timeframe.value,
                       "screenshot_price": ev.visible_price.value, "trend": ev.trend.value,
                       "verification": ver.status, "discrepancies": ver.discrepancies, "agreements": ver.agreements}
            out.setdefault(ver.symbol, []).append(summary)
    return out


def run_scan(mode: str, limit: int, interval: str, strategies: list[str] | None, fetch_news: bool,
             explain_llm: bool, include_screenshots: bool = True, scan_universe: bool = True) -> None:
    s = state.settings()
    uni = UniverseRequest(mode=mode, master_instruments=st.session_state.get("master"),
                          watchlist=st.session_state.get("watchlist"), limit=limit if scan_universe else 0,
                          index_name=st.session_state.get("index_name", "NIFTY 100"))
    req = ScanRequest(universe=uni, interval=interval,
                      screenshot_symbols=verified_screenshot_symbols() if include_screenshots else {},
                      manual_symbols=list(st.session_state.get("manual_symbols", [])),
                      enabled_strategies=strategies or None, fetch_news=fetch_news, explain_with_llm=explain_llm)
    bar = st.progress(0.0, text="Starting…")
    try:
        out = Orchestrator(state.provider(), s, state.llm()).run(req, lambda f, m: bar.progress(min(f, 1.0), text=m))
        st.session_state["last_scan"] = out
        bar.empty()
    except Exception as exc:  # noqa: BLE001
        bar.empty()
        st.error(f"Scan failed: {type(exc).__name__}: {exc}")
