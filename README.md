# 📈 BharatTrade AI

**An evidence-driven research and paper-trading assistant for NSE intraday cash-equity trading, built for accounts below ₹50,000.**

BharatTrade AI does the following:

- scans liquid NSE stocks
- reads chart screenshots you upload
- checks everything against fresh market data
- ranks setups with a transparent 0–100 score
- sizes each position so a losing trade costs only a small, fixed amount

It recommends **at most three** setups and shows **NO VALID INTRADAY OPPORTUNITY — DO NOT TRADE** whenever the evidence is not good enough.

> ⚠️ **Research and paper trading only.** It is not investment advice and it never places real orders. The score measures how well a setup meets the rules. It is **not** a probability of profit. Backtests do not guarantee future results.

---

## 1. What it uses (all free)

| Need | Free component | Notes |
|---|---|---|
| Candles (history + intraday) | **Yahoo Finance** via `yfinance` | No key needed. Data is **DELAYED** and labelled that way everywhere. Intraday history is limited: about 7 days for 1m and about 60 days for 5m/15m/30m. |
| Quotes, order book, circuit limits, delivery % | **NSE public website** (nseindia.com) | Free but unofficial. A quote stamped within 3 minutes during market hours is labelled LIVE, otherwise DELAYED. Cross-checked against Yahoo. If it is blocked, the app falls back to Yahoo and says so. |
| Market breadth, India VIX, live sector moves, market status, holidays, upper/lower circuit lists | **NSE public website** | Real NIFTY 500 advance/decline, and the NSE holiday calendar loaded automatically. |
| Universe lists | **niftyindices.com / NSE archives** | NIFTY 50/100/200/500/Next 50/Midcap 100 constituents and the full `EQUITY_L.csv`, downloaded automatically. |
| Bulk / block deals | **NSE archives CSV** | Shown as warnings on matching candidates. |
| Chart OCR | **Tesseract** + OpenCV | Runs locally. |
| Vision / explanation AI *(optional)* | **Google Gemini** free tier, **Groq** free tier, or **OpenRouter** free models | Optional. Without a key the app uses OCR + OpenCV and template explanations. |
| News *(optional)* | Free RSS: NSE announcements, corporate actions and results; Economic Times; Moneycontrol; Mint; Business Standard; The Hindu; Google News | Only adds warnings. It never raises a score. |
| Database | SQLite | Can move to PostgreSQL by setting `DATABASE_URL`. |

Nothing in the app ever invents a price. If data is missing or stale, the candidate is rejected and the reason is shown.

---

## 2. Install on Windows (step by step)

1. **Install Python 3.11, 3.12 or 3.13** from <https://www.python.org/downloads/windows/>. Tick **"Add python.exe to PATH"** during setup.
2. **Install Tesseract OCR** so the app can read screenshots:
   - Download the Windows installer from the UB Mannheim build: <https://github.com/UB-Mannheim/tesseract/wiki>
   - Install it to the default folder `C:\Program Files\Tesseract-OCR\`.
   - If it is not on your PATH, put this in `.env`: `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`
3. **Unzip** the project, for example to `C:\BharatTrade\bharattrade_ai`.
4. **Double-click `run_windows.bat`.** The first run takes a few minutes to create a virtual environment and install packages. Your browser then opens at <http://localhost:8501>.

To set it up manually (PowerShell) instead:

```powershell
cd C:\BharatTrade\bharattrade_ai
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py
```

On macOS or Linux, use `python3 -m venv .venv && source .venv/bin/activate`. Install Tesseract with `brew install tesseract` or `sudo apt install tesseract-ocr`.

### Optional: free AI keys

Edit `.env` (never commit it):

```ini
BT_LLM_PROVIDER=gemini          # or groq / openrouter / none
GEMINI_API_KEY=your-key-here    # https://aistudio.google.com/apikey
# GROQ_API_KEY=...              # https://console.groq.com/keys
```

Leave the model fields blank to use the defaults:

- Gemini: `gemini-2.5-flash`
- Groq: `llama-3.3-70b-versatile` for text and `meta-llama/llama-4-scout-17b-16e-instruct` for vision

Free-tier model names and limits change, so you can override them in `.env` or on the Settings page. **Settings → AI providers → Test AI provider** checks your key.

---

## 3. First run (5 minutes)

1. Open **Settings → Data source**. Keep **Yahoo + NSE website** selected and click **Test all free data sources**. A table shows which sources respond on your network right now. Sources marked ❌ are not used; the app never substitutes values for them.
   - Outside market hours, try **Simulated** mode and set a **Demo clock** (for example `11:30`). This lets you explore the whole workflow on synthetic data, which is clearly labelled.
2. Open **Risk Management** and set your capital (₹1–₹49,999), risk per trade and limits. Then **Save**.
3. Open **Market Overview** to see the regime (strong bullish … high volatility), the indices, VIX and sector strength.
4. In **Automatic Stock Scanner**, choose a universe and click **Run scan**.
5. Optionally, upload chart screenshots in the **Screenshot Upload Lab**, click **Verify all**, then open **Combined AI Analysis**.
6. Read **Top 3 Recommendations**. Each card shows entry trigger, entry range, stop, targets, quantity, costs and planned loss. It also answers *WHY THIS STOCK / ENTRY / STOP / TARGETS / WHAT CAN INVALIDATE IT*.
7. Click **Paper-trade** on a card. Then use **Paper Trading → Update prices** to simulate fills.

---

## 4. How a recommendation is produced

```
Market regime ─┐
Risk gate ─────┤
Universe ──────┼─► per stock: liquidity filter → candles → validation & freshness → indicators
Screenshots ───┤                → 8 strategies (completed candles only) → trade plan → score
Manual symbols ┘                → mandatory rejection rules → ranking + diversification → ≤ 3 ideas
```

- **Score (0–100)** with configurable weights: trend 20, momentum 15, VWAP 15, relative volume 15, breakout/price action 15, market and sector 10, liquidity 10. Every point is listed with its evidence.
- **A candidate is rejected if any of these is true:**
  - data is stale or missing
  - the symbol is unverified
  - liquidity is inadequate
  - the stop is invalid or too wide (more than 2.5×ATR)
  - R:R to T1 is below 1.5
  - net R:R after estimated costs is below 0.8
  - the live spread is excessive (only when a real bid/ask is available)
  - the breakout is unconfirmed
  - the regime conflicts with the trade, or is high-volatility or unknown
  - a circuit or halt is suspected
  - the setup is MISSED, INVALIDATED, EXPIRED or too late (after 14:45)
  - the score is below the minimum
  - a daily-loss, trade-count or cooldown limit has been hit
- **MISSED, never chased:** if price is already beyond the entry limit, the setup is marked MISSED.

### Capital management (below ₹50,000)

```
Risk Budget         = Capital × Risk% / 100            (default ₹30,000 × 0.5% = ₹150)
Risk per Share      = |Entry − Stop|                   (sized on the worst acceptable fill = entry limit)
Risk-Based Qty      = floor(Risk Budget / Risk per Share)
Capital-Based Qty   = floor(Available Allocation / Entry)   (default 25% = ₹7,500)
Final Qty           = min(both)
```

Other protections:

- daily loss limit (default 1.5%)
- maximum 3 trades a day
- cooldown after 2 consecutive losses
- open-exposure cap (75%)
- one position per sector
- return-correlation check
- **no leverage, ever**

Estimated costs follow Groww's published intraday schedule (Nov 2026, editable in Settings):

- brokerage: ₹20 or 0.1% per order, whichever is lower (₹5 minimum rule)
- STT: 0.025% on the sell side
- stamp duty: 0.003% on the buy side
- NSE transaction charge: 0.00307%
- SEBI fee: 0.0001%
- GST: 18%
- slippage and half-spread assumptions

**Your contract note is the only source of actual costs.**

> 💡 On a ₹30,000 account, costs are a large share of each trade's reward. The app shows the *net* R:R after costs on every card so you can see this.

---

## 5. Pages

| Page | What it does |
|---|---|
| Dashboard | Indices, regime, breadth, sector strength, scan counts, shortlist, capital, daily risk, paper P&L |
| Market Overview | NIFTY 50 / BANK NIFTY gap, PDH/PDL, VWAP (ETF proxy), EMA, ADX, VIX, sector relative strength |
| Automatic Stock Scanner | Seed list, NSE master (`EQUITY_L.csv`) or watchlist; request budget; partial-scan warning; filter and export |
| Screenshot Upload Lab | Multi-upload PNG/JPG/WEBP; per-field value, confidence, source and flags; correct the symbol; verify against market data; compare images |
| Combined AI Analysis | Merges scanner, verified screenshot and manual symbols; shows workflow state transitions |
| Top 3 Recommendations | Full recommendation cards, or the NO-TRADE banner with the closest rejected setups and why |
| Technical Chart Analysis | Candles with EMA, VWAP, Supertrend, Bollinger, S/R, RSI and MACD; freshness check; live signals |
| Strategy Explorer | Rules for all 8 strategies plus their historical signals on a chart |
| Backtesting | Multi-symbol and multi-strategy; out-of-sample split; anchored walk-forward; metrics; CSV export |
| Risk Management | Profile, position-size and cost calculator with the formula shown, today's account guard |
| Paper Trading | Simulated orders and positions, SL/target monitoring, manual exit, event log, CSV export |
| Trade Journal | Notes linked to paper trades |
| Performance Analytics | Equity curve, daily and strategy-wise P&L, scan and recommendation history |
| Settings & API Configuration | Provider and connection test, demo clock, AI keys (status only), weights, filters, costs, retention, audit log |

---

## 6. Honest limitations

- **Free data is delayed.** Exchange-licensed real-time data is not free. Every price shows its label (`DELAYED`, `HISTORICAL` or `SIMULATED`). Confirm prices in your broker app before acting.
- **NSE website endpoints are unofficial.** NSE can change or block them at any time, and they are subject to NSE's website terms. When they fail, the app falls back to Yahoo for quotes (no bid/ask, so no spread check) and to scanned-stock breadth, and shows why.
- **Index candles have no volume.** Index VWAP is approximated with NIFTYBEES/BANKBEES and labelled as a proxy.
- **Holidays** load automatically from NSE and are cached in `storage/`. If NSE is unreachable and nothing is cached, stale-data checks still prevent recommendations on a closed day.
- **The bundled seed list** is a hand-picked set of liquid large caps, **not** an official index list. For full coverage, upload NSE's `EQUITY_L.csv` (nseindia.com → Market Data → Securities available for trading).
- **Backtests** use today's symbols (possible survivorship bias), independent per-symbol simulation and conservative fills. See `docs/BACKTESTING.md`.

---

## 7. Tests

```bash
pytest            # 145 tests: data adapter (mocked), mapping, validation, indicators, VWAP reset,
                  # strategies (no look-ahead), sizing, costs, ranking, screenshots, verification,
                  # backtest fills, paper-trading lifecycle, risk limits, LLM (mocked), news, UI pages,
                  # NSE website client + composite provider (mocked HTTP)
```

No test uses real credentials or network access.

---

## 8. Project structure

```
bharattrade_ai/
  app.py                    Streamlit entry (14 pages)
  agents/                   13 agents + orchestrator (state machine)
  brokers/                  base.py (provider interface), yahoo_client.py, nse_web.py, composite.py,
                            simulated.py, factory.py
  data/                     instrument_master, validation, cache (SQLite), market_hours, market_extras
  market/                   regime, sector_strength, breadth
  indicators/               trend, momentum, volume, volatility, price_action, compute
  strategies/               A–H strategies + engine
  vision/                   image_preprocessor, ocr, chart_analyser, verification, schemas
  ranking/                  scoring, filters, ranker
  risk/                     position_sizing, transaction_costs, trade_plan, risk_limits
  backtesting/              simulator (shared with paper trading), engine, metrics
  paper_trading/            engine
  news/                     RSS news & events
  llm/                      free LLM providers + guarded explainer
  database/                 models, db (migrations, retention, audit), schema.sql
  ui/                       pages, components, state, runner
  config/                   settings, markets, universe_seed.csv, news_sources.json
  tests/                    pytest suite
  docs/                     architecture, strategies, backtesting, paper trading, data sources,
                            screenshot AI setup, troubleshooting
```

### Adding a real-time broker later

Implement `brokers/base.py::MarketDataProvider` (`get_candles`, `get_quote`) for any official broker API. Then register it in `brokers/factory.py`. Strategies, ranking and the UI do not change. Live order placement is intentionally not implemented.

**Cloud hosting:** see `docs/DEPLOYMENT.md` for Streamlit Community Cloud (free) and Docker.

See also: `docs/ARCHITECTURE.md`, `docs/STRATEGIES.md`, `docs/BACKTESTING.md`, `docs/PAPER_TRADING.md`, `docs/DATA_SOURCES.md`, `docs/SCREENSHOT_AI_SETUP.md`, `docs/TROUBLESHOOTING.md`.
