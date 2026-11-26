"""Screenshot Upload Lab."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from config.settings import STORAGE_DIR
from ui import state
from ui.components.widgets import disclaimer, fmt

FIELDS = ["symbol", "exchange", "timeframe", "visible_price", "timestamp", "ema_lines", "vwap", "rsi", "macd",
          "support_levels", "resistance_levels", "patterns", "trend"]


def _evidence_table(ev) -> pd.DataFrame:
    rows = []
    for f in FIELDS:
        o = getattr(ev, f)
        rows.append({"Field": f, "Value": "—" if o.value in (None, [], "") else str(o.value),
                     "Confidence": round(o.confidence, 2), "Source": o.source,
                     "Flag": "⚠️" if o.flagged else "", "Note": o.note})
    return pd.DataFrame(rows)


def _persist(img, ev, ver=None) -> None:
    from sqlalchemy import select

    from database.db import session_scope
    from database.models import ImageEvidence, ScreenshotRow

    sid = st.session_state["session_id"]
    folder = STORAGE_DIR / "screenshots"
    folder.mkdir(exist_ok=True)
    path = folder / f"{img.sha256[:16]}{Path(img.filename).suffix or '.img'}"
    if not path.exists():
        path.write_bytes(img.data)
    with session_scope() as s:
        row = s.execute(select(ScreenshotRow).where(ScreenshotRow.session_id == sid,
                                                    ScreenshotRow.sha256 == img.sha256)).scalar_one_or_none()
        if row is None:
            row = ScreenshotRow(session_id=sid, filename=img.filename, sha256=img.sha256, width=img.width,
                                height=img.height, size_bytes=len(img.data), stored_path=str(path))
            s.add(row)
            s.flush()
        s.add(ImageEvidence(screenshot_id=row.id, evidence=ev.model_dump(),
                            verification=ver.model_dump() if ver else {}))


def screenshot_page() -> None:
    st.title("📷 Screenshot Upload Lab")
    s = state.settings()
    from vision.ocr import tesseract_available

    llm = state.llm()
    c = st.columns(3)
    c[0].metric("OCR (Tesseract)", "Ready" if tesseract_available() else "Not installed")
    c[1].metric("Vision AI", f"{s.app.llm_provider}" if llm.available() else "Off", help="When off, OCR + OpenCV only")
    c[2].metric("Images in session", len(st.session_state["screenshots"]))
    if not tesseract_available():
        st.warning("Install Tesseract OCR to read text from charts (see README → Screenshot setup).")
    st.info("Screenshot prices are **never** used as live prices. Every detected symbol is re-validated and all "
            "indicators are recomputed on fresh market data before it can become a candidate.")
    ups = st.file_uploader("Upload chart screenshots (PNG, JPG/JPEG, WEBP)", type=["png", "jpg", "jpeg", "webp"],
                           accept_multiple_files=True)
    use_llm = st.toggle("Also ask the vision AI (if configured)", value=llm.available(), disabled=not llm.available())
    if ups and st.button("🔍 Analyse uploaded images", type="primary"):
        from agents.agents import ScreenshotVisionAgent
        from vision.image_preprocessor import ImageValidationError, load_and_validate

        agent = ScreenshotVisionAgent(state.universe_map(), llm)
        known = {v["img"].sha256 for v in st.session_state["screenshots"].values()}
        prog = st.progress(0.0)
        for k, up in enumerate(ups):
            prog.progress((k + 1) / len(ups), text=f"Analysing {up.name}…")
            try:
                img = load_and_validate(up.getvalue(), up.name, s.app.max_upload_mb)
            except ImageValidationError as exc:
                st.error(f"{up.name}: {exc}")
                continue
            if img.sha256 in known:
                st.caption(f"{up.name}: already analysed (duplicate skipped)")
                continue
            r = agent.run((img, use_llm))
            if not r.ok:
                st.error(f"{up.name}: analysis failed – {'; '.join(r.errors)}")
                continue
            ev = r.output
            st.session_state["screenshots"][ev.image_id] = {"img": img, "evidence": ev, "verification": None,
                                                            "include": True, "override": None}
            _persist(img, ev)
        prog.empty()

    shots = st.session_state["screenshots"]
    if not shots:
        disclaimer()
        return
    b = st.columns(3)
    if b[0].button("✅ Verify all against market data"):
        from vision.verification import verify_evidence

        with st.spinner("Fetching fresh candles and comparing…"):
            for item in shots.values():
                ev = item["evidence"]
                item["verification"] = verify_evidence(ev, state.provider(), set(state.universe_map()),
                                                       item.get("override"), s.scan.interval)
                _persist(item["img"], ev, item["verification"])
    if b[1].button("🗑️ Remove all images"):
        st.session_state["screenshots"] = {}
        st.rerun()
    compare = b[2].multiselect("Compare images", list(shots), format_func=lambda k: shots[k]["evidence"].filename)

    for key, item in list(shots.items()):
        ev, ver = item["evidence"], item["verification"]
        with st.container(border=True):
            top = st.columns([1.2, 2])
            with top[0]:
                st.image(item["img"].rgb, caption=f"{ev.filename} · readability {ev.readability:.2f}")
                st.caption(ev.readability_note)
            with top[1]:
                st.dataframe(_evidence_table(ev), hide_index=True, height=300)
                for f in ev.flags:
                    st.caption(f"⚠️ {f}")
                if ev.llm_note:
                    st.caption(f"Vision AI: {ev.llm_note}")
            ctl = st.columns([1.3, 1, 1, 1])
            choices = ["(as detected)"] + sorted(set(ev.symbol_candidates) | set(state.universe_map()))
            cur = item.get("override") or "(as detected)"
            sel = ctl[0].selectbox("Symbol (confirm / correct)", choices,
                                   index=choices.index(cur) if cur in choices else 0, key=f"ov_{key}")
            item["override"] = None if sel == "(as detected)" else sel
            item["include"] = ctl[1].checkbox("Use in analysis", value=item.get("include", True), key=f"inc_{key}")
            if ctl[2].button("Verify", key=f"ver_{key}"):
                from vision.verification import verify_evidence

                item["verification"] = verify_evidence(ev, state.provider(), set(state.universe_map()),
                                                       item.get("override"), s.scan.interval)
                _persist(item["img"], ev, item["verification"])
                st.rerun()
            if ctl[3].button("Remove", key=f"rm_{key}"):
                del shots[key]
                st.rerun()
            if ver:
                color = {"VERIFIED": "green", "VERIFIED_WITH_DISCREPANCIES": "orange", "UNVERIFIED": "red"}[ver.status]
                st.markdown(f"**Verification:** :{color}[{ver.status}] · symbol **{ver.symbol}** · current price "
                            f"{fmt(ver.current_price)} ({ver.current_price_label or 'n/a'}, {str(ver.current_price_ts)[:16]}) · "
                            f"interval {ver.interval_used}")
                for x in ver.reasons:
                    st.markdown(f"- ❌ {x}")
                for x in ver.discrepancies:
                    st.markdown(f"- ⚠️ {x}")
                for x in ver.agreements:
                    st.markdown(f"- ✅ {x}")
    if compare:
        st.markdown("#### Comparison")
        comp = {shots[k]["evidence"].filename: _evidence_table(shots[k]["evidence"]).set_index("Field")["Value"]
                for k in compare}
        st.dataframe(pd.DataFrame(comp))
    from ui.runner import verified_screenshot_symbols

    vs = verified_screenshot_symbols()
    st.success(f"Verified symbols ready for analysis: {', '.join(vs) or 'none'} → run **Combined AI Analysis**.")
    disclaimer()
