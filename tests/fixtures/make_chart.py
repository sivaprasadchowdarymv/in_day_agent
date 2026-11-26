"""Generate synthetic chart screenshots for tests and demos (no real data)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_chart(symbol: str = "RELIANCE", tf: str = "15", trend: str = "up", start: float = 2400.0,
               dark: bool = False, w: int = 1400, h: int = 800, rsi: float | None = 62.35,
               draw_level: bool = True, title: bool = True, seed: int = 7, fmt: str = "PNG") -> tuple[bytes, float]:
    rng = np.random.default_rng(seed)
    bg, fg = ((19, 23, 34), (220, 220, 220)) if dark else ((255, 255, 255), (20, 20, 20))
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    f_big, f = _font(30), _font(22)
    n = 60
    drift = {"up": 0.0018, "down": -0.0018, "sideways": 0.0}[trend]
    closes = start * np.exp(np.cumsum(rng.normal(drift, 0.002, n)))
    opens = np.concatenate([[start], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.001, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.001, n)))
    pmin, pmax = lows.min() * 0.998, highs.max() * 1.002
    top, bot, right = int(h * 0.12), int(h * 0.78), int(w * 0.88)

    def y(p):
        return top + (pmax - p) / (pmax - pmin) * (bot - top)

    step = (right - 40) / n
    for i in range(n):
        x = 40 + i * step + step / 2
        col = (38, 166, 91) if closes[i] >= opens[i] else (239, 83, 80)
        d.line([(x, y(highs[i])), (x, y(lows[i]))], fill=col, width=2)
        y1, y2 = sorted([y(opens[i]), y(closes[i])])
        d.rectangle([x - step * 0.35, y1, x + step * 0.35, max(y2, y1 + 2)], fill=col)
    # price axis labels
    for k in range(7):
        p = pmin + (pmax - pmin) * k / 6
        d.text((right + 12, y(p) - 11), f"{p:,.2f}", fill=fg, font=f)
    if draw_level:
        lvl = float(np.percentile(lows, 15))
        d.line([(40, y(lvl)), (right, y(lvl))], fill=(41, 98, 255), width=3)
    if title:
        d.text((20, 15), f"{symbol} · {tf} · NSE", fill=fg, font=f_big)
    d.text((20, 55), f"O {opens[-1]:.2f}  H {highs[-1]:.2f}  L {lows[-1]:.2f}  C {closes[-1]:.2f}", fill=fg, font=f)
    d.text((620, 55), "EMA 9   EMA 21   VWAP", fill=fg, font=f)
    if rsi is not None:
        d.text((20, int(h * 0.84)), f"RSI 14  {rsi:.2f}", fill=fg, font=f)
    d.text((20, h - 40), "24-11-2026 14:15", fill=fg, font=f)
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue(), float(closes[-1])
