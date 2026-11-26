"""C. EMA 9/21 trend continuation."""
from __future__ import annotations

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class EMATrendContinuation(Strategy):
    key = "ema_trend"
    name = "C. EMA 9/21 trend continuation"
    setup_type = "pullback"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.MILD_BEARISH, Regime.STRONG_BEARISH)
    entry_rules = ("EMA9 > EMA21 > EMA50 (long) with Supertrend up and ADX > 20. Price dips into the EMA9–EMA21 "
                   "zone within the last 3 candles (previous candle closing at/below EMA9), then a candle closes above EMA9 AND above the previous candle's high. Entry on a break of its high.")
    confirmation_rules = "+DI > −DI (long), close above VWAP when available."
    stop_method = "Below the lowest low of the last 3 candles or EMA21 (whichever is lower) minus 0.1×ATR."
    invalidation_rules = "Close below EMA21 or Supertrend flip before entry."
    default_params = {"min_adx": 20, "touch_lookback": 3, "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        p = self.params
        if i < 60 or not self._ok_time(ind.index[i], ctx):
            return None
        r = ind.iloc[i]
        if not valid(r["ema9"], r["ema21"], r["ema50"], r["adx"], r["atr14"]):
            return None
        win = ind.iloc[i - p["touch_lookback"]: i + 1]
        atr = r["atr14"]
        if r["ema9"] > r["ema21"] > r["ema50"] and r["supertrend_dir"] > 0 and r["adx"] >= p["min_adx"] \
                and r["plus_di"] > r["minus_di"]:
            prev = ind.iloc[i - 1]
            touched = (win["low"] <= win["ema9"]).any()
            resumed = r["close"] > prev["high"] and prev["close"] <= prev["ema9"] * 1.001
            if touched and resumed and r["close"] > r["ema9"] and r["close"] > r["open"] and \
                    ((not valid(r["vwap"])) or r["close"] > r["vwap"]):
                stop = min(win["low"].min(), r["ema21"]) - 0.1 * atr
                return self._make(ind, i, ctx, "LONG", r["high"], stop,
                                  ["EMA9 > EMA21 > EMA50 stacked uptrend", "Pullback into EMA9/21 zone held"],
                                  [f"ADX {r['adx']:.1f}, +DI > −DI", "Supertrend up"],
                                  [f"Close below EMA21 ({r['ema21']:.2f})", "Supertrend flips down"],
                                  {"ema21": float(r["ema21"])})
        if ctx.allow_short and r["ema9"] < r["ema21"] < r["ema50"] and r["supertrend_dir"] < 0 \
                and r["adx"] >= p["min_adx"] and r["minus_di"] > r["plus_di"]:
            prev = ind.iloc[i - 1]
            touched = (win["high"] >= win["ema9"]).any()
            resumed = r["close"] < prev["low"] and prev["close"] >= prev["ema9"] * 0.999
            if touched and resumed and r["close"] < r["ema9"] and r["close"] < r["open"] and \
                    ((not valid(r["vwap"])) or r["close"] < r["vwap"]):
                stop = max(win["high"].max(), r["ema21"]) + 0.1 * atr
                return self._make(ind, i, ctx, "SHORT", r["low"], stop,
                                  ["EMA9 < EMA21 < EMA50 stacked downtrend", "Rally into EMA9/21 zone rejected"],
                                  [f"ADX {r['adx']:.1f}, −DI > +DI", "Supertrend down"],
                                  [f"Close above EMA21 ({r['ema21']:.2f})", "Supertrend flips up"],
                                  {"ema21": float(r["ema21"])})
        return None
