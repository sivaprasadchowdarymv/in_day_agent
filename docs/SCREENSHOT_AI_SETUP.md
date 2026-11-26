# Screenshot intelligence setup

## 1. Tesseract OCR (required for text extraction)
- **Windows:** install from <https://github.com/UB-Mannheim/tesseract/wiki>. If `tesseract` is not on your PATH, set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.
- **macOS:** `brew install tesseract`
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr`

Check it: the Screenshot Upload Lab shows **OCR (Tesseract): Ready**.

## 2. Optional free vision AI
The vision AI is optional. It fills fields that OCR could not read and flags disagreements with OCR. Its output must pass a strict schema (`vision/schemas.py::LLMChartReading`, which includes an RSI range of 0–100, positive prices and allowed trend words), otherwise it is ignored.

| Provider | Key | Default vision model |
|---|---|---|
| Google Gemini (free tier) | `GEMINI_API_KEY` from <https://aistudio.google.com/apikey> | `gemini-2.5-flash` |
| Groq (free tier) | `GROQ_API_KEY` from <https://console.groq.com/keys> | `meta-llama/llama-4-scout-17b-16e-instruct` |
| OpenRouter (free models) | `OPENROUTER_API_KEY` | set `BT_LLM_VISION_MODEL` to a vision-capable `:free` model |

Set `BT_LLM_PROVIDER` in `.env`, or use **Settings → AI providers**. You can also paste a key there for the current session only; it is never saved. Free-tier models and quotas change, so set `BT_LLM_MODEL` and `BT_LLM_VISION_MODEL` if a default is retired.

## 3. What is extracted (per screenshot)
Each field records its value, a confidence score (0–1), its source (`ocr`, `opencv`, `llm` or `user`), a flag and a note:

- symbol (matched against the NSE universe – never guessed)
- exchange
- timeframe (mapped to the nearest supported interval)
- visible price (from the OHLC legend, or estimated from the last candle's position on the price axis)
- timestamp
- EMA periods, VWAP, RSI value, MACD
- coloured horizontal lines turned into support/resistance prices through the fitted price axis (flagged, because they may be indicators)
- trend (from a regression on candle-pixel positions)
- readability score (resolution, sharpness and OCR confidence)

## 4. Verification (always performed before a screenshot symbol can be traded)
1. Check that the symbol exists in the data provider. If it does not, the screenshot is **UNVERIFIED** and rejected.
2. Fetch fresh candles at the screenshot's timeframe and recompute the indicators.
3. Compare the screenshot price with the current price (more than 3% apart is flagged as possibly old or misread). Also compare trend, RSI and date.
4. Status is VERIFIED, VERIFIED_WITH_DISCREPANCIES or UNVERIFIED.
5. A verified symbol then goes through the **normal** pipeline. Strategies, plans and scores use only fresh market data. The screenshot price is never used as the live price.

You can correct the symbol, exclude images, remove them, compare them side by side and re-verify.
