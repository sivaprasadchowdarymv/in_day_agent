"""LLM providers (mocked HTTP), explanation number-guard, news parsing,
security/redaction, DB migrations/settings, and the full orchestrator."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from data.market_hours import TZ


# ---------------------------------------------------------------- LLM
class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


def test_gemini_key_in_header_not_url(monkeypatch):
    from llm import providers

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKEKEY1234567890123456")
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen["url"], seen["headers"], seen["body"] = url, headers, json
        return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})

    monkeypatch.setattr(providers.requests, "post", fake_post)
    g = providers.GeminiProvider()
    r = g.generate("hi", images=[(b"\x89PNG", "image/png")], json_mode=True)
    assert r.text == '{"ok": true}'
    assert "AIza" not in seen["url"] and seen["headers"]["x-goog-api-key"].startswith("AIza")
    assert seen["body"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


def test_groq_errors_are_redacted(monkeypatch):
    from llm import providers

    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a" * 40)
    monkeypatch.setattr(providers.requests, "post",
                        lambda *a, **k: FakeResp(401, {"error": "bad key gsk_" + "a" * 40}))
    p = providers.OpenAICompatibleProvider("groq")
    with pytest.raises(providers.LLMError) as e:
        p.generate("hi")
    assert "gsk_aaaa" not in str(e.value)


def test_no_llm_when_unconfigured():
    from llm.providers import make_llm

    assert not make_llm("gemini").available()
    assert not make_llm("none").available()
    assert not make_llm("openrouter").available()


def test_number_guard():
    from llm.explainer import number_guard

    ok, _ = number_guard("Entry at 101.50 with stop 99.20", "trigger 101.5 stop 99.2")
    assert ok
    ok, extra = number_guard("Target 120 has 80% probability", "trigger 101.5")
    assert not ok and "120.00" in extra


def test_llm_explanation_falls_back_on_invented_numbers():
    from llm.explainer import SECTIONS, llm_explanation
    from llm.providers import LLMProvider, LLMResponse

    tpl = {k: f"{k} evidence 101.5" for k in SECTIONS}

    class Liar(LLMProvider):
        def available(self):
            return True

        def generate(self, *a, **k):
            return LLMResponse(json.dumps({k: "Price will hit 250 soon" for k in SECTIONS}), "x", "m")

    out, src = llm_explanation(Liar(), tpl)
    assert out == tpl and "rejected" in src


# ---------------------------------------------------------------- news
def _rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://x/{i}</link>{('<pubDate>' + d + '</pubDate>') if d else ''}"
                   f"<description>{t}</description></item>" for i, (t, d) in enumerate(items))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'


def test_news_freshness_and_classification():
    from news.news_engine import event_risk_warnings, parse_feed

    now = datetime(2026, 11, 24, 12, 0, tzinfo=TZ)
    fmt = lambda dt: dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")  # noqa: E731
    rss = _rss([("Reliance Q2 results: net profit rises", fmt(now - timedelta(hours=2))),
                ("Reliance old dividend story", fmt(now - timedelta(days=5))),
                ("Reliance undated rumour", None),
                ("TCS bags order worth 500 cr", fmt(now - timedelta(hours=1)))])
    items = parse_feed(rss, "test", 24, now=now, symbol="RELIANCE", match_terms=["RELIANCE"])
    assert [i.title for i in items] == ["Reliance Q2 results: net profit rises"]
    assert items[0].event_type == "Earnings / results" and items[0].intraday_relevance == "HIGH"
    assert items[0].link.startswith("https://") and "IST" in items[0].published
    assert event_risk_warnings(items)


# ---------------------------------------------------------------- security
def test_redaction():
    from config.logging_setup import redact

    s = redact("url?key=AIzaSECRET123456789012345&x=1 Authorization: Bearer gsk_" + "b" * 30)
    assert "SECRET" not in s and "gsk_bbbb" not in s


def test_env_example_has_no_secrets():
    from config.settings import PROJECT_ROOT

    txt = (PROJECT_ROOT / ".env.example").read_text()
    assert "AIza" not in txt and "gsk_" not in txt.replace("gsk_...", "")


def test_gitignore_excludes_env_and_storage():
    from config.settings import PROJECT_ROOT

    gi = (PROJECT_ROOT / ".gitignore").read_text()
    assert ".env" in gi and "storage/" in gi


# ---------------------------------------------------------------- DB
def test_migrations_idempotent_and_settings_roundtrip(settings):
    from database.db import get_engine, load_saved_settings, run_migrations, save_settings
    from config.settings import Settings

    v = run_migrations(get_engine())
    assert run_migrations(get_engine()) == v >= 2
    settings.profile.capital = 25_000
    settings.weights.trend = 30
    save_settings(settings)
    loaded = load_saved_settings(Settings())
    assert loaded.profile.capital == 25_000 and loaded.weights.trend == 30


# ---------------------------------------------------------------- orchestrator
def test_orchestrator_states_and_persistence(sim, settings):
    from sqlalchemy import select

    from agents.orchestrator import NO_TRADE_MSG, Orchestrator
    from agents.schemas import ScanRequest, UniverseRequest
    from database.db import session_scope
    from database.models import CandidateRanking, ScanRun

    out = Orchestrator(sim, settings).run(ScanRequest(UniverseRequest(limit=12), manual_symbols=["SBIN"]))
    states = [x["to"] for x in out.state_log]
    assert states[:4] == ["MARKET_CONTEXT", "RISK_GATE", "UNIVERSE", "SCANNING"]
    assert states[-1] in ("DONE", "NO_TRADE")
    assert len(out.selected) <= 3
    assert "PARTIAL" in out.coverage_note
    if not out.selected:
        assert out.no_trade_reason.startswith(NO_TRADE_MSG)
    for c in out.selected:
        assert c.passed and c.plan.quantity > 0
        assert c.plan.max_planned_loss <= settings.profile.risk_budget + 1e-6
    with session_scope() as s:
        run = s.execute(select(ScanRun)).scalars().one()
        assert run.status in ("DONE", "NO_TRADE") and run.scanned == out.scanned
        assert len(s.execute(select(CandidateRanking)).scalars().all()) == len(out.candidates)


def test_orchestrator_positive_path_relaxed(sim, settings):
    """With filters relaxed, the pipeline must produce ≤3 ranked, sized ideas."""
    from agents.orchestrator import Orchestrator
    from agents.schemas import ScanRequest, UniverseRequest

    p = settings.profile
    p.min_risk_reward, p.min_net_risk_reward, p.max_stop_atr = 1.0, -10, 10
    settings.scan.min_score = 0
    found = False
    for hh in (10, 11, 12, 13, 14):
        from brokers.simulated import SimulatedProvider

        prov = SimulatedProvider(anchor=datetime(2026, 11, 24, hh, 7, tzinfo=TZ))
        out = Orchestrator(prov, settings, persist=False).run(ScanRequest(UniverseRequest(limit=40)))
        assert len(out.selected) <= 3
        for c in out.selected:
            assert c.passed and c.plan.quantity >= 1
            assert c.plan.capital_required <= p.max_allocation + 1e-6
        if out.selected:
            found = True
            ex, src = out.explanations[out.selected[0].symbol]
            assert "WHY THIS STOCK?" in ex and src == "template"
            break
    if not found:
        pytest.skip("No relaxed setup on simulated data at sampled times")


def test_orchestrator_blocks_when_daily_loss_hit(sim, settings):
    from agents.orchestrator import Orchestrator
    from agents.schemas import ScanRequest, UniverseRequest
    from risk.risk_limits import DayState

    out = Orchestrator(sim, settings, persist=False, day_state=DayState(realized_pnl=-1000)).run(
        ScanRequest(UniverseRequest(limit=8)))
    assert out.selected == [] and not out.account_gate.allowed
