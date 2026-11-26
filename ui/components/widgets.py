"""Reusable UI pieces: banners, metric tiles, recommendation cards, tables."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

LABEL_STYLE = {
    "LIVE": ("#0f5132", "#d1e7dd", "LIVE data"),
    "DELAYED": ("#664d03", "#fff3cd", "DELAYED data (free source, typically up to ~15 min behind)"),
    "HISTORICAL": ("#41464b", "#e2e3e5", "HISTORICAL data – not current"),
    "SIMULATED": ("#842029", "#f8d7da", "SIMULATED demo data – NOT real market prices"),
    "UNAVAILABLE": ("#842029", "#f8d7da", "Data UNAVAILABLE"),
}

CSS = """
<style>
.block-container {padding-top: 1.6rem; max-width: 1400px;}
.bt-banner {border-radius: 10px; padding: 10px 14px; margin: 4px 0 12px 0; font-weight: 600; font-size: 0.92rem;}
.bt-card {border: 1px solid rgba(128,128,128,.25); border-radius: 14px; padding: 18px 20px; margin-bottom: 18px;
          background: rgba(128,128,128,.04);}
.bt-card h3 {margin: 0 0 4px 0;}
.bt-pill {display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 700;
          margin-right: 6px;}
.bt-long {background:#d1e7dd; color:#0f5132;} .bt-short {background:#f8d7da; color:#842029;}
.bt-neutral {background:#e2e3e5; color:#41464b;}
.bt-notrade {border: 2px solid #dc3545; border-radius: 14px; padding: 22px; text-align:center; font-size: 1.3rem;
             font-weight: 800; color: #dc3545; margin: 10px 0 18px 0;}
.bt-kv {display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 8px 18px; margin-top: 10px;}
.bt-kv div span {display:block; font-size:.72rem; opacity:.7; text-transform: uppercase; letter-spacing: .03em;}
.bt-kv div b {font-size: 1.02rem;}
.bt-small {font-size: .8rem; opacity: .75;}
@media (max-width: 900px) {.bt-kv {grid-template-columns: repeat(2, minmax(0,1fr));}}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def data_banner(label: str, extra: str = "") -> None:
    parts = [p for p in (label or "UNAVAILABLE").split("/") if p]
    key = "SIMULATED" if "SIMULATED" in parts else (parts[0] if parts else "UNAVAILABLE")
    fg, bg, text = LABEL_STYLE.get(key, LABEL_STYLE["UNAVAILABLE"])
    st.markdown(f'<div class="bt-banner" style="color:{fg};background:{bg}">📡 {html.escape(text)}'
                f'{" · " + html.escape(extra) if extra else ""}</div>', unsafe_allow_html=True)


def disclaimer() -> None:
    st.caption("Research and paper-trading tool only. Not investment advice. Scores rank setup quality and are not "
               "probabilities of profit. Costs are estimates – your contract note is authoritative. No live orders "
               "are placed by this application.")


def no_trade(reason: str) -> None:
    st.markdown(f'<div class="bt-notrade">⛔ NO VALID INTRADAY OPPORTUNITY — DO NOT TRADE.'
                f'<div class="bt-small" style="color:inherit;font-weight:500;margin-top:6px">'
                f'{html.escape(reason.replace("NO VALID INTRADAY OPPORTUNITY — DO NOT TRADE.", "").strip())}</div>'
                f'</div>', unsafe_allow_html=True)


def fmt(v, nd=2, prefix=""):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, (int, float)):
        return f"{prefix}{v:,.{nd}f}"
    return str(v)


def kv_grid(items: list[tuple[str, str]]) -> str:
    cells = "".join(f"<div><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>" for k, v in items)
    return f'<div class="bt-kv">{cells}</div>'


def recommendation_card(rank: int, c, regime_label: str, explanation: tuple[dict, str] | None,
                        on_paper=None) -> None:
    p, sig = c.plan, c.signal
    d = p.direction
    pill = "bt-long" if d == "LONG" else "bt-short"
    head = (f'<div class="bt-card"><h3>#{rank} · {html.escape(c.name)} '
            f'<span class="bt-small">NSE: {html.escape(c.symbol)}</span></h3>'
            f'<span class="bt-pill {pill}">{d}</span><span class="bt-pill bt-neutral">{html.escape(sig.strategy_name)}</span>'
            f'<span class="bt-pill bt-neutral">{html.escape(p.status)}</span>'
            f'<span class="bt-pill bt-neutral">Score {c.total:.0f}/100</span>'
            f'<span class="bt-pill bt-neutral">Origin: {html.escape(c.origin)}</span>')
    items = [
        ("Market regime", regime_label), ("Sector", c.sector),
        ("Verified LTP", f"{fmt(p.current_price)} ({p.price_label})"), ("Last data timestamp", str(p.price_timestamp)[:19]),
        ("Entry trigger", fmt(p.entry_trigger)), ("Entry range", f"{fmt(p.entry_trigger)} – {fmt(p.entry_limit)}"),
        ("Stop-loss", fmt(p.stop_loss)), ("Target 1", fmt(p.target1)),
        ("Target 2", fmt(p.target2)), ("Target 3", fmt(p.target3)),
        ("Risk / reward (T1)", f"1 : {p.risk_reward:.2f}  (net ≈ {fmt(p.net_risk_reward)})"),
        ("Technical score", f"{c.total:.1f} / 100"),
        ("Quantity", str(p.quantity)), ("Capital required", fmt(p.capital_required, 0, "₹")),
        ("Est. total costs (at T1)", fmt(p.est_costs_at_target, 2, "₹")),
        ("Max planned loss", f"₹{p.max_planned_loss:,.2f} + est. costs ₹{p.est_costs_at_stop:,.2f}"),
    ]
    st.markdown(head + kv_grid(items) + "</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Reasons for selection**")
        for r in sig.reasons:
            st.markdown(f"- {r}")
        st.markdown("**Confirmation conditions**")
        for r in sig.confirmations:
            st.markdown(f"- {r}")
    with c2:
        st.markdown("**Invalidation conditions**")
        for r in p.invalidation:
            st.markdown(f"- {r}")
        st.markdown("**Risk warnings**")
        warns = list(c.warnings) + ["Intraday positions must be squared off; gaps and slippage can exceed the stop.",
                                    f"Data is {p.price_label.lower()} – confirm price in your broker app before acting."]
        for w in warns:
            st.markdown(f"- ⚠️ {w}")
    q = getattr(c, "quote", None)
    if q is not None:
        with st.expander("🔗 Data sources & exchange details"):
            spread = f"{q.spread_pct:.3f}%" if q.spread_pct is not None else "not available"
            st.markdown(
                f"- **Quote source:** {', '.join(q.sources) or q.source} · label **{q.label.value}** · "
                f"time {str(q.timestamp)[:19]}\n"
                f"- **Best bid / ask:** {fmt(q.bid)} / {fmt(q.ask)} · spread {spread}\n"
                f"- **Circuit band:** {fmt(q.lower_band)} – {fmt(q.upper_band)}\n"
                f"- **Delivery % (NSE, informational – not scored):** {fmt(q.delivery_pct)}\n"
                f"- **Cross-check:** {q.cross_check or '—'}")
    if c.screenshot_evidence:
        with st.expander("📷 Screenshot evidence"):
            for ev in c.screenshot_evidence:
                st.json(ev, expanded=False)
    if explanation:
        exp, src = explanation
        with st.expander(f"🧠 Explanation ({src})", expanded=rank == 1):
            for k, v in exp.items():
                st.markdown(f"**{k}**")
                st.write(v)
    with st.expander("📊 Score breakdown"):
        rows = [{"Component": comp.name, "Points": comp.points, "Max": round(comp.weight, 1),
                 "Evidence": "; ".join(comp.evidence)} for comp in c.score.components]
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    if on_paper:
        on_paper(c)


def candidates_table(cands) -> pd.DataFrame:
    rows = []
    for c in cands:
        rows.append({"Symbol": c.symbol, "Origin": c.origin, "Strategy": c.signal.strategy_name,
                     "Dir": c.plan.direction, "Score": round(c.total, 1), "Status": c.plan.status,
                     "LTP": c.plan.current_price, "Trigger": c.plan.entry_trigger, "Stop": c.plan.stop_loss,
                     "T1": c.plan.target1, "R:R": c.plan.risk_reward, "Qty": c.plan.quantity,
                     "Passed": "✅" if c.passed else "❌", "Rejections": " | ".join(c.rejections)})
    return pd.DataFrame(rows)


def download_df(df: pd.DataFrame, name: str, label: str = "⬇️ Export CSV") -> None:
    if df is not None and not df.empty:
        st.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=name, mime="text/csv")
