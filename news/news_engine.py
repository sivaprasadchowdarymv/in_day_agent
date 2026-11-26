"""News & corporate-event intelligence from free RSS feeds.

* Items without a publication timestamp, or older than `max_age_hours`, are
  discarded – an old article is never treated as current news.
* Every item keeps its source name, link and timestamp.
* News NEVER raises a candidate's score. It only adds event-risk warnings
  (e.g. results due today) so the trader can decide. No LLM-generated news.
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests

from config.logging_setup import get_logger
from config.settings import PROJECT_ROOT
from data.market_hours import TZ, now_ist

log = get_logger("news")
SOURCES_PATH = PROJECT_ROOT / "config" / "news_sources.json"
UA = {"User-Agent": "Mozilla/5.0 (BharatTradeAI research tool)", "Accept": "application/rss+xml, application/xml"}

EVENT_RULES = [
    ("Earnings / results", r"\b(results?|earnings|quarterly|q[1-4]\s?fy|profit|revenue|net loss)\b", "HIGH"),
    ("Corporate action", r"\b(dividend|bonus|split|buy-?back|rights issue|record date)\b", "MEDIUM"),
    ("Order / contract", r"\b(order win|bags order|contract|order worth|letter of award)\b", "HIGH"),
    ("Regulatory", r"\b(sebi|rbi|penalty|show cause|investigation|ban|nclt|insolvency)\b", "HIGH"),
    ("M&A / fund-raise", r"\b(acquisition|acquire|merger|stake|qip|fund raise|preferential|block deal|bulk deal)\b", "HIGH"),
    ("Rating / brokerage", r"\b(upgrade|downgrade|target price|rating)\b", "MEDIUM"),
    ("Board meeting", r"\b(board meeting|agm|egm)\b", "MEDIUM"),
    ("Management", r"\b(ceo|cfo|resign|appoint|managing director)\b", "MEDIUM"),
]


@dataclass
class NewsItem:
    symbol: str | None
    title: str
    link: str
    source: str
    published: str
    age_hours: float
    event_type: str
    intraday_relevance: str
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_sources() -> dict:
    try:
        return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"broad": [], "per_symbol": []}


def classify_event(text: str) -> tuple[str, str]:
    t = text.lower()
    for name, pat, rel in EVENT_RULES:
        if re.search(pat, t):
            return name, rel
    return "General news", "LOW"


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).astimezone(TZ)
    return None


def parse_feed(content: bytes | str, source: str, max_age_hours: float, now: datetime | None = None,
               symbol: str | None = None, match_terms: list[str] | None = None) -> list[NewsItem]:
    import feedparser

    now = now or now_ist()
    feed = feedparser.parse(content)
    out = []
    for e in feed.entries:
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        if not title:
            continue
        pub = _published(e)
        if pub is None:
            continue  # undated items are never treated as current
        age = (now - pub).total_seconds() / 3600
        if age < -1 or age > max_age_hours:
            continue
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:400].strip()
        if match_terms:
            hay = (title + " " + summary).upper()
            if not any(re.search(rf"\b{re.escape(t.upper())}\b", hay) for t in match_terms if t):
                continue
        et, rel = classify_event(title + " " + summary)
        out.append(NewsItem(symbol, title, e.get("link", ""), source, pub.strftime("%Y-%m-%d %H:%M IST"),
                            round(max(age, 0), 1), et, rel, summary))
    return out


def fetch(url: str, timeout: int = 12) -> bytes:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.content


def news_for_symbols(symbols: dict[str, str], max_age_hours: float = 24, include_broad: bool = True,
                     per_symbol: bool = True) -> tuple[dict[str, list[NewsItem]], list[str]]:
    """symbols: {SYMBOL: company name}. Returns ({symbol: items}, errors)."""
    cfg = load_sources()
    res: dict[str, list[NewsItem]] = {s: [] for s in symbols}
    errors: list[str] = []
    if include_broad:
        for src in [s for s in cfg.get("broad", []) if s.get("enabled", True)]:
            try:
                raw = fetch(src["url"])
            except requests.RequestException as exc:
                errors.append(f"{src['name']}: {type(exc).__name__}")
                continue
            for sym, name in symbols.items():
                short = name.split(" Limited")[0].split(" Ltd")[0]
                res[sym].extend(parse_feed(raw, src["name"], max_age_hours, symbol=sym, match_terms=[sym, short]))
    if per_symbol:
        for src in [s for s in cfg.get("per_symbol", []) if s.get("enabled", True)]:
            for sym, name in symbols.items():
                q = quote_plus(f'"{name}" OR "{sym}" NSE share')
                try:
                    raw = fetch(src["url"].format(query=q))
                except requests.RequestException as exc:
                    errors.append(f"{src['name']} ({sym}): {type(exc).__name__}")
                    continue
                res[sym].extend(parse_feed(raw, src["name"], max_age_hours, symbol=sym,
                                           match_terms=[sym, name.split(" ")[0]]))
    for sym in res:
        seen, uniq = set(), []
        for it in sorted(res[sym], key=lambda x: x.age_hours):
            k = it.title.lower()[:80]
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        res[sym] = uniq
    return res, errors


def event_risk_warnings(items: list[NewsItem]) -> list[str]:
    w = []
    for it in items:
        if it.intraday_relevance == "HIGH" and it.age_hours <= 18:
            w.append(f"Event risk: {it.event_type} – “{it.title[:90]}” ({it.source}, {it.published})")
    return w[:3]


def test_sources(timeout: int = 8) -> list[dict]:
    """Health check for every configured feed (used by Settings → Data sources)."""
    import feedparser

    cfg = load_sources()
    out = []
    for group in ("broad", "per_symbol"):
        for src in cfg.get(group, []):
            url = src["url"].format(query=quote_plus("NIFTY NSE")) if "{query}" in src["url"] else src["url"]
            row = {"source": src["name"], "enabled": src.get("enabled", True), "ok": False, "items": 0,
                   "latest": None, "error": None}
            try:
                feed = feedparser.parse(fetch(url, timeout))
                row["items"] = len(feed.entries)
                dates = [p for p in (_published(e) for e in feed.entries) if p]
                row["latest"] = max(dates).strftime("%Y-%m-%d %H:%M IST") if dates else None
                row["ok"] = row["items"] > 0
            except requests.RequestException as exc:
                row["error"] = type(exc).__name__
            out.append(row)
    return out
