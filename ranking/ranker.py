"""Combine candidates, apply diversification and pick up to N."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config.settings import TradingProfile
from risk.risk_limits import DayState, return_correlation, sector_ok


@dataclass
class Candidate:
    symbol: str
    name: str
    sector: str
    origin: str  # scanner | screenshot | manual
    signal: object
    plan: object
    score: object  # ScoreCard
    rejections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_label: str = ""
    data_ts: str = ""
    liquidity: object = None
    closes: pd.Series | None = None  # daily closes for correlation checks
    screenshot_evidence: list[dict] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    quote: object = None  # Quote used for the plan (sources, bid/ask, bands, delivery %)

    @property
    def passed(self) -> bool:
        return not self.rejections

    @property
    def total(self) -> float:
        return self.score.total if self.score else 0.0


def select_top(cands: list[Candidate], profile: TradingProfile, state: DayState | None = None,
               max_corr: float = 0.85) -> tuple[list[Candidate], list[Candidate]]:
    """Return (selected, passed_but_not_selected). Never forces N picks."""
    state = state or DayState()
    passing = sorted([c for c in cands if c.passed], key=lambda c: c.total, reverse=True)
    # one setup per symbol (best score)
    seen, uniq = set(), []
    for c in passing:
        if c.symbol not in seen:
            uniq.append(c)
            seen.add(c.symbol)
    chosen: list[Candidate] = []
    skipped: list[Candidate] = []
    exposure = state.open_exposure
    max_exp = profile.capital * profile.max_open_exposure_pct / 100
    for c in uniq:
        if len(chosen) >= profile.max_trade_ideas:
            c.warnings.append("Not selected: top-N limit reached")
            skipped.append(c)
            continue
        if not sector_ok(c.sector, state, [x.sector for x in chosen], profile):
            c.warnings.append(f"Not selected: sector {c.sector} exposure limit")
            skipped.append(c)
            continue
        corr_hit = None
        for x in chosen:
            if c.closes is not None and x.closes is not None:
                rho = return_correlation(c.closes, x.closes)
                if rho is not None and rho > max_corr and c.plan.direction == x.plan.direction:
                    corr_hit = (x.symbol, rho)
                    break
        if corr_hit:
            c.warnings.append(f"Not selected: {corr_hit[1]:.2f} return correlation with {corr_hit[0]}")
            skipped.append(c)
            continue
        if exposure + c.plan.capital_required > max_exp + 1e-6:
            c.warnings.append("Not selected: total exposure limit")
            skipped.append(c)
            continue
        exposure += c.plan.capital_required
        chosen.append(c)
    return chosen, skipped
