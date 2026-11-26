"""Paper trading: simulated orders/positions persisted in the database and
monitored with the same fill logic as the backtester (backtesting/simulator.py).

Prices come from the configured provider; every update records the data label
(DELAYED / SIMULATED ...) so the journal never presents delayed prices as live.
No real orders are ever placed.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select

from brokers.base import DataProviderError, MarketDataProvider
from config.logging_setup import get_logger
from config.settings import Settings
from data.market_hours import TZ, as_ist, now_ist, parse_hhmm
from database.db import audit, session_scope
from database.models import JournalEntry, PaperTrade
from risk.risk_limits import DayState
from risk.transaction_costs import round_trip_costs
from backtesting.simulator import SimOrder, SimPosition, manage_bar, position_costs, position_pnl, try_fill

log = get_logger("paper")

PENDING, OPEN, CLOSED, CANCELLED, EXPIRED = "PENDING", "OPEN", "CLOSED", "CANCELLED", "EXPIRED"


def _ev(t: PaperTrade, text: str, label: str, at: datetime | None = None) -> None:
    t.events = list(t.events or []) + [{"ts": str(at or now_ist()), "label": label, "event": text}]


class PaperTradingEngine:
    def __init__(self, provider: MarketDataProvider, settings: Settings):
        self.provider = provider
        self.s = settings

    # ------------------------------------------------------------ create
    def create_from_plan(self, plan, sector: str = "UNKNOWN", recommendation_id: int | None = None,
                         valid_minutes: int = 30) -> int:
        if plan.quantity <= 0:
            raise ValueError("Plan quantity is zero")
        now = now_ist()
        cutoff = datetime.combine(now.date(), parse_hhmm(self.s.profile.no_new_entries_after), TZ)
        with session_scope() as s:
            t = PaperTrade(recommendation_id=recommendation_id, symbol=plan.symbol, direction=plan.direction,
                           strategy=plan.strategy, sector=sector, status=PENDING, quantity=plan.quantity,
                           open_quantity=0, entry_trigger=plan.entry_trigger, entry_limit=plan.entry_limit,
                           stop_loss=plan.stop_loss, initial_stop=plan.stop_loss, target1=plan.target1,
                           target2=plan.target2, data_label=plan.price_label,
                           valid_until=min(now + timedelta(minutes=valid_minutes), cutoff), last_processed=now,
                           created_at=now,
                           events=[], exits=[])
            _ev(t, f"Paper order created: {plan.direction} {plan.quantity} {plan.symbol} trigger {plan.entry_trigger} "
                   f"limit {plan.entry_limit} SL {plan.stop_loss} T1 {plan.target1}", plan.price_label, now)
            s.add(t)
            s.flush()
            tid = t.id
        audit("paper_order_created", {"id": tid, "symbol": plan.symbol})
        return tid

    # ------------------------------------------------------------ update
    def _bars_since(self, symbol: str, since: datetime) -> tuple[pd.DataFrame, str]:
        cs = self.provider.get_candles(symbol, "1m", 2)
        df = cs.df
        if since is not None:
            df = df[df.index > as_ist(since) - pd.Timedelta(minutes=1)]
        return df, cs.label.value

    def update_trade(self, t: PaperTrade, bars: pd.DataFrame, label: str, now: datetime) -> None:
        """Advance one trade through the given completed 1-minute bars."""
        slip = self.s.costs.slippage_bps_per_side + self.s.costs.default_half_spread_bps
        sq = parse_hhmm(self.s.profile.square_off_time)
        order = SimOrder(t.symbol, t.strategy, t.direction, t.entry_trigger, t.entry_limit, t.initial_stop,
                         t.target1, t.target2, t.quantity, 0, 10 ** 6)
        for k, (ts, bar) in enumerate(bars.iterrows()):
            if t.status == PENDING:
                if t.valid_until and ts >= as_ist(t.valid_until):
                    t.status = EXPIRED
                    _ev(t, "Order expired without trigger", label, ts)
                    break
                status, pos = try_fill(order, bar, k, ts, slip)
                if status == "missed":
                    t.status = EXPIRED
                    _ev(t, f"MISSED: price gapped beyond limit {t.entry_limit} – not chasing", label, ts)
                    break
                if status == "filled":
                    t.status, t.entry_price, t.entry_at = OPEN, pos.entry_price, ts.to_pydatetime()
                    t.open_quantity = pos.open_qty
                    _ev(t, f"Entry filled {t.quantity} @ {pos.entry_price:.2f} (simulated)", label, ts)
                    if pos.exits:
                        self._apply_exits(t, pos, label)
                    continue
            elif t.status == OPEN:
                pos = SimPosition(order, t.entry_price, as_ist(t.entry_at), 0, t.open_quantity, t.stop_loss,
                                  exits=[(e[0], e[1], e[2], pd.Timestamp(e[3])) for e in (t.exits or [])],
                                  t1_done=t.t1_done)
                n_before = len(pos.exits)
                evs = manage_bar(pos, bar, ts, slip, 0.5, square_off=ts.time() >= sq)
                t.stop_loss, t.t1_done, t.open_quantity = pos.stop, pos.t1_done, pos.open_qty
                for e in evs:
                    _ev(t, e, label, ts)
                if len(pos.exits) > n_before:
                    self._apply_exits(t, pos, label)
            if t.status in (CLOSED, EXPIRED, CANCELLED):
                break
        t.last_processed = now
        t.data_label = label

    def _apply_exits(self, t: PaperTrade, pos: SimPosition, label: str) -> None:
        t.exits = [[px, q, r, str(ts)] for px, q, r, ts in pos.exits]
        t.open_quantity = pos.open_qty
        t.realized_gross = round(position_pnl(pos), 2)
        if pos.closed:
            t.status = CLOSED
            t.costs = round(position_costs(pos, self.s.costs), 2)
            t.realized_net = round(t.realized_gross - t.costs, 2)
            exit_qty = sum(q for _, q, _, _ in pos.exits)
            t.exit_price = round(sum(px * q for px, q, _, _ in pos.exits) / exit_qty, 2)
            t.exit_at = pos.exits[-1][3].to_pydatetime()
            t.exit_reason = " + ".join(r for _, _, r, _ in pos.exits)
            _ev(t, f"Closed. Gross ₹{t.realized_gross:.2f}, est. costs ₹{t.costs:.2f}, net ₹{t.realized_net:.2f}",
                label)

    def refresh_all(self) -> dict:
        now = now_ist()
        summary = {"updated": 0, "errors": []}
        with session_scope() as s:
            rows = s.execute(select(PaperTrade).where(PaperTrade.status.in_([PENDING, OPEN]))).scalars().all()
            for t in rows:
                try:
                    bars, label = self._bars_since(t.symbol, t.last_processed)
                    self.update_trade(t, bars, label, now)
                    summary["updated"] += 1
                except DataProviderError as exc:
                    summary["errors"].append(f"{t.symbol}: {exc}")
        return summary

    def close_manual(self, trade_id: int, price: float | None = None, reason: str = "MANUAL EXIT") -> None:
        with session_scope() as s:
            t = s.get(PaperTrade, trade_id)
            if t is None or t.status not in (PENDING, OPEN):
                return
            if t.status == PENDING:
                t.status = CANCELLED
                _ev(t, "Cancelled by user", "USER")
                return
            if price is None:
                q = self.provider.get_quote(t.symbol)
                price, label = q.ltp, q.label.value
            else:
                label = "USER"
            order = SimOrder(t.symbol, t.strategy, t.direction, t.entry_trigger, t.entry_limit, t.initial_stop,
                             t.target1, t.target2, t.quantity, 0, 0)
            pos = SimPosition(order, t.entry_price, as_ist(t.entry_at), 0, t.open_quantity, t.stop_loss,
                              exits=[(e[0], e[1], e[2], pd.Timestamp(e[3])) for e in (t.exits or [])])
            pos.exits.append((round(price, 2), pos.open_qty, reason, pd.Timestamp(now_ist())))
            pos.open_qty = 0
            self._apply_exits(t, pos, label)

    # ------------------------------------------------------------ queries
    def trades_df(self, status: list[str] | None = None) -> pd.DataFrame:
        with session_scope() as s:
            q = select(PaperTrade)
            if status:
                q = q.where(PaperTrade.status.in_(status))
            rows = s.execute(q.order_by(PaperTrade.created_at.desc())).scalars().all()
            data = [{c.name: getattr(r, c.name) for c in PaperTrade.__table__.columns} for r in rows]
        return pd.DataFrame(data)

    def mark_to_market(self, df: pd.DataFrame) -> pd.DataFrame:
        """Unrealised P&L of open positions using the latest (labelled) quote."""
        if df.empty:
            return df
        df = df.copy()
        ltps, labels, unreal = [], [], []
        for _, r in df.iterrows():
            if r["status"] != OPEN:
                ltps.append(None); labels.append(None); unreal.append(0.0)
                continue
            try:
                q = self.provider.get_quote(r["symbol"])
                s = 1 if r["direction"] == "LONG" else -1
                ltps.append(q.ltp); labels.append(q.label.value)
                unreal.append(round((q.ltp - r["entry_price"]) * r["open_quantity"] * s, 2))
            except DataProviderError:
                ltps.append(None); labels.append("UNAVAILABLE"); unreal.append(0.0)
        df["ltp"], df["ltp_label"], df["unrealised"] = ltps, labels, unreal
        return df

    def day_state(self, day=None) -> DayState:
        day = day or now_ist().date()
        df = self.trades_df()
        st = DayState()
        if df.empty:
            return st
        df["day"] = pd.to_datetime(df["created_at"]).dt.date
        today = df[df["day"] == day]
        closed = today[today["status"] == CLOSED].sort_values("exit_at")
        st.realized_pnl = float(closed["realized_net"].sum())
        st.trades_taken = int(today["status"].isin([OPEN, CLOSED]).sum())
        streak = 0
        for v in closed["realized_net"][::-1]:
            if v < 0:
                streak += 1
            else:
                break
        st.consecutive_losses = streak
        if streak:
            st.last_loss_at = as_ist(closed["exit_at"].iloc[-1]).to_pydatetime()
        open_ = df[df["status"] == OPEN]
        st.open_exposure = float((open_["entry_price"] * open_["open_quantity"]).sum())
        st.open_sectors = open_.groupby("sector").size().to_dict()
        return st

    def performance(self) -> dict:
        df = self.trades_df([CLOSED])
        if df.empty:
            return {"closed_trades": 0}
        df["day"] = pd.to_datetime(df["exit_at"]).dt.date
        return {
            "closed_trades": len(df),
            "net_pnl": round(float(df["realized_net"].sum()), 2),
            "win_rate_pct": round(float((df["realized_net"] > 0).mean() * 100), 1),
            "daily": df.groupby("day")["realized_net"].sum().round(2).to_dict(),
            "by_strategy": df.groupby("strategy")["realized_net"].agg(["count", "sum", "mean"]).round(2).to_dict("index"),
        }

    def export_csv(self) -> bytes:
        df = self.trades_df()
        buf = io.StringIO()
        df.drop(columns=[c for c in ("events", "exits") if c in df.columns]).to_csv(buf, index=False)
        return buf.getvalue().encode("utf-8")

    # ------------------------------------------------------------ journal
    def add_journal(self, text: str, trade_id: int | None = None, kind: str = "note", tags: list[str] | None = None):
        with session_scope() as s:
            s.add(JournalEntry(paper_trade_id=trade_id, text=text[:5000], kind=kind, tags=tags or []))

    def journal_df(self) -> pd.DataFrame:
        with session_scope() as s:
            rows = s.execute(select(JournalEntry).order_by(JournalEntry.created_at.desc())).scalars().all()
            return pd.DataFrame([{c.name: getattr(r, c.name) for c in JournalEntry.__table__.columns} for r in rows])


def estimated_round_trip(plan, settings: Settings) -> dict:
    long = plan.direction == "LONG"
    b, s_ = (plan.entry_limit, plan.target1) if long else (plan.target1, plan.entry_limit)
    return round_trip_costs(b, s_, plan.quantity, settings.costs).to_dict()
