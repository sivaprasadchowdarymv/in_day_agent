"""Pydantic schemas for screenshot evidence. Every extracted value carries its
source and confidence; missing information stays None (never fabricated)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Source = Literal["ocr", "opencv", "llm", "user", "none"]


class Observed(BaseModel):
    value: Any = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: Source = "none"
    flagged: bool = False
    note: str = ""


class ScreenshotEvidence(BaseModel):
    image_id: str
    filename: str
    sha256: str
    width: int
    height: int
    readability: float = Field(0.0, ge=0.0, le=1.0)
    readability_note: str = ""
    symbol: Observed = Observed()
    symbol_candidates: list[str] = []
    exchange: Observed = Observed()
    timeframe: Observed = Observed()
    visible_price: Observed = Observed()
    timestamp: Observed = Observed()
    ema_lines: Observed = Observed()
    vwap: Observed = Observed()
    rsi: Observed = Observed()
    macd: Observed = Observed()
    support_levels: Observed = Observed()
    resistance_levels: Observed = Observed()
    patterns: Observed = Observed()
    trend: Observed = Observed()
    price_axis: dict | None = None
    flags: list[str] = []
    raw_text: str = ""
    llm_used: bool = False
    llm_note: str = ""


class LLMChartReading(BaseModel):
    """Schema the vision LLM must return. Unknown → null. Validated strictly."""

    symbol: str | None = None
    exchange: str | None = None
    timeframe: str | None = None
    last_price: float | None = None
    timestamp: str | None = None
    ema_periods: list[int] = []
    vwap_visible: bool | None = None
    rsi_value: float | None = None
    macd_state: Literal["bullish", "bearish", "neutral"] | None = None
    support: list[float] = []
    resistance: list[float] = []
    patterns: list[str] = []
    trend: Literal["up", "down", "sideways"] | None = None
    unreadable: list[str] = []

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v):
        if v is None:
            return v
        v = v.strip().upper().replace("NSE:", "").replace(".NS", "")
        return v or None

    @field_validator("rsi_value")
    @classmethod
    def _rsi(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("RSI must be 0–100")
        return v

    @field_validator("last_price")
    @classmethod
    def _price(cls, v):
        if v is not None and v <= 0:
            raise ValueError("price must be positive")
        return v


class VerificationResult(BaseModel):
    image_id: str
    symbol: str | None
    status: Literal["VERIFIED", "VERIFIED_WITH_DISCREPANCIES", "UNVERIFIED"]
    reasons: list[str] = []
    discrepancies: list[str] = []
    agreements: list[str] = []
    current_price: float | None = None
    current_price_label: str | None = None
    current_price_ts: str | None = None
    interval_used: str | None = None
