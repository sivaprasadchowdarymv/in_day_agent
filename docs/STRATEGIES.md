# Strategies

## Rules that apply to every strategy

- **Completed candles only.** A strategy only looks at candles that have finished. Indicators are computed only from current and past data. Swing points are confirmed `k` bars late and are placed on the bar where they become known. The opening range is unknown until it has finished. The tests check that adding future candles never changes a past signal.
- **One code path.** The same `evaluate(ind, i)` runs in live scanning and in backtesting.
- **Entry time window.** Entries are allowed from 09:20 to 14:45 IST. Positions are squared off at 15:15.
- **Entry orders.** Entry is a stop-limit order. The trigger is the signal candle's high (or low for shorts) ± 1 tick. The limit is the trigger ± max(0.15×ATR, 0.1%). Price beyond the limit means the setup is **MISSED**, and the app does not chase it.
- **Stop width.** Stops tighter than 0.3×ATR are widened to 0.3×ATR. Stops wider than 2.5×ATR are rejected.
- **Targets.**
  - T1 = 1.5R and T2 = 2.5R.
  - T3 = 3.5R, only when ADX > 25.
  - Each target is capped just before the next major opposing level: previous-day high or low, today's high or low, or an S/R cluster with at least 2 touches. If that cap pulls T1 below 1.5R, the setup is rejected.
- **Validity.** A signal stays valid for a few candles (`valid_bars`). After that it **EXPIRES**.

| Key | Strategy | Directions | Suitable regimes |
|---|---|---|---|
| `vwap_retest` | A. VWAP breakout & retest | Long/Short | All except High volatility |
| `orb` | B. Opening Range Breakout | Long/Short | All except High volatility |
| `ema_trend` | C. EMA 9/21 trend continuation | Long/Short | Trending (not Sideways) |
| `rvol_breakout` | D. Relative-volume breakout | Long | Bullish / Sideways |
| `sr_breakout` | E. Support/resistance breakout | Long/Short | All except High volatility |
| `momentum` | F. Momentum continuation | Long/Short | Trending (not Sideways) |
| `pullback` | G. Pullback entry | Long/Short | All except High volatility |
| `breakdown` | H. Breakdown & bearish momentum | Short | Bearish / Sideways |

## A. VWAP breakout and retest
- **Entry:** within the last 6 candles, price closed across VWAP. Then a candle retests VWAP (its wick comes within 0.2×ATR) and closes back in the breakout direction. Enter on a break of that retest candle.
- **Confirmation:** the candle closes in the trade direction, EMA9 is on the same side of VWAP, and bar relative volume is at least 1.
- **Stop:** beyond the retest candle's extreme or VWAP, whichever is further, plus 0.1×ATR.
- **Invalidation:** a candle closes back across VWAP before the entry triggers.

## B. Opening Range Breakout (15 minutes)
- **Entry:** the first close beyond the OR high or low after 09:30, up to 11:30. Only the first breakout of the day counts.
- **Confirmation:** bar relative volume is at least 1.2 and the close is on the correct side of VWAP.
- **Stop:** the opposite side of the opening range. If the range is wider than 1.5×ATR, the OR midpoint is used instead.
- **Invalidation:** price closes back inside the range.

## C. EMA 9/21 trend continuation
- **Entry:** EMA9 > EMA21 > EMA50, Supertrend is up, and ADX is at least 20 with +DI > −DI. Price pulls back into the EMA9 zone. Then a candle closes above EMA9 *and* above the previous candle's high.
- **Stop:** below the lowest low of the last 3 candles or EMA21, whichever is lower, minus 0.1×ATR.
- **Invalidation:** a close below EMA21, or Supertrend flips.

## D. Relative-volume breakout
- **Entry:** a strong bullish candle (body at least 60% of the range, close in the top 20%) closes at a new intraday high. Bar relative volume is at least 2, time-of-day relative volume is at least 1.5, and the close is above VWAP.
- **Stop:** below the breakout candle's low, minus 0.1×ATR.

## E. Support/resistance breakout
- **Levels:** resistance is the highest confirmed swing high of the last 75 candles. If the previous-day high is within 1 ATR above it, the previous-day high is used instead. Support mirrors this.
- **Entry:** a candle closes at least 0.1×ATR beyond the level, after the prior candle closed on the other side. Bar relative volume must be at least 1.3.
- **Stop:** 0.5×ATR back inside the level (the failed-breakout logic).

## F. Momentum continuation
- **Entry:** ROC10 is at least 0.6%, RSI is between 58 and 78, the MACD histogram is positive and has risen for 2 candles, ADX is at least 25 with DI aligned, and price is above VWAP. A small consolidation candle forms (an inside bar, or a range under 0.8×ATR). Enter on a break of its high.
- **Stop:** below the lowest low of the last 2 candles, minus 0.1×ATR.

## G. Pullback entry
- **Entry:** EMA21 > EMA50. RSI was above 55 earlier, then dipped below 45 within the last 5 candles. Price reaches EMA21 or VWAP (within 0.3×ATR) and prints a reversal candle (hammer, bullish engulfing or a strong bullish candle). Enter above that candle.
- **Stop:** below the pullback low, minus 0.1×ATR.

## H. Breakdown & bearish momentum (short only)
- **Entry:** a strong bearish candle closes below the intraday low (or below the previous-day low). Bar relative volume is at least 1.5, price is below VWAP, EMA9 < EMA21, RSI < 45 and the MACD histogram is below 0.
- **Stop:** above the breakdown candle's high, plus 0.1×ATR.

## Changing parameters
Each strategy has `default_params`. You can override them with `get_strategy(key, **params)`. The backtester's walk-forward can choose parameters on in-sample data only.
