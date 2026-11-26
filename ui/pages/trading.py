"""Risk Management, Paper Trading, Trade Journal and Performance Analytics pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import state
from ui.components.charts import bar_chart, equity_curve
from ui.components.widgets import data_banner, disclaimer, download_df, fmt


def _engine():
    from paper_trading.engine import PaperTradingEngine

    return PaperTradingEngine(state.provider(), state.settings())


def risk_page() -> None:
    st.title("🛡️ Risk Management")
    s = state.settings()
    p = s.profile
    with st.form("profile"):
        st.markdown("#### Trading profile (account below ₹50,000)")
        c = st.columns(4)
        cap = c[0].number_input("Capital ₹", 1000.0, 49_999.0, float(p.capital), step=1000.0)
        risk = c[1].number_input("Risk per trade %", 0.1, 2.0, float(p.risk_per_trade_pct), step=0.1)
        alloc = c[2].number_input("Max allocation per trade %", 5.0, 100.0, float(p.max_allocation_pct), step=5.0)
        ideas = c[3].number_input("Max trade ideas", 1, 3, int(p.max_trade_ideas))
        c = st.columns(4)
        rr = c[0].number_input("Min risk/reward", 1.0, 5.0, float(p.min_risk_reward), step=0.1)
        nrr = c[1].number_input("Min net R:R after costs", 0.0, 5.0, float(p.min_net_risk_reward), step=0.1)
        dl = c[2].number_input("Max daily loss %", 0.5, 5.0, float(p.max_daily_loss_pct), step=0.1)
        mt = c[3].number_input("Max trades per day", 1, 10, int(p.max_trades_per_day))
        c = st.columns(4)
        mcl = c[0].number_input("Consecutive-loss limit", 1, 5, int(p.max_consecutive_losses))
        cool = c[1].number_input("Cooldown minutes", 0, 240, int(p.cooldown_minutes))
        expo = c[2].number_input("Max open exposure %", 10.0, 100.0, float(p.max_open_exposure_pct), step=5.0)
        sec = c[3].number_input("Max positions per sector", 1, 3, int(p.max_positions_per_sector))
        short = st.checkbox("Allow intraday short selling (cash segment, squared off same day)", p.allow_short)
        st.caption("Leverage is never used to increase position size.")
        if st.form_submit_button("💾 Save profile", type="primary"):
            for k, v in dict(capital=cap, risk_per_trade_pct=risk, max_allocation_pct=alloc, max_trade_ideas=int(ideas),
                             min_risk_reward=rr, min_net_risk_reward=nrr, max_daily_loss_pct=dl,
                             max_trades_per_day=int(mt), max_consecutive_losses=int(mcl), cooldown_minutes=int(cool),
                             max_open_exposure_pct=expo, max_positions_per_sector=int(sec), allow_short=short).items():
                setattr(p, k, v)
            errs = p.validate()
            if errs:
                st.error(" ".join(errs))
            else:
                from database.db import save_settings

                save_settings(s)
                st.success("Saved.")
    c = st.columns(4)
    c[0].metric("Risk budget / trade", fmt(p.risk_budget, 0, "₹"))
    c[1].metric("Max allocation / trade", fmt(p.max_allocation, 0, "₹"))
    c[2].metric("Max daily loss", fmt(p.max_daily_loss, 0, "₹"))
    c[3].metric("Max exposure", fmt(p.capital * p.max_open_exposure_pct / 100, 0, "₹"))

    st.markdown("#### Position-size & cost calculator")
    c = st.columns(4)
    entry = c[0].number_input("Entry price", 1.0, 100_000.0, 500.0)
    stop = c[1].number_input("Stop-loss", 1.0, 100_000.0, 495.0)
    target = c[2].number_input("Target", 1.0, 100_000.0, 507.5)
    direction = c[3].selectbox("Direction", ["LONG", "SHORT"])
    from risk.position_sizing import size_position
    from risk.transaction_costs import round_trip_costs

    try:
        r = size_position(entry, stop, p)
        st.markdown(
            f"Risk budget = ₹{p.capital:,.0f} × {p.risk_per_trade_pct}% = **₹{r.risk_budget:,.2f}** · "
            f"Risk/share = |{entry} − {stop}| = **₹{r.risk_per_share:,.2f}** · "
            f"Risk-based qty = ⌊{r.risk_budget:.2f} / {r.risk_per_share:.2f}⌋ = **{r.risk_based_qty}** · "
            f"Capital-based qty = ⌊{r.available_allocation:,.0f} / {entry}⌋ = **{r.capital_based_qty}** → "
            f"**Final quantity {r.quantity}** (limited by {r.limiting_factor}).")
        if r.quantity:
            b, sl = (entry, target) if direction == "LONG" else (target, entry)
            cb = round_trip_costs(b, sl, r.quantity, s.costs)
            gross = abs(target - entry) * r.quantity
            c = st.columns(4)
            c[0].metric("Capital required", fmt(r.capital_required, 0, "₹"))
            c[1].metric("Max planned loss", fmt(r.max_planned_loss, 2, "₹"))
            c[2].metric("Gross reward at target", fmt(gross, 2, "₹"))
            c[3].metric("Est. costs + slippage", fmt(cb.total_with_slippage, 2, "₹"))
            st.dataframe(pd.DataFrame([cb.to_dict()]), hide_index=True)
            st.caption("All costs are ESTIMATES from configurable assumptions; actual charges appear on your contract note.")
        else:
            st.warning(r.note)
    except ValueError as exc:
        st.error(str(exc))

    st.markdown("#### Today's account guard")
    from data.market_hours import now_ist
    from risk.risk_limits import check_account_limits

    try:
        ds = _engine().day_state()
        g = check_account_limits(ds, p, now_ist())
        c = st.columns(4)
        c[0].metric("Realised P&L (paper)", fmt(ds.realized_pnl, 2, "₹"))
        c[1].metric("Trades taken", f"{ds.trades_taken}/{p.max_trades_per_day}")
        c[2].metric("Consecutive losses", ds.consecutive_losses)
        c[3].metric("Open exposure", fmt(ds.open_exposure, 0, "₹"))
        (st.success if g.allowed else st.error)("New trades allowed" if g.allowed else "; ".join(g.reasons))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not compute account state: {exc}")
    disclaimer()


def paper_page() -> None:
    st.title("📝 Paper Trading")
    st.caption("Simulated orders only. Fills use the same conservative rules as the backtester.")
    eng = _engine()
    c = st.columns(3)
    if c[0].button("🔄 Update prices & process orders", type="primary"):
        with st.spinner("Fetching 1-minute candles…"):
            res = eng.refresh_all()
        st.success(f"Updated {res['updated']} order(s)/position(s).")
        for e in res["errors"]:
            st.warning(e)
    data_banner(state.provider().realtime_label.value, "paper fills are simulated from this data")
    df = eng.trades_df()
    if df.empty:
        st.info("No paper trades yet. Create one from **Top 3 Recommendations** or below.")
    else:
        live = eng.mark_to_market(df[df["status"].isin(["PENDING", "OPEN"])])
        st.markdown("#### Pending orders & open positions")
        if live.empty:
            st.caption("None.")
        else:
            show = ["id", "symbol", "direction", "strategy", "status", "quantity", "open_quantity", "entry_trigger",
                    "entry_limit", "entry_price", "stop_loss", "target1", "target2", "ltp", "ltp_label", "unrealised"]
            st.dataframe(live[[c for c in show if c in live]], hide_index=True)
            c = st.columns([1, 1, 2])
            tid = c[0].selectbox("Trade", live["id"].tolist())
            px = c[1].number_input("Exit price (0 = latest quote)", 0.0, 1e6, 0.0)
            if c[2].button("Close / cancel selected"):
                eng.close_manual(int(tid), px or None)
                st.rerun()
        closed = df[df["status"].isin(["CLOSED", "EXPIRED", "CANCELLED"])]
        st.markdown("#### Closed / expired")
        st.dataframe(closed[["id", "symbol", "direction", "strategy", "status", "quantity", "entry_price",
                             "exit_price", "exit_reason", "realized_gross", "costs", "realized_net", "data_label",
                             "created_at"]], hide_index=True)
        with st.expander("Event log"):
            for _, r in df.iterrows():
                st.markdown(f"**#{r['id']} {r['symbol']}**")
                for e in r["events"] or []:
                    st.caption(f"{e['ts'][:19]} [{e['label']}] {e['event']}")
        st.download_button("⬇️ Export paper trades CSV", eng.export_csv(), "paper_trades.csv", "text/csv")
    with st.expander("➕ Manual paper order"):
        s = state.settings()
        c = st.columns(5)
        sym = c[0].selectbox("Symbol", sorted(state.universe_map()))
        d = c[1].selectbox("Direction", ["LONG", "SHORT"], key="mpd")
        trig = c[2].number_input("Trigger", 0.0, 1e6, 0.0)
        sl = c[3].number_input("Stop", 0.0, 1e6, 0.0)
        tgt = c[4].number_input("Target", 0.0, 1e6, 0.0)
        if st.button("Create paper order"):
            from risk.position_sizing import size_position
            from types import SimpleNamespace

            ok = trig > 0 and sl > 0 and tgt > 0 and ((d == "LONG" and sl < trig < tgt) or (d == "SHORT" and tgt < trig < sl))
            if not ok:
                st.error("Prices must satisfy stop < trigger < target (long) or target < trigger < stop (short).")
            else:
                r = size_position(trig, sl, s.profile)
                plan = SimpleNamespace(symbol=sym, direction=d, strategy="manual", entry_trigger=trig,
                                       entry_limit=round(trig * (1.001 if d == "LONG" else 0.999), 2), stop_loss=sl,
                                       target1=tgt, target2=None, quantity=r.quantity,
                                       price_label=state.provider().realtime_label.value)
                try:
                    tid = eng.create_from_plan(plan, state.seed_sectors().get(sym, "UNKNOWN"))
                    st.success(f"Paper order #{tid}: {r.quantity} shares (risk-sized).")
                except ValueError as exc:
                    st.error(str(exc))
    disclaimer()


def journal_page() -> None:
    st.title("📓 Trade Journal")
    eng = _engine()
    trades = eng.trades_df()
    with st.form("jr"):
        opts = [None] + (trades["id"].tolist() if not trades.empty else [])
        tid = st.selectbox("Link to paper trade (optional)", opts,
                           format_func=lambda x: "—" if x is None else
                           f"#{x} {trades.set_index('id').loc[x, 'symbol']}")
        kind = st.selectbox("Type", ["note", "pre-trade plan", "post-trade review", "mistake", "lesson"])
        text = st.text_area("Entry", max_chars=5000)
        tags = st.text_input("Tags (comma separated)")
        if st.form_submit_button("Add entry", type="primary") and text.strip():
            eng.add_journal(text.strip(), tid, kind, [t.strip() for t in tags.split(",") if t.strip()])
            st.success("Saved.")
    j = eng.journal_df()
    if j.empty:
        st.caption("No journal entries yet.")
    else:
        st.dataframe(j, hide_index=True)
        download_df(j, "journal.csv")
    disclaimer()


def performance_page() -> None:
    st.title("📉 Performance Analytics")
    eng = _engine()
    perf = eng.performance()
    if perf.get("closed_trades", 0) == 0:
        st.info("No closed paper trades yet.")
    else:
        c = st.columns(3)
        c[0].metric("Closed paper trades", perf["closed_trades"])
        c[1].metric("Net P&L (est.)", fmt(perf["net_pnl"], 2, "₹"))
        c[2].metric("Win rate", f"{perf['win_rate_pct']}%")
        closed = eng.trades_df(["CLOSED"])
        st.plotly_chart(equity_curve(closed, "exit_at", "realized_net"), key="perf_eq")
        daily = pd.DataFrame({"day": [str(k) for k in perf["daily"]], "pnl": list(perf["daily"].values())})
        st.plotly_chart(bar_chart(daily, "day", "pnl", "Daily net P&L (₹)"), key="perf_daily")
        bs = pd.DataFrame(perf["by_strategy"]).T.reset_index().rename(columns={"index": "strategy"})
        st.markdown("#### Strategy-wise")
        st.dataframe(bs, hide_index=True)
    st.markdown("#### Scan & recommendation history")
    from sqlalchemy import select

    from database.db import session_scope
    from database.models import Recommendation, ScanRun

    with session_scope() as s:
        runs = s.execute(select(ScanRun).order_by(ScanRun.id.desc()).limit(50)).scalars().all()
        rdf = pd.DataFrame([{"id": r.id, "started": r.started_at, "status": r.status, "label": r.data_label,
                             "regime": r.regime, "scanned": r.scanned, "candidates": r.candidates,
                             "recommendations": r.recommendations} for r in runs])
        recs = s.execute(select(Recommendation).order_by(Recommendation.id.desc()).limit(100)).scalars().all()
        recdf = pd.DataFrame([{"scan": r.scan_run_id, "rank": r.rank, "symbol": r.symbol, "dir": r.direction,
                               "strategy": r.strategy, "score": r.score, "status": r.status,
                               "trigger": r.plan.get("entry_trigger"), "stop": r.plan.get("stop_loss"),
                               "t1": r.plan.get("target1"), "created": r.created_at} for r in recs])
    st.dataframe(rdf, hide_index=True)
    st.dataframe(recdf, hide_index=True)
    download_df(recdf, "recommendations.csv")
    st.caption("Past results – simulated or historical – do not guarantee future returns.")
    disclaimer()
