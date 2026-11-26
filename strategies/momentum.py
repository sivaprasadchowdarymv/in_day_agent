"""F. Momentum continuation."""
from __future__ import annotations

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class MomentumContinuation(Strategy):
    key = "momentum"
    name = "F. Momentum continuation"
    setup_type = "breakout"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.MILD_BEARISH, Regime.STRONG_BEARISH)
    entry_rules = ("Established momentum (ROC10 ≥ 0.6%, RSI 58–78, MACD histogram positive and rising for 2 "
                   "candles, ADX ≥ 25 with +DI > −DI, above VWAP). A small consolidation candle (inside bar or "
                   "range < 0.8×ATR) forms; entry on a break of its high.")
    confirmation_rules = "MACD histogram rising; relative volume ≥ 1."
    stop_method = "Below the lowest low of the last 2 candles minus 0.1×ATR."
    invalidation_rules = "MACD histogram turns negative or close below VWAP before entry."
    default_params = {"min_roc": 0.6, "min_adx": 25, "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        p = self.params
        if i < 40 or not self._ok_time(ind.index[i], ctx):
            return None
        r, r1, r2 = ind.iloc[i], ind.iloc[i - 1], ind.iloc[i - 2]
        if not valid(r["roc10"], r["rsi14"], r["macd_hist"], r1["macd_hist"], r2["macd_hist"], r["adx"], r["atr14"]):
            return None
        atr = r["atr14"]
        consolidating = bool(r["inside_bar"]) or (r["high"] - r["low"]) < 0.8 * atr
        vw = valid(r["vwap"])
        if r["roc10"] >= p["min_roc"] and 58 <= r["rsi14"] <= 78 and r["macd_hist"] > 0 and \
                r["macd_hist"] >= r1["macd_hist"] >= r2["macd_hist"] and r["adx"] >= p["min_adx"] and \
                r["plus_di"] > r["minus_di"] and consolidating and ((not vw) or r["close"] > r["vwap"]):
            stop = min(r["low"], r1["low"]) - 0.1 * atr
            return self._make(ind, i, ctx, "LONG", r["high"], stop,
                              [f"ROC10 {r['roc10']:.2f}%, RSI {r['rsi14']:.1f}", "Tight consolidation in uptrend"],
                              ["MACD histogram rising", f"ADX {r['adx']:.1f}"],
                              ["MACD histogram turns negative", "Close below VWAP"], {})
        if ctx.allow_short and r["roc10"] <= -p["min_roc"] and 22 <= r["rsi14"] <= 42 and r["macd_hist"] < 0 and \
                r["macd_hist"] <= r1["macd_hist"] <= r2["macd_hist"] and r["adx"] >= p["min_adx"] and \
                r["minus_di"] > r["plus_di"] and consolidating and ((not vw) or r["close"] < r["vwap"]):
            stop = max(r["high"], r1["high"]) + 0.1 * atr
            return self._make(ind, i, ctx, "SHORT", r["low"], stop,
                              [f"ROC10 {r['roc10']:.2f}%, RSI {r['rsi14']:.1f}", "Tight consolidation in downtrend"],
                              ["MACD histogram falling", f"ADX {r['adx']:.1f}"],
                              ["MACD histogram turns positive", "Close above VWAP"], {})
        return None
