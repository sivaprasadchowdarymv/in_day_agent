"""Workflow orchestrator with explicit state transitions:

INIT → MARKET_CONTEXT → RISK_GATE → UNIVERSE → SCANNING → SCREENSHOT_MERGE →
RANKING → EXPLAINING → PERSISTING → DONE | NO_TRADE | FAILED
"""
from __future__ import annotations

from collections.abc import Callable

from agents.agents import (InstrumentDiscoveryAgent, MarketRegimeAgent, NewsAnalysisAgent, PaperTradingAgent,
                           RankingAgent, RecommendationExplanationAgent, RiskManagementAgent, StockScannerAgent)
from agents.schemas import AnalysisRequest, ScanOutcome, ScanRequest
from brokers.base import MarketDataProvider
from config.logging_setup import get_logger
from config.settings import Settings
from data.instrument_master import Instrument, load_seed_universe
from data.market_hours import now_ist
from database.db import audit, session_scope
from database.models import CandidateRanking, Recommendation, ScanRun
from llm.providers import LLMProvider, NoLLM
from market.breadth import compute_breadth
from market.regime import Regime, RegimeResult
from news.news_engine import event_risk_warnings
from risk.risk_limits import DayState

log = get_logger("orchestrator")

STATES = ["INIT", "MARKET_CONTEXT", "RISK_GATE", "UNIVERSE", "SCANNING", "SCREENSHOT_MERGE", "RANKING",
          "EXPLAINING", "PERSISTING", "DONE", "NO_TRADE", "FAILED"]
NO_TRADE_MSG = "NO VALID INTRADAY OPPORTUNITY — DO NOT TRADE."


class Orchestrator:
    def __init__(self, provider: MarketDataProvider, settings: Settings, llm: LLMProvider | None = None,
                 persist: bool = True, day_state: DayState | None = None):
        self.p, self.s = provider, settings
        self.llm = llm or NoLLM()
        self.persist = persist
        self.day_state = day_state
        self.state = "INIT"
        self.log: list[dict] = []

    def _go(self, state: str, note: str = "") -> None:
        assert state in STATES
        self.log.append({"ts": now_ist().strftime("%H:%M:%S"), "from": self.state, "to": state, "note": note})
        self.state = state

    def run(self, req: ScanRequest, progress: Callable[[float, str], None] | None = None) -> ScanOutcome:
        progress = progress or (lambda f, m: None)
        warnings: list[str] = []
        run_id = None
        try:
            if self.persist:
                with session_scope() as s:
                    sr = ScanRun(data_provider=self.p.name, data_label=self.p.realtime_label.value,
                                 settings_snapshot=self.s.to_dict())
                    s.add(sr)
                    s.flush()
                    run_id = sr.id
            # ---- market context
            self._go("MARKET_CONTEXT")
            progress(0.03, "Analysing NIFTY 50, BANK NIFTY, VIX and sectors…")
            mr = MarketRegimeAgent(self.p).run(req.interval)
            if mr.ok:
                regime, sectors = mr.output
                warnings.extend(mr.warnings)
            else:
                import pandas as pd

                regime = RegimeResult(Regime.UNKNOWN, 0.0, [], None, ["Regime agent failed"], mr.errors, "UNAVAILABLE")
                sectors = pd.DataFrame()
                warnings.extend(mr.errors)
            # ---- risk gate
            self._go("RISK_GATE")
            state = self.day_state
            if state is None:
                pa = PaperTradingAgent(self.p, self.s).run("day_state")
                state = pa.output if pa.ok else DayState()
            gate = RiskManagementAgent(self.s).run(state).output
            # ---- universe
            self._go("UNIVERSE")
            from brokers.factory import nse_client_of

            nse = nse_client_of(self.p)
            if req.universe.nse is None:
                req.universe.nse = nse
            ur = InstrumentDiscoveryAgent().run(req.universe)
            uni = ur.output
            warnings.extend(ur.warnings)
            seed = {i.symbol: i for i in load_seed_universe()}
            hitters = None
            if nse is not None:
                from data.market_extras import circuit_hitters

                hitters, hw = circuit_hitters(nse)
                warnings.extend(hw)
            # ---- scanning
            self._go("SCANNING", uni.coverage_note)
            scanner = StockScannerAgent(self.p, self.s, regime, sectors, gate, req.enabled_strategies, req.interval,
                                        hitters)
            analyses = []
            n = max(len(uni.to_scan), 1)
            for k, inst in enumerate(uni.to_scan):
                progress(0.08 + 0.72 * k / n, f"Scanning {inst.symbol} ({k + 1}/{len(uni.to_scan)})…")
                r = scanner.run(AnalysisRequest(inst, "scanner"))
                if r.ok:
                    analyses.append(r.output)
                else:
                    warnings.append(f"{inst.symbol}: {'; '.join(r.errors)}")
            # ---- screenshot + manual candidates
            self._go("SCREENSHOT_MERGE")
            scanned_syms = {a.symbol for a in analyses}
            extra = [(s, "screenshot", ev) for s, ev in req.screenshot_symbols.items()]
            extra += [(s, "manual", []) for s in req.manual_symbols]
            for sym, origin, ev in extra:
                progress(0.82, f"Re-verifying {origin} symbol {sym} on fresh data…")
                existing = next((a for a in analyses if a.symbol == sym), None)
                if existing is not None:
                    for c in existing.candidates:
                        c.origin = f"scanner+{origin}"
                        c.screenshot_evidence = ev
                    continue
                inst = seed.get(sym) or Instrument(sym, sym, "UNKNOWN", "EQ", source=origin)
                r = scanner.run(AnalysisRequest(inst, origin, ev, symbol_verified=True))
                if r.ok:
                    analyses.append(r.output)
                    scanned_syms.add(sym)
            cands = [c for a in analyses for c in a.candidates]
            # breadth from scanned stocks
            changes = {a.symbol: a.day_change_pct for a in analyses if a.day_change_pct is not None}
            breadth = compute_breadth(changes, uni.universe_size)
            eligible = sum(1 for a in analyses if a.liquidity and a.liquidity.eligible)
            # ---- news (warnings only)
            news = {}
            if req.fetch_news and cands:
                syms = {c.symbol: c.name for c in cands if c.passed}
                if syms:
                    progress(0.86, "Checking recent news & corporate events…")
                    nr = NewsAnalysisAgent(self.s).run(syms)
                    if nr.ok:
                        news = {k: [i.to_dict() for i in v] for k, v in nr.output.items()}
                        warnings.extend(f"News source: {e}" for e in nr.warnings)
                        for c in cands:
                            items = nr.output.get(c.symbol, [])
                            c.news = [i.to_dict() for i in items[:5]]
                            c.warnings.extend(event_risk_warnings(items))
                    if nse is not None:
                        from data.market_extras import deals_for

                        deals, dw = deals_for(nse, set(syms))
                        warnings.extend(dw)
                        for c in cands:
                            c.warnings.extend(f"NSE {d}" for d in deals.get(c.symbol, [])[:3])
            # ---- ranking
            self._go("RANKING")
            progress(0.9, "Ranking candidates and applying mandatory filters…")
            selected, skipped = RankingAgent(self.s).run((cands, state)).output
            # ---- explanations
            self._go("EXPLAINING")
            explanations = {}
            ex_agent = RecommendationExplanationAgent(self.llm)
            for c in selected:
                r = ex_agent.run((c, req.explain_with_llm))
                explanations[c.symbol] = r.output if r.ok else ({}, "error")
            labels = {a.data.intraday.label.value for a in analyses if a.data is not None}
            data_label = "/".join(sorted(labels)) or "UNAVAILABLE"
            no_trade = None
            if not selected:
                if regime.regime in (Regime.HIGH_VOLATILITY, Regime.UNKNOWN):
                    no_trade = f"{NO_TRADE_MSG} Market regime: {regime.regime.value}."
                elif gate.reasons:
                    no_trade = f"{NO_TRADE_MSG} Risk limits: {'; '.join(gate.reasons)}."
                elif not cands:
                    no_trade = f"{NO_TRADE_MSG} No strategy produced a setup on the scanned stocks."
                else:
                    no_trade = f"{NO_TRADE_MSG} {len(cands)} setup(s) found but none passed every mandatory check."
            # ---- persist
            self._go("PERSISTING")
            if self.persist and run_id is not None:
                self._persist(run_id, uni, analyses, cands, selected, explanations, regime, data_label, eligible)
            self._go("NO_TRADE" if no_trade else "DONE", no_trade or f"{len(selected)} recommendation(s)")
            progress(1.0, "Done")
            audit("scan_completed", {"run_id": run_id, "selected": [c.symbol for c in selected],
                                     "regime": regime.regime.value})
            return ScanOutcome(run_id, self.log, regime, sectors, breadth, uni.universe_size, len(analyses), eligible,
                               uni.coverage_note, analyses, cands, selected, skipped, gate, data_label, warnings,
                               no_trade, explanations, news)
        except Exception as exc:
            self._go("FAILED", f"{type(exc).__name__}: {exc}")
            log.exception("Scan failed")
            if self.persist and run_id is not None:
                with session_scope() as s:
                    sr = s.get(ScanRun, run_id)
                    if sr:
                        sr.status, sr.finished_at, sr.state_log = "FAILED", now_ist(), self.log
            raise

    def _persist(self, run_id, uni, analyses, cands, selected, explanations, regime, data_label, eligible):
        with session_scope() as s:
            sr = s.get(ScanRun, run_id)
            sr.finished_at = now_ist()
            sr.status = "NO_TRADE" if not selected else "DONE"
            sr.universe_size, sr.scanned, sr.eligible = uni.universe_size, len(analyses), eligible
            sr.candidates, sr.recommendations = len(cands), len(selected)
            sr.regime, sr.coverage_note, sr.data_label = regime.regime.value, uni.coverage_note, data_label
            sr.state_log = self.log
            for c in cands:
                s.add(CandidateRanking(scan_run_id=run_id, symbol=c.symbol, origin=c.origin, strategy=c.signal.strategy,
                                       direction=c.signal.direction, score=c.total, passed=c.passed,
                                       rejection_reasons=c.rejections, components=c.score.to_dict()))
            for rank, c in enumerate(selected, 1):
                exp, src = explanations.get(c.symbol, ({}, ""))
                rec = Recommendation(scan_run_id=run_id, rank=rank, symbol=c.symbol, direction=c.plan.direction,
                                     strategy=c.plan.strategy, status=c.plan.status, score=c.total,
                                     plan=_jsonable(c.plan.to_dict()), explanation={"source": src, **exp})
                s.add(rec)
                s.flush()
                c.recommendation_id = rec.id


def _jsonable(d):
    import math

    out = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        else:
            out[k] = v
    return out
