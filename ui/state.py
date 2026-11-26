"""Shared Streamlit state: settings, provider, LLM, universe, last scan."""
from __future__ import annotations

import os
import uuid

import streamlit as st

from config.logging_setup import setup_logging
from config.settings import Settings, load_settings


def init_app() -> None:
    if st.session_state.get("_init"):
        return
    _secrets_to_env()
    base = load_settings()
    setup_logging(base.app.log_level)
    from database.db import init_db, load_saved_settings

    init_db(base.app.database_url or None)
    s = load_saved_settings(base)
    st.session_state["settings"] = s
    st.session_state["session_id"] = uuid.uuid4().hex
    st.session_state.setdefault("screenshots", {})  # image_id -> dict(evidence, verification, img, include)
    st.session_state.setdefault("manual_symbols", [])
    st.session_state.setdefault("watchlist", [])
    st.session_state.setdefault("master", None)
    st.session_state.setdefault("last_scan", None)
    try:  # NSE holiday calendar (free source); cached on disk, never blocks start-up
        from brokers.factory import nse_client_of
        from data.market_extras import refresh_holidays

        n, _ = refresh_holidays(nse_client_of(provider()) if s.app.data_provider == "yahoo_nse" else None)
        st.session_state["holidays_loaded"] = n
    except Exception:  # noqa: BLE001
        st.session_state["holidays_loaded"] = 0
    tcmd = os.getenv("TESSERACT_CMD")
    if tcmd:
        from vision.ocr import configure_tesseract

        configure_tesseract(tcmd)
    st.session_state["_init"] = True


def _secrets_to_env() -> None:
    """Cloud hosting: copy top-level Streamlit secrets (e.g. GEMINI_API_KEY, DATABASE_URL)
    into environment variables so the app reads them the same way as a local .env.
    Values are never displayed or logged."""
    try:
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float, bool)) and not os.getenv(k):
                os.environ[k] = str(v)
    except Exception:  # noqa: BLE001 - no secrets file is normal locally
        pass


def settings() -> Settings:
    return st.session_state["settings"]


@st.cache_resource(show_spinner=False)
def _provider(kind: str, delay: float, demo_clock: str):
    from brokers.factory import make_provider

    s = Settings()
    s.app.data_provider = kind
    s.app.demo_clock = demo_clock if kind == "simulated" else ""
    s.scan.request_delay_sec = delay
    return make_provider(s)


def provider():
    s = settings()
    return _provider(s.app.data_provider, s.scan.request_delay_sec, s.app.demo_clock)


def llm():
    from llm.providers import make_llm

    s = settings()
    return make_llm(s.app.llm_provider, s.app.llm_model, s.app.llm_vision_model)


@st.cache_data(show_spinner=False)
def seed_universe_map() -> dict[str, str]:
    from data.instrument_master import load_seed_universe

    return {i.symbol: i.name for i in load_seed_universe()}


@st.cache_data(show_spinner=False)
def seed_sectors() -> dict[str, str]:
    from data.instrument_master import load_seed_universe

    return {i.symbol: i.sector for i in load_seed_universe()}


def universe_map() -> dict[str, str]:
    m = dict(seed_universe_map())
    master = st.session_state.get("master")
    if master:
        m.update({i.symbol: i.name for i in master})
    return m
