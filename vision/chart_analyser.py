"""Chart screenshot analysis: OCR text parsing + OpenCV geometry + optional
free-tier vision LLM. Produces ScreenshotEvidence with per-field provenance.

Rules:
* Nothing is guessed – fields stay empty when unreadable.
* Screenshot prices are NEVER used as current market prices; they are only
  compared against freshly fetched data in vision/verification.py.
"""
from __future__ import annotations

import difflib
import re
import uuid

import cv2
import numpy as np
from pydantic import ValidationError

from config.logging_setup import get_logger
from llm.providers import LLMError, LLMProvider, extract_json
from vision.image_preprocessor import LoadedImage, candle_masks, ocr_ready, sharpness
from vision.ocr import Word, lines_from_words, ocr_words, tesseract_available
from vision.schemas import LLMChartReading, Observed, ScreenshotEvidence

log = get_logger("vision")

_NUM = re.compile(r"^[₹$]?\(?([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\)?$")
_TF_PATTERNS = [
    (re.compile(r"\b(\d{1,3})\s?(?:m|min|mins|minute|minutes)\b", re.I), "m"),
    (re.compile(r"\b(\d)\s?(?:h|hr|hrs|hour|hours)\b", re.I), "h"),
    (re.compile(r"\b(1)?\s?(?:d|day|daily)\b", re.I), "d"),
]
_TV_TITLE = re.compile(r"([A-Z][A-Z0-9&\-]{1,19}?)\s*[·•\-,:|]\s*(\d{1,3}[mMhHdDwW]?|[1]?[DWM])\s*[·•\-,:|]\s*(NSE|BSE)", re.I)
_OHLC_CLOSE = re.compile(r"\bC\s*[:=]?\s*([0-9][0-9,]*\.[0-9]+)")
_DATE = re.compile(r"\b(\d{1,2}[-/ ](?:\d{1,2}|[A-Za-z]{3})[-/ ]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\b")
_IND = {
    "ema": re.compile(r"\bEMA\s*\(?\s*(\d{1,3})", re.I),
    "sma": re.compile(r"\b(?:SMA|MA)\s*\(?\s*(\d{1,3})", re.I),
}
SUPPORTED_TF = [("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60), ("1d", 1440)]

VISION_PROMPT = """You are reading a stock chart screenshot. Report ONLY what is clearly visible.
Return a JSON object with exactly these keys (use null or [] when not visible – never guess):
symbol, exchange, timeframe (e.g. "5m","15m","1h","1d"), last_price (number), timestamp (string as shown),
ema_periods (list of ints), vwap_visible (bool), rsi_value (0-100), macd_state ("bullish"/"bearish"/"neutral"),
support (list of price numbers that are explicitly drawn or labelled), resistance (list), patterns (list of
candlestick/chart pattern names visible on the right edge), trend ("up"/"down"/"sideways"),
unreadable (list of field names you could not read)."""


def normalise_timeframe(raw: str | None) -> tuple[str | None, str]:
    """Map '5', '5m', '15 min', '1H', 'D' to a supported interval. Returns (interval, note)."""
    if not raw:
        return None, ""
    s = raw.strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,3})(m|min|mins|minute|minutes)?$", s)
    minutes = None
    if m:
        minutes = int(m.group(1))
    elif re.match(r"^(\d)(h|hr|hour|hours)$", s):
        minutes = int(s[0]) * 60
    elif s in {"d", "1d", "day", "daily", "1day"}:
        minutes = 1440
    elif s in {"w", "1w", "m1"}:
        return None, f"Timeframe '{raw}' (weekly/monthly) not supported for intraday"
    if minutes is None:
        return None, f"Unrecognised timeframe '{raw}'"
    best = min(SUPPORTED_TF, key=lambda t: (abs(t[1] - minutes), -t[1]))  # ties → slower timeframe
    note = "" if best[1] == minutes else f"Screenshot timeframe {raw} mapped to nearest supported {best[0]}"
    return best[0], note


def _num(text: str) -> float | None:
    m = _NUM.match(text.strip())
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fit_price_axis(words: list[Word], width: int, height: int) -> dict | None:
    """Linear map y_pixel -> price from numeric labels on the right axis."""
    pts = [(w.cy, _num(w.text)) for w in words if w.cx > width * 0.85 and _num(w.text) is not None]
    pts = [(y, v) for y, v in pts if v and v > 0]
    if len(pts) < 3:
        return None
    ys = np.array([p[0] for p in pts])
    vs = np.array([p[1] for p in pts])
    # robust: drop points far from median-based fit
    for _ in range(2):
        if len(ys) < 3:
            return None
        a, b = np.polyfit(ys, vs, 1)
        resid = np.abs(vs - (a * ys + b))
        keep = resid <= max(np.median(resid) * 3, 1e-6)
        ys, vs = ys[keep], vs[keep]
    if len(ys) < 3:
        return None
    a, b = np.polyfit(ys, vs, 1)
    pred = a * ys + b
    ss_res = float(((vs - pred) ** 2).sum())
    ss_tot = float(((vs - vs.mean()) ** 2).sum()) or 1e-9
    r2 = 1 - ss_res / ss_tot
    if a >= 0 or r2 < 0.98:
        return None
    return {"slope": float(a), "intercept": float(b), "r2": round(r2, 4), "points": int(len(ys))}


def y_to_price(axis: dict, y: float) -> float:
    return axis["slope"] * y + axis["intercept"]


def detect_trend(rgb: np.ndarray) -> tuple[str | None, float, str]:
    """Fit a line through candle-pixel centroids in the plot area."""
    h, w = rgb.shape[:2]
    g, r = candle_masks(rgb)
    mask = cv2.bitwise_or(g, r)
    plot = mask[int(h * 0.08): int(h * 0.80), : int(w * 0.88)]
    cols = np.where(plot.sum(axis=0) > 0)[0]
    if len(cols) < w * 0.15:
        return None, 0.0, "Too few candle pixels for trend detection"
    xs, ys = [], []
    for x in cols[:: max(1, len(cols) // 300)]:
        yy = np.where(plot[:, x] > 0)[0]
        xs.append(x)
        ys.append(yy.mean())
    xs, ys = np.array(xs, float), np.array(ys, float)
    recent = xs >= np.percentile(xs, 40)  # focus on the more recent 60% of the chart
    xs, ys = xs[recent], ys[recent]
    a, b = np.polyfit(xs, ys, 1)
    pred = a * xs + b
    r2 = 1 - ((ys - pred) ** 2).sum() / (((ys - ys.mean()) ** 2).sum() or 1e-9)
    drift = -a * (xs.max() - xs.min()) / plot.shape[0]  # +ve = price rising
    if abs(drift) < 0.08 or r2 < 0.3:
        return "sideways", round(min(0.6, 0.3 + abs(r2) * 0.3), 2), f"Drift {drift:+.2f} of plot height"
    trend = "up" if drift > 0 else "down"
    conf = round(min(0.8, 0.35 + max(r2, 0) * 0.45), 2)
    return trend, conf, f"Candle centroid drift {drift:+.2f} of plot height (R² {r2:.2f})"


def rightmost_candle_y(rgb: np.ndarray) -> float | None:
    h, w = rgb.shape[:2]
    g, r = candle_masks(rgb)
    mask = cv2.bitwise_or(g, r)[int(h * 0.08): int(h * 0.80), : int(w * 0.88)]
    cols = np.where(mask.sum(axis=0) > 0)[0]
    if len(cols) == 0:
        return None
    x = cols.max()
    yy = np.where(mask[:, max(0, x - 3): x + 1].sum(axis=1) > 0)[0]
    return float(yy.mean() + int(h * 0.08)) if len(yy) else None


def detect_horizontal_lines(rgb: np.ndarray) -> list[float]:
    """Saturated (coloured) long horizontal lines – often user-drawn S/R.
    Grey gridlines are ignored by requiring colour saturation."""
    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = cv2.inRange(hsv, (0, 90, 90), (180, 255, 255))
    g, r = candle_masks(rgb)
    sat = cv2.bitwise_and(sat, cv2.bitwise_not(cv2.bitwise_or(g, r)))
    lines = cv2.HoughLinesP(sat, 1, np.pi / 180, threshold=80, minLineLength=int(w * 0.35), maxLineGap=12)
    ys: list[float] = []
    if lines is None:
        return ys
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        if abs(y2 - y1) <= 2 and h * 0.05 < y1 < h * 0.85:
            y = (y1 + y2) / 2
            if all(abs(y - e) > 6 for e in ys):
                ys.append(float(y))
    return sorted(ys)


class ChartAnalyser:
    def __init__(self, universe: dict[str, str], llm: LLMProvider | None = None):
        """universe: {SYMBOL: company name}"""
        self.universe = {k.upper(): v for k, v in universe.items()}
        self._names = {v.upper(): k for k, v in self.universe.items()}
        self.llm = llm

    # ----------------------------------------------------------- symbol
    def find_symbols(self, words: list[Word], lines: list[str], height: int) -> list[tuple[str, float, str]]:
        found: dict[str, tuple[float, str]] = {}
        for line in lines:
            m = _TV_TITLE.search(line.upper())
            if m and m.group(1) in self.universe:
                found[m.group(1)] = (0.95, "TradingView-style title")
        for w in words:
            t = re.sub(r"[^A-Z0-9&\-:]", "", w.text.upper()).replace("NSE:", "").replace("BSE:", "").strip(":-")
            if t in self.universe and len(t) >= 2:
                conf = 0.85 if w.cy < height * 0.2 else 0.6
                conf *= min(1.0, max(w.conf, 30) / 90)
                if conf > found.get(t, (0, ""))[0]:
                    found[t] = (round(conf, 2), "exact symbol text" + (" in title area" if w.cy < height * 0.2 else ""))
        joined = [l.upper() for l in lines[:12]]
        for name_up, sym in self._names.items():
            if sym in found:
                continue
            for l in joined:
                if len(name_up) >= 6 and name_up in l:
                    found[sym] = (0.7, "company name match")
                    break
                r = difflib.SequenceMatcher(None, name_up, l[: len(name_up) + 4]).ratio()
                if len(name_up) >= 8 and r >= 0.88:
                    found[sym] = (0.55, f"fuzzy company-name match ({r:.2f})")
                    break
        return sorted(((s, c, n) for s, (c, n) in found.items()), key=lambda x: -x[1])

    # ----------------------------------------------------------- main
    def analyse(self, img: LoadedImage, use_llm: bool = True) -> ScreenshotEvidence:
        ev = ScreenshotEvidence(image_id=uuid.uuid4().hex[:12], filename=img.filename, sha256=img.sha256,
                                width=img.width, height=img.height)
        rgb = img.rgb
        sharp = sharpness(rgb)
        words: list[Word] = []
        if tesseract_available():
            gray = ocr_ready(rgb)
            scale = gray.shape[1] / rgb.shape[1]
            try:
                words = ocr_words(gray, scale)
            except Exception as exc:  # noqa: BLE001
                ev.flags.append(f"OCR failed: {type(exc).__name__}")
        else:
            ev.flags.append("Tesseract OCR not installed – text extraction skipped")
        lines = lines_from_words(words, y_tol=max(6, img.height // 120))
        ev.raw_text = "\n".join(lines)[:4000]
        mean_conf = float(np.mean([w.conf for w in words])) if words else 0.0
        res_score = min(1.0, (img.width * img.height) / (1280 * 720))
        sharp_score = min(1.0, sharp / 300)
        ev.readability = round(0.4 * res_score + 0.3 * sharp_score + 0.3 * (mean_conf / 100), 2)
        ev.readability_note = (f"{img.width}×{img.height}px, sharpness {sharp:.0f}, OCR words {len(words)}, "
                               f"mean OCR confidence {mean_conf:.0f}%")
        if ev.readability < 0.35:
            ev.flags.append("Low readability – results unreliable")

        # symbol & exchange
        syms = self.find_symbols(words, lines, img.height)
        ev.symbol_candidates = [s for s, _, _ in syms]
        if syms:
            s, c, note = syms[0]
            ambiguous = len(syms) > 1 and syms[1][1] >= c - 0.1
            ev.symbol = Observed(value=s, confidence=c, source="ocr", flagged=ambiguous,
                                 note=note + ("; multiple symbols visible – please confirm" if ambiguous else ""))
        else:
            ev.symbol = Observed(flagged=True, note="No known NSE symbol found in text")
        up = " ".join(lines).upper()
        if re.search(r"\bNSE\b", up):
            ev.exchange = Observed(value="NSE", confidence=0.8, source="ocr")
        elif re.search(r"\bBSE\b", up):
            ev.exchange = Observed(value="BSE", confidence=0.8, source="ocr",
                                   note="BSE chart – app verifies against NSE data")

        # timeframe
        tf_raw = None
        for l in lines[:15]:
            m = _TV_TITLE.search(l.upper())
            if m:
                tf_raw = m.group(2)
                break
        if not tf_raw:
            for pat, unit in _TF_PATTERNS:
                m = pat.search(" ".join(lines[:15]))
                if m:
                    tf_raw = (m.group(1) or "1") + unit
                    break
        tf, tf_note = normalise_timeframe(tf_raw)
        ev.timeframe = Observed(value=tf, confidence=0.75 if tf else 0.0, source="ocr" if tf else "none",
                                flagged=bool(tf_note) or tf is None, note=tf_note or ("" if tf else "Timeframe not found"))

        # axis, price, timestamp
        axis = fit_price_axis(words, img.width, img.height)
        ev.price_axis = axis
        m = _OHLC_CLOSE.search(" ".join(lines))
        if m:
            ev.visible_price = Observed(value=float(m.group(1).replace(",", "")), confidence=0.75, source="ocr",
                                        note="Close value from OHLC legend (screenshot price – NOT live)")
        elif axis:
            y = rightmost_candle_y(rgb)
            if y is not None:
                ev.visible_price = Observed(value=round(y_to_price(axis, y), 2), confidence=0.4, source="opencv",
                                            flagged=True, note="Estimated from last candle position on price axis")
        dm, tm = _DATE.search(ev.raw_text), _TIME.search(ev.raw_text)
        if dm or tm:
            ev.timestamp = Observed(value=" ".join(x.group(0) for x in (dm, tm) if x), confidence=0.6, source="ocr")
        else:
            ev.timestamp = Observed(flagged=True, note="No timestamp visible – screenshot age unknown")

        # indicators
        emas = sorted({int(x) for x in _IND["ema"].findall(ev.raw_text) if 2 <= int(x) <= 400})
        if emas:
            ev.ema_lines = Observed(value=emas, confidence=0.75, source="ocr")
        if re.search(r"\bVWAP\b", up):
            ev.vwap = Observed(value=True, confidence=0.8, source="ocr")
        rm = re.search(r"\bRSI\b[^0-9]{0,15}(\d{1,3})?[^0-9]{0,10}(\d{1,2}\.\d{1,2})", ev.raw_text, re.I)
        if rm:
            v = float(rm.group(2))
            if 0 <= v <= 100:
                ev.rsi = Observed(value=v, confidence=0.55, source="ocr", note="RSI value read from legend")
        elif re.search(r"\bRSI\b", up):
            ev.rsi = Observed(value=None, confidence=0.5, source="ocr", note="RSI pane visible, value unreadable")
        if re.search(r"\bMACD\b", up):
            ev.macd = Observed(value="visible", confidence=0.6, source="ocr", note="MACD pane visible")

        # trend and drawn levels
        tr, conf, note = detect_trend(rgb)
        ev.trend = Observed(value=tr, confidence=conf, source="opencv" if tr else "none", note=note,
                            flagged=tr is None)
        if axis:
            ys = detect_horizontal_lines(rgb)
            ref = ev.visible_price.value
            prices = [round(y_to_price(axis, y), 2) for y in ys]
            if prices and ref:
                sup = [p for p in prices if p < ref]
                res = [p for p in prices if p > ref]
                if sup:
                    ev.support_levels = Observed(value=sup, confidence=0.4, source="opencv", flagged=True,
                                                 note="Coloured horizontal lines below price (may be indicators)")
                if res:
                    ev.resistance_levels = Observed(value=res, confidence=0.4, source="opencv", flagged=True,
                                                    note="Coloured horizontal lines above price (may be indicators)")
        else:
            ev.flags.append("Price axis not readable – pixel levels cannot be converted to prices")

        if use_llm and self.llm is not None and self.llm.available():
            self._merge_llm(img, ev)
        return ev

    def _merge_llm(self, img: LoadedImage, ev: ScreenshotEvidence) -> None:
        try:
            resp = self.llm.generate(VISION_PROMPT, images=[(img.data, img.mime)], json_mode=True, max_tokens=700)
            reading = LLMChartReading.model_validate(extract_json(resp.text))
        except ValidationError as exc:
            ev.llm_note = f"Vision LLM output failed schema validation ({exc.error_count()} errors) – ignored"
            return
        except (LLMError, ValueError) as exc:
            ev.llm_note = f"Vision LLM not used: {str(exc)[:160]}"
            return
        ev.llm_used = True
        ev.llm_note = f"{resp.provider}:{resp.model}"

        def fill(field: str, value, conf: float = 0.5, note: str = ""):
            cur: Observed = getattr(ev, field)
            if value in (None, [], ""):
                return
            if cur.value in (None, [], ""):
                setattr(ev, field, Observed(value=value, confidence=conf, source="llm", note=note or "From vision LLM"))
            elif str(cur.value).upper() != str(value).upper() and field not in ("support_levels", "resistance_levels"):
                cur.flagged = True
                cur.note = (cur.note + f"; LLM read '{value}' (disagrees)").strip("; ")

        if reading.symbol:
            if reading.symbol in self.universe:
                if reading.symbol not in ev.symbol_candidates:
                    ev.symbol_candidates.append(reading.symbol)
                fill("symbol", reading.symbol, 0.55)
            else:
                ev.flags.append(f"LLM read symbol '{reading.symbol}' which is not in the NSE universe")
        if reading.exchange:
            fill("exchange", reading.exchange.upper(), 0.5)
        tf, tf_note = normalise_timeframe(reading.timeframe)
        if tf:
            fill("timeframe", tf, 0.5, tf_note)
        fill("visible_price", reading.last_price, 0.45, "LLM-read screenshot price (NOT live)")
        fill("timestamp", reading.timestamp, 0.4)
        fill("ema_lines", reading.ema_periods or None, 0.5)
        if reading.vwap_visible:
            fill("vwap", True, 0.5)
        fill("rsi", reading.rsi_value, 0.45)
        fill("macd", reading.macd_state, 0.45)
        fill("support_levels", reading.support or None, 0.35)
        fill("resistance_levels", reading.resistance or None, 0.35)
        fill("patterns", reading.patterns or None, 0.35, "Patterns named by vision LLM (unverified)")
        fill("trend", reading.trend, 0.45)
        for f in reading.unreadable:
            ev.flags.append(f"LLM could not read: {f}")
