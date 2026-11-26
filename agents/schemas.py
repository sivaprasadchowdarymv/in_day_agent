"""Typed inputs/outputs exchanged between agents."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from data.instrument_master import Instrument, LiquidityReport


@dataclass
class UniverseRequest:
    mode: str = "seed"  # seed | master | watchlist
    master_instruments: list[Instrument] | None = None
    watchlist: list[str] | None = None
    limit: int = 60
    index_name: str = "NIFTY 100"  # for mode="index"
    nse: object = None  # NSEWebClient for index / master_auto modes


@dataclass
class UniverseResult:
    instruments: list[Instrument]
    universe_size: int
    to_scan: list[Instrument]
    coverage_note: str


@dataclass
class SymbolDataRequest:
    symbol: str
    interval: str
    lookback_days: int
    daily_days: int


@dataclass
class SymbolData:
    symbol: str
    intraday: object  # CandleSet
    daily: object  # CandleSet
    fresh: bool
    freshness_msg: str
    validation_issues: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisRequest:
    instrument: Instrument
    origin: str = "scanner"
    screenshot_evidence: list[dict] = field(default_factory=list)
    symbol_verified: bool = True


@dataclass
class SymbolAnalysis:
    symbol: str
    liquidity: LiquidityReport | None
    data: SymbolData | None
    indicators: pd.DataFrame | None
    candidates: list = field(default_factory=list)
    day_change_pct: float | None = None
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class ScanRequest:
    universe: UniverseRequest
    interval: str = "5m"
    screenshot_symbols: dict[str, list[dict]] = field(default_factory=dict)  # verified symbol -> evidence
    manual_symbols: list[str] = field(default_factory=list)
    enabled_strategies: list[str] | None = None
    fetch_news: bool = False
    explain_with_llm: bool = False


@dataclass
class ScanOutcome:
    run_id: int | None
    state_log: list[dict]
    regime: object
    sectors: pd.DataFrame
    breadth: object
    universe_size: int
    scanned: int
    eligible: int
    coverage_note: str
    analyses: list[SymbolAnalysis]
    candidates: list
    selected: list
    skipped: list
    account_gate: object
    data_label: str
    warnings: list[str]
    no_trade_reason: str | None
    explanations: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
