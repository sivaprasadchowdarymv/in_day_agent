"""Engine/session management, versioned migrations, retention and audit."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import Callable, Iterator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.logging_setup import get_logger, redact
from config.settings import STORAGE_DIR, Settings
from database.models import AuditLog, Base, SchemaVersion, UserSetting

log = get_logger("db")

_engine: Engine | None = None
_Session: sessionmaker | None = None


def default_url() -> str:
    return f"sqlite:///{(STORAGE_DIR / 'bharattrade.sqlite').as_posix()}"


def init_db(url: str | None = None) -> Engine:
    global _engine, _Session
    url = url or default_url()
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine = create_engine(url, **kwargs)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    run_migrations(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    return _engine  # type: ignore[return-value]


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        init_db()
    s = _Session()  # type: ignore[misc]
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ----------------------------------------------------------------- migrations
def _m1_initial(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def _m2_indexes(engine: Engine) -> None:
    # example additive migration: make sure all tables created by newer models exist
    Base.metadata.create_all(engine)


MIGRATIONS: list[tuple[int, Callable[[Engine], None]]] = [(1, _m1_initial), (2, _m2_indexes)]


def run_migrations(engine: Engine) -> int:
    insp = inspect(engine)
    if "schema_version" not in insp.get_table_names():
        SchemaVersion.__table__.create(engine)
    with engine.begin() as conn:
        current = conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar() or 0
    for version, fn in MIGRATIONS:
        if version > current:
            fn(engine)
            with Session(engine) as s:
                s.add(SchemaVersion(version=version))
                s.commit()
            log.info(f"Applied DB migration {version}")
            current = version
    return current


# ----------------------------------------------------------------- helpers
def audit(action: str, details: dict | None = None, actor: str = "user") -> None:
    safe = {k: (redact(str(v)) if isinstance(v, str) else v) for k, v in (details or {}).items()}
    try:
        with session_scope() as s:
            s.add(AuditLog(action=action, details=safe, actor=actor))
    except Exception as exc:  # pragma: no cover - auditing must not break the app
        log.error(f"Audit log failed: {exc}")


def save_settings(settings: Settings) -> None:
    with session_scope() as s:
        row = s.get(UserSetting, "settings")
        data = settings.to_dict()
        if row:
            row.value = data
        else:
            s.add(UserSetting(key="settings", value=data))
    audit("settings_saved", {"capital": settings.profile.capital, "risk_pct": settings.profile.risk_per_trade_pct})


def load_saved_settings(base: Settings) -> Settings:
    try:
        with session_scope() as s:
            row = s.get(UserSetting, "settings")
            if row:
                merged = Settings.from_dict(row.value)
                # environment-chosen provider wins only if DB has none
                return merged
    except Exception as exc:  # pragma: no cover
        log.error(f"Could not load saved settings: {exc}")
    return base


def apply_retention(days: int) -> dict:
    from data.market_hours import now_ist
    from database.models import AuditLog as A, IndicatorSnapshot, MarketSnapshot

    cutoff = now_ist() - timedelta(days=days)
    removed = {}
    with session_scope() as s:
        for model in (MarketSnapshot, IndicatorSnapshot, A):
            col = getattr(model, "created_at", None)
            if col is None:
                continue
            rows = s.execute(select(model).where(col < cutoff)).scalars().all()
            for r in rows:
                s.delete(r)
            removed[model.__tablename__] = len(rows)
    return removed
