"""Screenshot validation, OCR/OpenCV extraction, LLM JSON parsing, verification."""
import io
import json

import pytest
from PIL import Image

from data.instrument_master import load_seed_universe
from vision.chart_analyser import ChartAnalyser, normalise_timeframe
from vision.image_preprocessor import ImageValidationError, load_and_validate, safe_filename
from vision.ocr import tesseract_available
from vision.schemas import LLMChartReading
from vision.verification import verify_evidence
from tests.fixtures.make_chart import make_chart

UNIVERSE = {i.symbol: i.name for i in load_seed_universe()}
needs_ocr = pytest.mark.skipif(not tesseract_available(), reason="Tesseract not installed")


def test_rejects_non_images_and_bad_sizes():
    with pytest.raises(ImageValidationError):
        load_and_validate(b"not an image", "x.png")
    with pytest.raises(ImageValidationError):
        load_and_validate(b"", "x.png")
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, "PNG")
    with pytest.raises(ImageValidationError):
        load_and_validate(buf.getvalue(), "tiny.png")
    big, _ = make_chart()
    with pytest.raises(ImageValidationError):
        load_and_validate(big, "x.png", max_mb=0)
    gif = io.BytesIO()
    Image.new("RGB", (400, 300)).save(gif, "GIF")
    with pytest.raises(ImageValidationError):
        load_and_validate(gif.getvalue(), "x.gif")


def test_accepts_png_jpg_webp():
    for fmt in ("PNG", "JPEG", "WEBP"):
        data, _ = make_chart(fmt=fmt)
        img = load_and_validate(data, f"chart.{fmt.lower()}")
        assert img.width == 1400 and len(img.sha256) == 64


def test_safe_filename():
    assert "/" not in safe_filename("../../etc/passwd")
    assert safe_filename("") == "image"


@pytest.mark.parametrize("raw,exp", [("5", "5m"), ("15m", "15m"), ("1H", "1h"), ("D", "1d"), ("10", "15m"),
                                     ("2h", "1h"), (None, None), ("abc", None)])
def test_timeframe_normalisation(raw, exp):
    assert normalise_timeframe(raw)[0] == exp


@needs_ocr
def test_extracts_symbol_timeframe_price_trend():
    data, last = make_chart(symbol="RELIANCE", tf="15", trend="up")
    ev = ChartAnalyser(UNIVERSE).analyse(load_and_validate(data, "c.png"))
    assert ev.symbol.value == "RELIANCE" and ev.symbol.source == "ocr"
    assert ev.timeframe.value == "15m"
    assert ev.visible_price.value == pytest.approx(last, abs=0.02)
    assert ev.trend.value == "up"
    assert ev.ema_lines.value == [9, 21] and ev.vwap.value is True
    assert ev.rsi.value == pytest.approx(62.35)
    assert ev.price_axis and ev.price_axis["r2"] > 0.98


@needs_ocr
def test_dark_theme_and_downtrend():
    data, _ = make_chart(symbol="TCS", tf="5", trend="down", dark=True, start=3100)
    ev = ChartAnalyser(UNIVERSE).analyse(load_and_validate(data, "c.png"))
    assert ev.symbol.value == "TCS" and ev.trend.value == "down" and ev.timeframe.value == "5m"


@needs_ocr
def test_missing_symbol_is_not_fabricated():
    data, _ = make_chart(symbol="RELIANCE", title=False)
    ev = ChartAnalyser(UNIVERSE).analyse(load_and_validate(data, "c.png"))
    assert ev.symbol.value is None and ev.symbol.flagged


def test_blank_image_yields_nothing():
    buf = io.BytesIO()
    Image.new("RGB", (800, 500), (255, 255, 255)).save(buf, "PNG")
    ev = ChartAnalyser(UNIVERSE).analyse(load_and_validate(buf.getvalue(), "blank.png"))
    assert ev.symbol.value is None and ev.visible_price.value is None and ev.trend.value is None


def test_llm_schema_validation():
    ok = LLMChartReading.model_validate({"symbol": "nse:sbin", "rsi_value": 55, "trend": "up", "support": [700.5]})
    assert ok.symbol == "SBIN"
    with pytest.raises(Exception):
        LLMChartReading.model_validate({"rsi_value": 150})
    with pytest.raises(Exception):
        LLMChartReading.model_validate({"trend": "moon"})
    with pytest.raises(Exception):
        LLMChartReading.model_validate({"last_price": -5})


def test_extract_json_variants():
    from llm.providers import extract_json

    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json")


@needs_ocr
def test_llm_merge_fills_gaps_and_flags_disagreement():
    from llm.providers import LLMProvider, LLMResponse

    class FakeLLM(LLMProvider):
        name = "fake"

        def available(self):
            return True

        def generate(self, prompt, system="", images=None, json_mode=False, max_tokens=1200):
            return LLMResponse(json.dumps({"symbol": "RELIANCE", "timeframe": "5m", "trend": "up",
                                           "patterns": ["bull flag"], "macd_state": "bullish"}), "fake", "m")

    data, _ = make_chart(symbol="RELIANCE", tf="15")
    ev = ChartAnalyser(UNIVERSE, FakeLLM()).analyse(load_and_validate(data, "c.png"))
    assert ev.llm_used
    assert ev.timeframe.value == "15m" and ev.timeframe.flagged  # OCR kept, disagreement flagged
    assert ev.patterns.value == ["bull flag"] and ev.patterns.source == "llm"


def test_llm_garbage_is_ignored():
    from llm.providers import LLMProvider, LLMResponse

    class BadLLM(LLMProvider):
        def available(self):
            return True

        def generate(self, *a, **k):
            return LLMResponse('{"rsi_value": 900}', "bad", "m")

    data, _ = make_chart()
    ev = ChartAnalyser(UNIVERSE, BadLLM()).analyse(load_and_validate(data, "c.png"))
    assert not ev.llm_used and "schema" in ev.llm_note


@needs_ocr
def test_verification_against_market_data(sim):
    data, _ = make_chart(symbol="RELIANCE")
    ev = ChartAnalyser(UNIVERSE).analyse(load_and_validate(data, "c.png"))
    res = verify_evidence(ev, sim, set(UNIVERSE))
    assert res.status in ("VERIFIED", "VERIFIED_WITH_DISCREPANCIES")
    assert res.current_price_label == "SIMULATED" and res.current_price != ev.visible_price.value
    assert any("Screenshot price" in d for d in res.discrepancies + res.agreements)


def test_unverified_symbol_rejected(sim):
    from vision.schemas import Observed, ScreenshotEvidence

    ev = ScreenshotEvidence(image_id="a", filename="f", sha256="0" * 64, width=1, height=1,
                            symbol=Observed(value="FAKESYM", confidence=0.9, source="ocr"))
    assert verify_evidence(ev, sim, set(UNIVERSE)).status == "UNVERIFIED"
    ev2 = ScreenshotEvidence(image_id="b", filename="f", sha256="0" * 64, width=1, height=1)
    assert verify_evidence(ev2, sim, set(UNIVERSE)).status == "UNVERIFIED"
    # user override works
    assert verify_evidence(ev2, sim, set(UNIVERSE), symbol_override="SBIN").status.startswith("VERIFIED")
