"""Structured logging with secret redaction."""
from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler

from config.settings import LOG_DIR

_SECRET_PATTERNS = [
    re.compile(r"(key=)[^&\s\"']+", re.I),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.I),
    re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\s\"',}]+", re.I),
    re.compile(r"(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.I),
    re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,})"),
    re.compile(r"\b(gsk_[0-9A-Za-z]{20,})"),
    re.compile(r"\b(sk-or-[0-9A-Za-z\-]{20,})"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 1 and pat.pattern.startswith("("):
            text = pat.sub(lambda m: m.group(1) + "***REDACTED***", text)
        else:
            text = pat.sub("***REDACTED***", text)
    return text


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = RotatingFileHandler(LOG_DIR / "bharattrade.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(RedactingJsonFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(RedactingJsonFormatter())
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("urllib3", "yfinance", "peewee", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"bharattrade.{name}")
