"""Settings & API configuration, connection tests."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from config.settings import get_secret
from ui import state
from ui.components.widgets import disclaimer


def settings_page() -> None:
    st.title("⚙️ Settings & API Configuration")
    s = state.settings()
    tab = st.tabs(["Data source", "AI providers", "Scanner & scoring", "Costs", "Maintenance"])

    with tab[0]:
        st.markdown("#### Market data provider")
        opts = {"yahoo_nse": "Yahoo Finance candles + NSE website quotes/breadth/circuits (free, recommended)",
                "yahoo": "Yahoo Finance only (free, DELAYED, no key)",
                "simulated": "Simulated demo data (NOT market data)"}
        choice = st.radio("Provider", list(opts), format_func=opts.get,
                          index=list(opts).index(s.app.data_provider) if s.app.data_provider in opts else 0)
        if choice == "simulated":
            st.error("Simulated mode generates synthetic prices for learning and testing. Every screen will show a "
                     "red SIMULATED banner. Never trade from simulated output.")
        if s.app.data_provider == "simulated":
            clk = st.text_input("Demo clock HH:MM (replay the last session at this time; blank = real clock)",
                                s.app.demo_clock, help="Lets you explore the full workflow outside market hours.")
            if clk != s.app.demo_clock and st.button("Apply demo clock"):
                try:
                    from data.market_hours import parse_hhmm

                    if clk:
                        parse_hhmm(clk)
                    s.app.demo_clock = clk
                    from database.db import save_settings

                    save_settings(s)
                    st.session_state["last_scan"] = None
                    st.rerun()
                except ValueError:
                    st.error("Use HH:MM, e.g. 11:30")
        if choice != s.app.data_provider and st.button("Apply provider"):
            s.app.data_provider = choice
            from database.db import audit, save_settings

            save_settings(s)
            audit("provider_changed", {"provider": choice})
            st.session_state.pop("market_ctx", None)
            st.session_state["last_scan"] = None
            st.rerun()
        st.markdown("#### Connection test")
        if st.button("🔌 Test connection", type="primary"):
            with st.spinner("Requesting NIFTY 50 daily candles…"):
                res = state.provider().test_connection()
            (st.success if res.get("ok") else st.error)(
                "Connected" if res.get("ok") else "Connection failed – no data was substituted.")
            st.json(res)
        st.markdown("#### Data sources health")
        if st.button("🩺 Test all free data sources"):
            _sources_health()
        st.caption("The provider is isolated behind `brokers/base.py::MarketDataProvider`. A licensed real-time feed "
                   "(any broker API) can be added later by implementing that interface – strategy code does not change.")

    with tab[1]:
        st.markdown("#### Optional free AI providers")
        st.caption("Used only to read screenshots and to rephrase explanations. Keys are read from environment "
                   "variables (.env) and are never shown, logged or stored in the database.")
        keys = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
        st.dataframe(pd.DataFrame([{"Provider": k, "Env variable": v, "Key present": "✅" if get_secret(v) else "—"}
                                   for k, v in keys.items()]), hide_index=True)
        with st.form("llm"):
            prov = st.selectbox("Provider", ["none", "gemini", "groq", "openrouter"],
                                index=["none", "gemini", "groq", "openrouter"].index(s.app.llm_provider)
                                if s.app.llm_provider in ("none", "gemini", "groq", "openrouter") else 0)
            model = st.text_input("Text model (blank = default)", s.app.llm_model)
            vmodel = st.text_input("Vision model (blank = default)", s.app.llm_vision_model)
            sess_key = st.text_input("Or paste a key for THIS SESSION only (not saved)", type="password")
            if st.form_submit_button("Save AI settings"):
                s.app.llm_provider, s.app.llm_model, s.app.llm_vision_model = prov, model, vmodel
                if sess_key and prov in keys:
                    os.environ[keys[prov]] = sess_key.strip()
                from database.db import save_settings

                save_settings(s)
                st.success("Saved (key, if pasted, kept in memory only).")
        if st.button("Test AI provider"):
            from llm.providers import LLMError

            llm = state.llm()
            if not llm.available():
                st.warning("Provider not configured.")
            else:
                try:
                    r = llm.generate("Reply with the single word OK.", max_tokens=10)
                    st.success(f"{r.provider}:{r.model} → {r.text.strip()[:50]}")
                except LLMError as exc:
                    st.error(str(exc))
        from vision.ocr import tesseract_available

        st.markdown(f"**Tesseract OCR:** {'✅ found' if tesseract_available() else '❌ not found – see README'}")

    with tab[2]:
        sc, w = s.scan, s.weights
        with st.form("scan"):
            c = st.columns(4)
            sc.interval = c[0].selectbox("Default interval", ["5m", "15m", "3m", "1m", "30m", "1h"],
                                         index=["5m", "15m", "3m", "1m", "30m", "1h"].index(sc.interval))
            sc.max_symbols_per_scan = int(c[1].number_input("Max symbols per scan", 5, 2000, sc.max_symbols_per_scan))
            sc.request_delay_sec = c[2].number_input("Delay between requests (s)", 0.0, 5.0, float(sc.request_delay_sec))
            sc.min_score = c[3].number_input("Minimum score", 0.0, 100.0, float(sc.min_score))
            c = st.columns(4)
            sc.min_avg_traded_value_cr = c[0].number_input("Min avg traded value (₹ Cr)", 0.0, 1000.0,
                                                           float(sc.min_avg_traded_value_cr))
            sc.min_avg_volume = c[1].number_input("Min avg volume", 0.0, 1e8, float(sc.min_avg_volume))
            sc.min_price = c[2].number_input("Min price ₹", 1.0, 10000.0, float(sc.min_price))
            sc.max_price = c[3].number_input("Max price ₹", 10.0, 100000.0, float(sc.max_price))
            c = st.columns(4)
            sc.min_atr_pct = c[0].number_input("Min daily ATR %", 0.0, 10.0, float(sc.min_atr_pct))
            sc.max_atr_pct = c[1].number_input("Max daily ATR %", 0.5, 20.0, float(sc.max_atr_pct))
            sc.max_spread_pct = c[2].number_input("Max live spread %", 0.01, 2.0, float(sc.max_spread_pct))
            sc.stale_after_minutes = int(c[3].number_input("Stale after (min)", 5, 120, sc.stale_after_minutes))
            st.markdown("**Scoring weights** (normalised to 100)")
            c = st.columns(7)
            for col, k in zip(c, ["trend", "momentum", "vwap", "relative_volume", "price_action", "market_sector",
                                  "liquidity"]):
                setattr(w, k, col.number_input(k.replace("_", " "), 0.0, 100.0, float(getattr(w, k))))
            if st.form_submit_button("Save", type="primary"):
                from database.db import save_settings

                save_settings(s)
                st.success("Saved.")
        st.caption("Normalised weights: " + ", ".join(f"{k} {v:.1f}" for k, v in w.normalised().items()))

    with tab[3]:
        c_ = s.costs
        with st.form("costs"):
            st.caption("Defaults: Groww intraday schedule as published Nov 2026 – verify on groww.in/pricing.")
            c = st.columns(4)
            c_.brokerage_flat = c[0].number_input("Brokerage flat ₹/order", 0.0, 100.0, float(c_.brokerage_flat))
            c_.brokerage_pct = c[1].number_input("Brokerage % (lower of)", 0.0, 1.0, float(c_.brokerage_pct), format="%.4f")
            c_.brokerage_min = c[2].number_input("Min brokerage ₹", 0.0, 50.0, float(c_.brokerage_min))
            c_.stt_sell_pct = c[3].number_input("STT % (sell)", 0.0, 1.0, float(c_.stt_sell_pct), format="%.4f")
            c = st.columns(4)
            c_.stamp_buy_pct = c[0].number_input("Stamp duty % (buy)", 0.0, 1.0, float(c_.stamp_buy_pct), format="%.4f")
            c_.exchange_txn_pct = c[1].number_input("Exchange txn %", 0.0, 1.0, float(c_.exchange_txn_pct), format="%.5f")
            c_.sebi_pct = c[2].number_input("SEBI %", 0.0, 1.0, float(c_.sebi_pct), format="%.5f")
            c_.gst_pct = c[3].number_input("GST %", 0.0, 30.0, float(c_.gst_pct))
            c = st.columns(2)
            c_.slippage_bps_per_side = c[0].number_input("Slippage bps / side", 0.0, 50.0, float(c_.slippage_bps_per_side))
            c_.default_half_spread_bps = c[1].number_input("Assumed half-spread bps", 0.0, 50.0,
                                                           float(c_.default_half_spread_bps))
            if st.form_submit_button("Save costs", type="primary"):
                from database.db import save_settings

                save_settings(s)
                st.success("Saved.")

    with tab[4]:
        from database.db import apply_retention, default_url, session_scope

        st.write("Database:", s.app.database_url or default_url())
        days = st.number_input("Retention (days) for snapshots & audit logs", 7, 3650, int(s.app.retention_days))
        if st.button("Apply retention policy"):
            st.json(apply_retention(int(days)))
            from data.cache import CandleCache

            st.write("Old intraday candles purged:", CandleCache().purge_older_than(int(days)))
        st.download_button("⬇️ Export settings JSON", s.to_json(), "bharattrade_settings.json", "application/json")
        from sqlalchemy import select

        from database.models import AuditLog

        with session_scope() as ss:
            rows = ss.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(100)).scalars().all()
            st.dataframe(pd.DataFrame([{"ts": r.created_at, "action": r.action, "details": r.details} for r in rows]),
                         hide_index=True)
    disclaimer()


def _sources_health() -> None:
    """Probe every free source and show what works on this network right now."""
    import time as _t

    from brokers.nse_web import NSEWebClient

    rows = []

    def probe(name, fn):
        t0 = _t.perf_counter()
        try:
            detail = fn()
            rows.append({"Source": name, "OK": "✅", "Detail": str(detail)[:120],
                         "ms": int((_t.perf_counter() - t0) * 1000)})
        except Exception as exc:  # noqa: BLE001
            rows.append({"Source": name, "OK": "❌", "Detail": f"{type(exc).__name__}: {str(exc)[:100]}",
                         "ms": int((_t.perf_counter() - t0) * 1000)})

    with st.spinner("Probing sources (rate-limited, ~30 s)…"):
        from brokers.yahoo_client import YahooFinanceProvider

        y = YahooFinanceProvider(request_delay_sec=0.3)
        probe("Yahoo – NIFTY 50 daily", lambda: f"{len(y.get_candles('^NSEI', '1d', 5, is_index=True).df)} candles")
        probe("Yahoo – RELIANCE 5m", lambda: f"last {y.get_candles('RELIANCE', '5m', 2).last_ts}")
        n = NSEWebClient(min_interval_sec=1.0)
        probe("NSE – market status", lambda: n.market_status())
        probe("NSE – quote RELIANCE", lambda: (lambda q: f"LTP {q.ltp} at {q.last_update}, band "
                                                         f"{q.lower_band}-{q.upper_band}")(n.quote("RELIANCE")))
        probe("NSE – order book & delivery", lambda: (lambda t: f"bid {t.best_bid} ask {t.best_ask} "
                                                               f"delivery {t.delivery_pct}%")(n.trade_info("RELIANCE")))
        probe("NSE – all indices / breadth", lambda: f"{len(n.all_indices())} indices")
        probe("NSE – holidays", lambda: f"{len(n.holidays())} trading holidays")
        probe("NSE – price-band hitters", lambda: {k: len(v) for k, v in n.price_band_hitters().items()})
        probe("NIFTY 50 constituents", lambda: f"{len(n.index_constituents('NIFTY 50'))} stocks")
        probe("NSE equity master (EQUITY_L.csv)", lambda: f"{len(n.equity_master_csv().splitlines())} lines")
        probe("NSE bulk deals", lambda: f"{len(n.bulk_deals())} rows")
        from news.news_engine import test_sources

        try:
            for r in test_sources():
                rows.append({"Source": f"News – {r['source']}", "OK": "✅" if r["ok"] else "❌",
                             "Detail": r["error"] or f"{r['items']} items, latest {r['latest']}", "ms": None})
        except Exception as exc:  # noqa: BLE001
            rows.append({"Source": "News feeds", "OK": "❌", "Detail": str(exc)[:100], "ms": None})
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("❌ means that source is not used right now – the app falls back or reports 'unavailable'; it never "
               "substitutes made-up values. NSE website endpoints are unofficial and may block automated access.")
