"""The 13 BharatTrade agents. Financial values are always computed by
deterministic Python; LLMs (optional) only interpret screenshots or rephrase."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from agents.base import BaseAgent
from agents.schemas import (AnalysisRequest, SymbolAnalysis, SymbolData, SymbolDataRequest, UniverseRequest,
                            UniverseResult)
from brokers.base import DataProviderError, MarketDataProvider
from config.settings import Settings
from data.instrument_master import assess_liquidity, load_seed_universe
from data.market_hours import now_ist, parse_hhmm
from data.validation import freshness, validate_candles
from indicators.compute import compute_all
from indicators.price_action import support_resistance
from llm.explainer import llm_explanation, template_explanation
from llm.providers import LLMProvider
from market.regime import compute_regime, direction_alignment
from market.sector_strength import compute_sector_strength, sector_alignment
from ranking.filters import mandatory_rejections
from ranking.ranker import Candidate, select_top
from ranking.scoring import score_candidate
from risk.risk_limits import DayState, check_account_limits
from risk.trade_plan import build_trade_plan
from strategies.base import StrategyContext
from strategies.strategy_engine import all_strategies, latest_signals


class MarketDataAgent(BaseAgent):
    name = "MarketDataAgent"
    input_schema = SymbolDataRequest
    output_schema = SymbolData

    def __init__(self, provider: MarketDataProvider, settings: Settings):
        super().__init__()
        self.p, self.s = provider, settings

    def _run(self, req: SymbolDataRequest):
        daily = self.p.get_candles(req.symbol, "1d", req.daily_days)
        intra = self.p.get_candles(req.symbol, req.interval, req.lookback_days)
        rep = validate_candles(intra.df, req.interval)
        fresh, msg = freshness(intra, self.s.scan.stale_after_minutes)
        return SymbolData(req.symbol, intra, daily, fresh, msg, rep.issues, rep.warnings + intra.warnings), []


class MarketRegimeAgent(BaseAgent):
    name = "MarketRegimeAgent"

    def __init__(self, provider: MarketDataProvider):
        super().__init__()
        self.p = provider

    def _run(self, interval: str = "5m"):
        regime = compute_regime(self.p, interval if interval in ("5m", "15m") else "5m")
        sectors, sw = compute_sector_strength(self.p, regime.sector_changes_live, regime.nifty_change_live)
        return (regime, sectors), regime.warnings + sw


class InstrumentDiscoveryAgent(BaseAgent):
    name = "InstrumentDiscoveryAgent"
    input_schema = UniverseRequest
    output_schema = UniverseResult

    def _run(self, req: UniverseRequest):
        seed = {i.symbol: i for i in load_seed_universe()}
        warnings: list[str] = []
        inst = None
        if req.mode == "index" and req.nse is not None:
            from brokers.nse_web import NSEWebError
            from data.instrument_master import instruments_from_constituents

            try:
                inst = instruments_from_constituents(req.nse.index_constituents(req.index_name), req.index_name)
            except NSEWebError as exc:
                warnings.append(f"Index constituents unavailable ({exc}); using seed list")
        elif req.mode == "master_auto" and req.nse is not None:
            from brokers.nse_web import NSEWebError
            from data.instrument_master import parse_nse_equity_master

            try:
                inst, _ = parse_nse_equity_master(req.nse.equity_master_csv())
            except (NSEWebError, ValueError) as exc:
                warnings.append(f"NSE equity master download failed ({exc}); using seed list")
        elif req.mode in ("index", "master_auto"):
            warnings.append("NSE website source not enabled (choose 'Yahoo + NSE' in Settings); using seed list")
        if inst:
            pass
        elif req.mode == "master" and req.master_instruments:
            inst = req.master_instruments
        elif req.mode == "watchlist" and req.watchlist:
            from data.instrument_master import Instrument

            inst = [seed.get(s) or Instrument(s, s, "UNKNOWN", "EQ", source="watchlist") for s in req.watchlist]
        else:
            inst = list(seed.values())
        to_scan = inst[: req.limit]
        note = (f"Scanning {len(to_scan)} of {len(inst)} instruments in the selected universe"
                + (" – PARTIAL scan (raise the limit in Settings to scan more)" if len(to_scan) < len(inst) else
                   " – full universe scanned"))
        return UniverseResult(inst, len(inst), to_scan, note), warnings


class TechnicalAnalysisAgent(BaseAgent):
    name = "TechnicalAnalysisAgent"

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings

    def _run(self, data: SymbolData):
        ind = compute_all(data.intraday.df, data.intraday.interval)
        levels = support_resistance(ind, upto=len(ind) - 1) if len(ind) > 20 else []
        return (ind, levels), []


class StrategyValidationAgent(BaseAgent):
    name = "StrategyValidationAgent"

    def __init__(self, settings: Settings, enabled: list[str] | None = None):
        super().__init__()
        self.s = settings
        self.strategies = all_strategies(enabled)

    def _run(self, payload):
        symbol, ind, interval = payload
        p = self.s.profile
        ctx = StrategyContext(symbol=symbol, interval=interval, tick=p.tick_size, allow_short=p.allow_short,
                              no_new_entries_after=parse_hhmm(p.no_new_entries_after))
        return latest_signals(ind, self.strategies, ctx), []


class RiskManagementAgent(BaseAgent):
    name = "RiskManagementAgent"

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings

    def _run(self, day_state: DayState | None):
        return check_account_limits(day_state or DayState(), self.s.profile, now_ist()), []

    def plan(self, sig, ind, quote, levels, gate, now: datetime | None = None):
        bars_since = len(ind) - 1 - sig.bar_index
        return build_trade_plan(sig, ind, self.s.profile, self.s.costs,
                                current_price=quote.ltp if quote else None,
                                price_label=quote.label.value if quote else "UNAVAILABLE",
                                price_ts=quote.timestamp if quote else None, levels=levels,
                                bars_since_signal=bars_since, now_time=(now or now_ist()).time()
                                if quote and quote.label.value != "SIMULATED" else None,
                                available_allocation=gate.available_allocation)


class StockScannerAgent(BaseAgent):
    """Runs liquidity → data → indicators → strategies → plans → scores for one instrument."""
    name = "StockScannerAgent"
    input_schema = AnalysisRequest

    def __init__(self, provider, settings, regime, sectors, gate, enabled=None, interval="5m",
                 circuit_hitters: dict[str, set[str]] | None = None):
        super().__init__()
        self.p, self.s = provider, settings
        self.hitters = circuit_hitters or {"upper": set(), "lower": set()}
        self.regime, self.sectors, self.gate = regime, sectors, gate
        self.interval = interval
        self.data_agent = MarketDataAgent(provider, settings)
        self.ta_agent = TechnicalAnalysisAgent(settings)
        self.strat_agent = StrategyValidationAgent(settings, enabled)
        self.risk_agent = RiskManagementAgent(settings)

    def _run(self, req: AnalysisRequest):
        inst, sc = req.instrument, self.s.scan
        out = SymbolAnalysis(inst.symbol, None, None, None)
        try:
            daily = self.p.get_candles(inst.symbol, "1d", sc.daily_lookback_days)
        except DataProviderError as exc:
            out.skipped_reason = f"Data error: {exc}"
            out.errors.append(str(exc))
            return out, []
        out.liquidity = assess_liquidity(inst.symbol, daily.df, sc)
        if not out.liquidity.eligible and req.origin == "scanner":
            out.skipped_reason = "Liquidity filter: " + "; ".join(out.liquidity.reasons)
            return out, []
        r = self.data_agent.run(SymbolDataRequest(inst.symbol, self.interval, sc.lookback_days, sc.daily_lookback_days))
        if not r.ok:
            out.skipped_reason = "Data error: " + "; ".join(r.errors)
            return out, []
        data: SymbolData = r.output
        out.data = data
        if data.intraday.empty or len(data.intraday.df) < 60:
            out.skipped_reason = "Insufficient intraday candles"
            return out, []
        ta = self.ta_agent.run(data)
        ind, levels = ta.output
        out.indicators = ind
        last = ind.iloc[-1]
        if pd.notna(last.get("pdc")) and last["pdc"]:
            out.day_change_pct = float((last["close"] / last["pdc"] - 1) * 100)
        sigs = self.strat_agent.run((inst.symbol, ind, self.interval)).output or []
        if not sigs:
            return out, []
        try:
            quote = self.p.get_quote(inst.symbol)
        except DataProviderError as exc:
            quote = None
            out.errors.append(f"Quote unavailable: {exc}")
        for sig in sigs:
            plan = self.risk_agent.plan(sig, ind, quote, levels, self.gate)
            row, prev = ind.iloc[sig.bar_index], ind.iloc[max(sig.bar_index - 1, 0)]
            ra = direction_alignment(self.regime.regime, sig.direction)
            sa, snote = sector_alignment(self.sectors, inst.sector, sig.direction)
            liq = out.liquidity
            score = score_candidate(row, prev, sig.direction, sig.setup_type, sig.provisional, ra,
                                    f"Market regime: {self.regime.regime.value}", sa, snote,
                                    liq.avg_traded_value_cr if liq else None,
                                    quote.spread_pct if quote else None, quote is None or quote.spread_pct is None,
                                    self.s.weights)
            strat_cls = next(s for s in self.strat_agent.strategies if s.key == sig.strategy)
            rej = mandatory_rejections(
                data_fresh=data.fresh, freshness_msg=data.freshness_msg, data_label=data.intraday.label.value,
                liquidity_ok=bool(liq and liq.eligible), liquidity_reasons=liq.reasons if liq else ["unknown"],
                plan=plan, min_rr=self.s.profile.min_risk_reward,
                live_spread_pct=quote.spread_pct if quote else None, max_spread_pct=sc.max_spread_pct,
                provisional=sig.provisional, regime=self.regime.regime, strategy_regimes=strat_cls.suitable_regimes,
                symbol_verified=req.symbol_verified, circuit_suspected=bool(liq and liq.circuit_suspected),
                score=score.total, min_score=sc.min_score, allow_short=self.s.profile.allow_short,
                account_gate_reasons=list(self.gate.reasons),
                near_circuit=quote.near_circuit if quote else None,
                circuit_hitter=("UPPER" if inst.symbol in self.hitters.get("upper", set()) else
                                "LOWER" if inst.symbol in self.hitters.get("lower", set()) else None))
            if data.validation_issues:
                rej.extend(f"Data validation: {x}" for x in data.validation_issues)
            warns = list(data.validation_warnings)
            if quote is not None:
                warns += [w for w in quote.warnings if "Cross-check" in w or "circuit" in w or "unavailable" in w]
            c = Candidate(inst.symbol, inst.name, inst.sector, req.origin, sig, plan, score, rej,
                          warnings=warns, data_label=data.intraday.label.value,
                          data_ts=str(data.intraday.last_ts), liquidity=liq, closes=daily.df["close"],
                          screenshot_evidence=req.screenshot_evidence, quote=quote)
            out.candidates.append(c)
        return out, []


class ScreenshotVisionAgent(BaseAgent):
    name = "ScreenshotVisionAgent"

    def __init__(self, universe: dict[str, str], llm: LLMProvider | None = None):
        super().__init__()
        from vision.chart_analyser import ChartAnalyser

        self.analyser = ChartAnalyser(universe, llm)

    def _run(self, payload):
        img, use_llm = payload
        return self.analyser.analyse(img, use_llm=use_llm), []


class NewsAnalysisAgent(BaseAgent):
    name = "NewsAnalysisAgent"

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings

    def _run(self, symbols: dict[str, str]):
        from news.news_engine import news_for_symbols

        items, errors = news_for_symbols(symbols, self.s.app.news_max_age_hours)
        return items, errors


class RankingAgent(BaseAgent):
    name = "RankingAgent"

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings

    def _run(self, payload):
        cands, state = payload
        return select_top(cands, self.s.profile, state), []


class RecommendationExplanationAgent(BaseAgent):
    name = "RecommendationExplanationAgent"

    def __init__(self, llm: LLMProvider | None = None):
        super().__init__()
        self.llm = llm

    def _run(self, payload):
        cand, use_llm = payload
        tpl = template_explanation(cand)
        if use_llm and self.llm is not None:
            return llm_explanation(self.llm, tpl), []
        return (tpl, "template"), []


class BacktestingAgent(BaseAgent):
    name = "BacktestingAgent"

    def __init__(self, provider, settings):
        super().__init__()
        self.p, self.s = provider, settings

    def _run(self, payload):
        from backtesting.engine import BacktestConfig, run_backtest

        symbols, strategies, interval, days = payload
        data, warnings = {}, []
        for sym in symbols:
            try:
                cs = self.p.get_candles(sym, interval, days)
                data[sym] = cs.df
                if cs.warnings:
                    warnings.extend(f"{sym}: {w}" for w in cs.warnings)
            except DataProviderError as exc:
                warnings.append(f"{sym}: {exc}")
        cfg = BacktestConfig(self.s.profile, self.s.costs, interval, allow_short=self.s.profile.allow_short)
        return (run_backtest(data, strategies, cfg), data), warnings


class PaperTradingAgent(BaseAgent):
    name = "PaperTradingAgent"

    def __init__(self, provider, settings):
        super().__init__()
        from paper_trading.engine import PaperTradingEngine

        self.engine = PaperTradingEngine(provider, settings)

    def _run(self, action: str = "refresh"):
        if action == "refresh":
            return self.engine.refresh_all(), []
        if action == "day_state":
            return self.engine.day_state(), []
        raise ValueError(f"Unknown action {action}")
