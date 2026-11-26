"""Central configuration for BharatTrade AI.

All values have safe defaults and can be overridden via environment variables
(.env file) or the Settings page (persisted in the database as user settings).

Secrets (LLM API keys) are only read from the environment / session and are
never written to logs or to the database.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = PROJECT_ROOT / "storage"
LOG_DIR = PROJECT_ROOT / "logs"
STORAGE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

MAX_CAPITAL = 49_999.0  # product constraint: account below ₹50,000


def _env(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


@dataclass
class TradingProfile:
    capital: float = 30_000.0
    risk_per_trade_pct: float = 0.5
    max_allocation_pct: float = 25.0
    max_trade_ideas: int = 3
    min_risk_reward: float = 1.5
    min_net_risk_reward: float = 0.8  # reward/risk at T1 after estimated costs & slippage
    max_stop_atr: float = 2.5  # stops wider than this many ATRs are rejected
    max_daily_loss_pct: float = 1.5
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 2
    cooldown_minutes: int = 30
    max_open_exposure_pct: float = 75.0
    max_positions_per_sector: int = 1
    allow_short: bool = True
    allow_leverage: bool = False  # never used to increase size; kept for transparency
    tick_size: float = 0.05
    no_new_entries_after: str = "14:45"
    square_off_time: str = "15:15"

    def validate(self) -> list[str]:
        errors = []
        if not (0 < self.capital <= MAX_CAPITAL):
            errors.append(f"Capital must be between ₹1 and ₹{MAX_CAPITAL:,.0f}.")
        if not (0 < self.risk_per_trade_pct <= 2.0):
            errors.append("Risk per trade must be between 0% and 2%.")
        if not (0 < self.max_allocation_pct <= 100):
            errors.append("Max allocation per trade must be between 0% and 100%.")
        if not (1 <= self.max_trade_ideas <= 3):
            errors.append("Max simultaneous trade ideas must be 1–3.")
        if self.min_risk_reward < 1.0:
            errors.append("Minimum risk/reward must be at least 1.0.")
        if self.tick_size <= 0:
            errors.append("Tick size must be positive.")
        return errors

    @property
    def risk_budget(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100.0

    @property
    def max_allocation(self) -> float:
        return self.capital * self.max_allocation_pct / 100.0

    @property
    def max_daily_loss(self) -> float:
        return self.capital * self.max_daily_loss_pct / 100.0


@dataclass
class CostConfig:
    """Estimated intraday (MIS) equity costs. Defaults reflect Groww's published
    intraday schedule as of Nov 2026 – verify before relying on them; actual
    costs come only from your contract note."""

    brokerage_flat: float = 20.0  # ₹ per executed order
    brokerage_pct: float = 0.1  # % of order value; lower of flat/pct applies
    brokerage_min: float = 5.0  # minimum per order (lower of this and 2.5% of value)
    stt_sell_pct: float = 0.025
    stamp_buy_pct: float = 0.003
    exchange_txn_pct: float = 0.00307  # NSE cash incl. IPFT (from 1 Mar 2026)
    sebi_pct: float = 0.0001
    gst_pct: float = 18.0
    slippage_bps_per_side: float = 2.0  # 0.02% assumed adverse slippage
    default_half_spread_bps: float = 1.5  # used when no live spread is available


@dataclass
class ScoringWeights:
    trend: float = 20
    momentum: float = 15
    vwap: float = 15
    relative_volume: float = 15
    price_action: float = 15
    market_sector: float = 10
    liquidity: float = 10

    def normalised(self) -> dict[str, float]:
        d = asdict(self)
        total = sum(d.values()) or 1.0
        return {k: v * 100.0 / total for k, v in d.items()}


@dataclass
class ScanConfig:
    interval: str = "5m"
    lookback_days: int = 10
    daily_lookback_days: int = 120
    min_avg_traded_value_cr: float = 10.0  # ₹ crore per day (20-day average)
    min_avg_volume: float = 200_000.0
    min_price: float = 50.0
    max_price: float = 5_000.0  # must be affordable within allocation
    max_spread_pct: float = 0.15
    min_atr_pct: float = 0.8
    max_atr_pct: float = 6.0
    min_history_days: int = 30
    max_symbols_per_scan: int = 60
    request_delay_sec: float = 0.3
    stale_after_minutes: int = 25  # Yahoo intraday is delayed; beyond this = stale
    min_score: float = 55.0


@dataclass
class AppConfig:
    data_provider: str = "yahoo_nse"  # "yahoo_nse" (Yahoo candles + NSE website quotes) | "yahoo" | "simulated"
    demo_clock: str = ""  # simulated mode only: replay the last session at "HH:MM" (blank = real clock)
    database_url: str = ""
    llm_provider: str = "none"  # none | gemini | groq | openrouter
    llm_model: str = ""
    llm_vision_model: str = ""
    log_level: str = "INFO"
    max_upload_mb: int = 8
    news_max_age_hours: int = 24
    retention_days: int = 90


@dataclass
class Settings:
    profile: TradingProfile = field(default_factory=TradingProfile)
    costs: CostConfig = field(default_factory=CostConfig)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    scan: ScanConfig = field(default_factory=ScanConfig)
    app: AppConfig = field(default_factory=AppConfig)

    def to_dict(self) -> dict:
        return {
            "profile": asdict(self.profile),
            "costs": asdict(self.costs),
            "weights": asdict(self.weights),
            "scan": asdict(self.scan),
            "app": asdict(self.app),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls()
        for section_name in ("profile", "costs", "weights", "scan", "app"):
            section = getattr(s, section_name)
            for f in fields(section):
                if section_name in d and f.name in d[section_name]:
                    setattr(section, f.name, type(getattr(section, f.name))(d[section_name][f.name]))
        return s


def load_settings() -> Settings:
    """Defaults <- environment variables."""
    s = Settings()
    p = s.profile
    p.capital = min(_env("BT_CAPITAL", p.capital), MAX_CAPITAL)
    p.risk_per_trade_pct = _env("BT_RISK_PCT", p.risk_per_trade_pct)
    p.max_allocation_pct = _env("BT_MAX_ALLOC_PCT", p.max_allocation_pct)
    p.allow_short = _env("BT_ALLOW_SHORT", p.allow_short)
    a = s.app
    a.data_provider = _env("BT_DATA_PROVIDER", a.data_provider)
    a.database_url = _env("DATABASE_URL", a.database_url)
    a.llm_provider = _env("BT_LLM_PROVIDER", a.llm_provider)
    a.llm_model = _env("BT_LLM_MODEL", a.llm_model)
    a.llm_vision_model = _env("BT_LLM_VISION_MODEL", a.llm_vision_model)
    a.log_level = _env("BT_LOG_LEVEL", a.log_level)
    a.max_upload_mb = _env("BT_MAX_UPLOAD_MB", a.max_upload_mb)
    s.scan.max_symbols_per_scan = _env("BT_MAX_SYMBOLS", s.scan.max_symbols_per_scan)
    return s


def get_secret(name: str) -> str | None:
    """Read an API key from the environment only. Never log the value."""
    val = os.getenv(name)
    return val.strip() if val else None
