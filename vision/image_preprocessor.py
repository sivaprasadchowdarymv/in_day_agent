"""Upload validation and OpenCV preprocessing."""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MAX_DIM = 8000
MIN_W, MIN_H = 200, 150
Image.MAX_IMAGE_PIXELS = 50_000_000  # decompression-bomb guard


class ImageValidationError(ValueError):
    pass


@dataclass
class LoadedImage:
    filename: str
    data: bytes
    mime: str
    sha256: str
    rgb: np.ndarray  # H x W x 3 uint8

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    @property
    def height(self) -> int:
        return self.rgb.shape[0]


def safe_filename(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or "image"))
    return keep[-120:] or "image"


def load_and_validate(data: bytes, filename: str, max_mb: int = 8) -> LoadedImage:
    if not data:
        raise ImageValidationError("Empty file")
    if len(data) > max_mb * 1024 * 1024:
        raise ImageValidationError(f"File larger than {max_mb} MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageValidationError(f"Not a valid image: {type(exc).__name__}") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError(f"Unsupported format {fmt}; use PNG, JPG/JPEG or WEBP")
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if w > MAX_DIM or h > MAX_DIM:
            raise ImageValidationError(f"Image too large ({w}×{h}); max {MAX_DIM}px")
        if w < MIN_W or h < MIN_H:
            raise ImageValidationError(f"Image too small ({w}×{h}) to read a chart")
        rgb = np.array(im.convert("RGB"))
    return LoadedImage(safe_filename(filename), data, ALLOWED_FORMATS[fmt], hashlib.sha256(data).hexdigest(), rgb)


def is_dark(rgb: np.ndarray) -> bool:
    return float(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).mean()) < 110


def ocr_ready(rgb: np.ndarray) -> np.ndarray:
    """Grayscale, upscale small images, normalise to dark text on light background."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if is_dark(rgb):
        gray = 255 - gray
    h, w = gray.shape
    if w < 1600:
        f = min(2.5, 1600 / w)
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    gray = cv2.bilateralFilter(gray, 5, 40, 40)
    return gray


def sharpness(rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def candle_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Binary masks of green-ish (bullish) and red-ish (bearish) pixels."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    green = cv2.inRange(hsv, (35, 70, 70), (95, 255, 255))
    red1 = cv2.inRange(hsv, (0, 70, 70), (10, 255, 255))
    red2 = cv2.inRange(hsv, (165, 70, 70), (180, 255, 255))
    return green, cv2.bitwise_or(red1, red2)
