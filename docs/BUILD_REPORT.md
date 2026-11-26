# Build report

## Scope change from the original brief
The brief asked for Groww's official API and local Ollama models. At the user's request, the project uses **free sources** instead:

| Original | Replaced by |
|---|---|
| Groww Trade API for market data | Yahoo Finance via `yfinance`, behind the same provider interface (`brokers/base.py`) |
| Ollama vision model | Tesseract OCR + OpenCV, plus an optional free-tier Gemini, Groq or OpenRouter vision model |
| Ollama LLM for explanations | A deterministic template, plus an optional free-tier LLM checked by a number guard |

No existing BharatTrade repository was supplied, so the project was built from scratch using the requested module layout.

## Phase summary
| Phase | Delivered | Tests |
|---|---|---|
| 1 | Architecture, dependency map and roadmap (`docs/ARCHITECTURE.md`) | – |
| 2 | Provider interface, Yahoo adapter (retry, backoff, rate limit, provisional-candle handling, 3m resampling), SQLite candle cache, NSE master/watchlist parsing, liquidity filter, validation and freshness | `test_data_layer.py` (20) |
| 3 | Indicators (EMA, Supertrend, ADX, RSI, MACD, ROC, StochRSI, session VWAP, RVOL, ATR, Bollinger, HV, opening range, PDH/PDL, pivots, swings, S/R, candle patterns), market regime, sector RS, breadth | `test_indicators.py` (16), `test_ranking.py` (regime parts) |
| 4 | 8 strategies, scoring, mandatory filters, ranker with sector, correlation and exposure caps | `test_strategies_risk.py`, `test_ranking.py` (7) |
| 5 | Screenshot validation, OCR/OpenCV extraction, LLM schema merge, market-data verification | `test_vision.py` (21) |
| 6 | Trade plans (trigger, limit, stop, T1–T3, MISSED/INVALIDATED/EXPIRED/TOO_LATE), position sizing, Groww-schedule cost model, account risk limits | `test_strategies_risk.py` (18) |
| 7 | Shared fill simulator, backtester (OOS and walk-forward), metrics, DB-backed paper trading, news RSS, 13 agents and orchestrator | `test_backtest_paper.py` (17), `test_services.py` (13) |
| 8 | 14-page Streamlit UI, demo clock | `test_ui_smoke.py` (15 headless page renders + scan button) |
| 9 | Full suite | **145 passed** |
| 11 | More free sources: NSE website quotes, order book, circuit bands, delivery %, NIFTY 500 breadth, VIX, live sector moves, holidays, price-band hitters, index constituents, EQUITY_L auto-download, bulk/block deals; 6 more RSS news feeds; composite provider with Yahoo cross-check; data-source health page | `test_free_sources.py` (18) |
| 10 | README and guides in `docs/` | – |

## Acceptance criteria status
| # | Criterion | Status |
|---|---|---|
| 1 | Provider authentication | N/A: Yahoo needs no key. Connection test page provided. |
| 2 | Instrument mapping | ✅ tested (seed, NSE master, watchlist, `.NS` mapping) |
| 3 | Candle retrieval | ✅ adapter tested with mocked responses. ⚠️ Live Yahoo calls could not run in the build sandbox (network blocked); verify with **Settings → Test connection** |
| 4 | Configurable scanning | ✅ |
| 5–7 | Screenshot upload, symbol extraction, verification | ✅ tested on generated charts. Real-world accuracy depends on screenshot quality |
| 8 | Tested indicators | ✅ including VWAP session reset and look-ahead checks |
| 9 | Transparent scoring | ✅ |
| 10–11 | ≤ 3 setups, NO TRADE behaviour | ✅ |
| 12–13 | Sizing below ₹50k, costs and slippage | ✅ |
| 14–16 | Paper trading, backtesting, analytics | ✅ |
| 17 | Secret handling | ✅ env-only keys, header auth, log redaction, `.gitignore` |
| 18–19 | Documentation, automated tests | ✅ |
| 20 | No fake data | ✅ simulated mode is explicit and labelled. Failures raise errors or fall back to labelled cache |

## Known limitations
See README §6. Main points:

- data is delayed
- no bid/ask
- index VWAP uses an ETF proxy
- breadth is computed from scanned stocks only
- the NSE holiday list must be maintained each year
- backtests have survivorship bias
