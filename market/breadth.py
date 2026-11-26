"""Advance/decline breadth computed from the stocks actually scanned.
This is NOT full-market breadth unless the whole universe was scanned; the
result always reports its coverage."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Breadth:
    advances: int
    declines: int
    unchanged: int
    sample_size: int
    universe_size: int

    @property
    def ratio(self) -> float | None:
        return self.advances / self.declines if self.declines else None

    @property
    def coverage_note(self) -> str:
        return (f"Breadth from {self.sample_size} scanned stocks out of {self.universe_size} in the universe"
                + ("" if self.sample_size >= self.universe_size else " (partial – not full market breadth)"))


def compute_breadth(changes_pct: dict[str, float], universe_size: int, flat_band: float = 0.05) -> Breadth:
    adv = sum(1 for v in changes_pct.values() if v > flat_band)
    dec = sum(1 for v in changes_pct.values() if v < -flat_band)
    return Breadth(adv, dec, len(changes_pct) - adv - dec, len(changes_pct), universe_size)
