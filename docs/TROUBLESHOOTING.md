# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **Connection test fails** or there is "No data returned" | Yahoo may be rate-limiting or temporarily unavailable. Wait a minute, raise *Delay between requests* in Settings, or try again later. The app never substitutes fake data. |
| Every candidate says **Stale data** | The market is closed or Yahoo's delay is longer than *Stale after (min)*. Outside market hours this is expected. To explore, use Simulated mode with a demo clock. |
| Always **NO VALID INTRADAY OPPORTUNITY** | This is often correct. Open *Top 3 → Closest setups* to see why. A common reason on small accounts is the net R:R after costs. You can adjust *Min net R:R* in Risk Management, but understand the trade-off. |
| NSE sources ❌ in the health check | NSE blocks some networks, VPNs and cloud IPs, and rate-limits rapid requests. The app keeps working on Yahoo only, without bid/ask spread checks or NIFTY 500 breadth. Try again later from a home connection, or choose *Yahoo only* in Settings. |
| News feed ❌ | The publisher changed or removed the RSS URL. Disable it or replace the URL in `config/news_sources.json`. |
| `Ticker` errors for some symbols | The symbol was renamed or delisted. Upload a fresh `EQUITY_L.csv` or remove it from your watchlist. |
| Sector index shows "unavailable" | Yahoo does not publish every NSE sector index. Sector alignment is then scored as neutral. |
| **OCR: Not installed** | Install Tesseract and set `TESSERACT_CMD` (see `SCREENSHOT_AI_SETUP.md`). |
| Screenshot symbol not detected | Crop so the chart title is visible, use the highest resolution and light or dark theme without overlays, or choose the symbol in the dropdown. |
| Vision AI errors (HTTP 429/400/404) | The free-tier quota was exceeded or the model was renamed. Wait, or set `BT_LLM_VISION_MODEL` to a current model. |
| `StreamlitAPIException` after an upgrade | Use the pinned versions in `requirements.txt`. |
| Windows: `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `run_windows.bat`. |
| Database locked | Close other app instances. SQLite allows one writer at a time. For several users, move to PostgreSQL (`DATABASE_URL`). |
| Reset everything | Stop the app and delete the `storage/` folder. It holds the databases, cached candles and stored screenshots. |
| Logs | `logs/bharattrade.log` (JSON lines, with secrets redacted). |
