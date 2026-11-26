"""Agent base class: uniform result envelope, timing, error capture, logging."""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from config.logging_setup import get_logger


@dataclass
class AgentResult:
    agent: str
    ok: bool
    output: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0


class BaseAgent:
    name = "BaseAgent"
    input_schema: type | None = None
    output_schema: type | None = None

    def __init__(self):
        self.log = get_logger(f"agent.{self.name}")

    def run(self, payload=None) -> AgentResult:
        if self.input_schema is not None and payload is not None and not isinstance(payload, self.input_schema):
            return AgentResult(self.name, False, errors=[f"Invalid input type {type(payload).__name__}; "
                                                         f"expected {self.input_schema.__name__}"])
        t0 = time.perf_counter()
        try:
            out, warnings = self._run(payload)
            res = AgentResult(self.name, True, out, warnings=warnings or [])
        except Exception as exc:  # noqa: BLE001 - agents must never crash the workflow
            self.log.error(f"{self.name} failed: {exc}\n{traceback.format_exc(limit=3)}")
            res = AgentResult(self.name, False, errors=[f"{type(exc).__name__}: {str(exc)[:300]}"])
        res.duration_ms = int((time.perf_counter() - t0) * 1000)
        self.log.info(f"{self.name} ok={res.ok} in {res.duration_ms} ms")
        return res

    def _run(self, payload):  # pragma: no cover - abstract
        raise NotImplementedError
