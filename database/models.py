"""SQLAlchemy 2.0 models. Portable across SQLite and PostgreSQL (JSON columns,
no dialect-specific types). Candle history lives in the separate candle cache
(data/cache.py) because it is high-volume, disposable data."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    from data.market_hours import now_ist

    return now_ist()


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InstrumentRow(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    series: Mapped[str] = mapped_column(String(8), default="EQ")
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="seed")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    regime: Mapped[str] = mapped_column(String(40))
    data_label: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(8))
    candle_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    values: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("scan_run_id", "symbol", "interval", name="uq_ind_snapshot"),)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    data_provider: Mapped[str] = mapped_column(String(60))
    data_label: Mapped[str] = mapped_column(String(16))
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    eligible: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    recommendations: Mapped[int] = mapped_column(Integer, default=0)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    coverage_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_log: Mapped[list] = mapped_column(JSON, default=list)
    settings_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class CandidateRanking(Base):
    __tablename__ = "candidate_rankings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(20))  # scanner | screenshot | manual
    strategy: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    components: Mapped[dict] = mapped_column(JSON, default=dict)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24))
    score: Mapped[float] = mapped_column(Float)
    plan: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreenshotRow(Base):
    __tablename__ = "screenshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(String(400))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("session_id", "sha256", name="uq_screenshot_session_hash"),)


class ImageEvidence(Base):
    __tablename__ = "image_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    screenshot_id: Mapped[int] = mapped_column(ForeignKey("screenshots.id"), index=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(40))
    sector: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(20), index=True)  # PENDING OPEN CLOSED CANCELLED EXPIRED
    quantity: Mapped[int] = mapped_column(Integer)
    open_quantity: Mapped[int] = mapped_column(Integer, default=0)
    entry_trigger: Mapped[float] = mapped_column(Float)
    entry_limit: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    initial_stop: Mapped[float] = mapped_column(Float)
    target1: Mapped[float] = mapped_column(Float)
    target2: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_gross: Mapped[float] = mapped_column(Float, default=0.0)
    costs: Mapped[float] = mapped_column(Float, default=0.0)
    realized_net: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    data_label: Mapped[str] = mapped_column(String(16), default="DELAYED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    exits: Mapped[list] = mapped_column(JSON, default=list)  # [[price, qty, reason, ts], ...]
    t1_done: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_processed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalEntry(Base):
    __tablename__ = "trade_journal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_trade_id: Mapped[int | None] = mapped_column(ForeignKey("paper_trades.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(20), default="note")
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    strategies: Mapped[list] = mapped_column(JSON)
    symbols: Mapped[list] = mapped_column(JSON)
    interval: Mapped[str] = mapped_column(String(8))
    data_label: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    trades: Mapped[list] = mapped_column(JSON, default=list)


class UserSetting(Base):
    __tablename__ = "user_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(40), default="user")
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
