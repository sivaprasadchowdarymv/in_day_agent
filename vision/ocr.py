"""Tesseract OCR wrapper returning words with positions and confidence."""
from __future__ import annotations

import shutil
from dataclasses import dataclass

import numpy as np

from config.logging_setup import get_logger

log = get_logger("ocr")


@dataclass
class Word:
    text: str
    conf: float  # 0..100
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def tesseract_available() -> bool:
    try:
        import pytesseract

        cmd = pytesseract.pytesseract.tesseract_cmd
        return bool(shutil.which(cmd) or shutil.which("tesseract"))
    except ImportError:
        return False


def configure_tesseract(path: str | None) -> None:
    """On Windows set e.g. C:\\Program Files\\Tesseract-OCR\\tesseract.exe"""
    if path:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = path


def ocr_words(gray: np.ndarray, scale: float = 1.0, psm: int = 11) -> list[Word]:
    """Run OCR; coordinates are divided by `scale` to map back to the original image."""
    import pytesseract

    data = pytesseract.image_to_data(gray, config=f"--psm {psm} --oem 3", output_type=pytesseract.Output.DICT)
    out = []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not t or conf < 0:
            continue
        out.append(Word(t, conf, int(data["left"][i] / scale), int(data["top"][i] / scale),
                        int(data["width"][i] / scale), int(data["height"][i] / scale)))
    return out


def lines_from_words(words: list[Word], y_tol: int = 8) -> list[str]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x)):
        if rows and abs(rows[-1][0].cy - w.cy) <= y_tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [" ".join(x.text for x in sorted(r, key=lambda w: w.x)) for r in rows]
