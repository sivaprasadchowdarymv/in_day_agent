"""G. Pullback entry (buy the dip in an uptrend / sell the rally in a downtrend)."""
from __future__ import annotations

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class PullbackEntry(Strategy):
    key = "pullback"
    name = "G. Pullback entry"
    setup_type = "pullback"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.SIDEWAYS, Regime.MILD_BEARISH,
                        Regime.STRONG_BEARISH)
    entry_rules = ("Uptrend (EMA21 > EMA50, price above VWAP earlier in the session). RSI dips below 45 within the "
                   "last 5 candles after being above 55, price reaches EMA21 or VWAP (within 0.3×ATR) and a "
                   "reversal candle (hammer / bullish engulfing / strong bullish) forms. Entry above its high.")
    confirmation_rules = "Reversal candle pattern at support; current close back above EMA21."
    stop_method = "Below the pullback swing low (lowest low of last 5 candles) minus 0.1×ATR."
    invalidation_rules = "Close below the pullback low or EMA50 before entry."
    default_params = {"lookback": 5, "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        n = self.params["lookback"]
        if i < 60 or not self._ok_time(ind.index[i], ctx):
            return None
        r = ind.iloc[i]
        if not valid(r["ema21"], r["ema50"], r["rsi14"], r["atr14"]):
            return None
        win = ind.iloc[i - n: i + 1]
        earlier = ind.iloc[i - 3 * n: i - n]
        atr = r["atr14"]
        supports = [r["ema21"]] + ([r["vwap"]] if valid(r["vwap"]) else [])
        if r["ema21"] > r["ema50"] and (earlier["rsi14"] > 55).any() and (win["rsi14"] < 45).any():
            near = any(abs(win["low"].min() - s) <= 0.3 * atr for s in supports)
            reversal = bool(r["hammer"] or r["bull_engulf"] or r["strong_bull"])
            if near and reversal and r["close"] > r["ema21"]:
                stop = win["low"].min() - 0.1 * atr
                pat = "hammer" if r["hammer"] else ("bullish engulfing" if r["bull_engulf"] else "strong bullish candle")
                return self._make(ind, i, ctx, "LONG", r["high"], stop,
                                  ["Uptrend: EMA21 > EMA50", "RSI reset from >55 to <45 (healthy pullback)"],
                                  [f"Reversal: {pat} near EMA21/VWAP"],
                                  [f"Close below pullback low {win['low'].min():.2f}", "Close below EMA50"],
                                  {"pullback_low": float(win["low"].min())})
        if ctx.allow_short and r["ema21"] < r["ema50"] and (earlier["rsi14"] < 45).any() and (win["rsi14"] > 55).any():
            near = any(abs(win["high"].max() - s) <= 0.3 * atr for s in supports)
            reversal = bool(r["shooting_star"] or r["bear_engulf"] or r["strong_bear"])
            if near and reversal and r["close"] < r["ema21"]:
                stop = win["high"].max() + 0.1 * atr
                return self._make(ind, i, ctx, "SHORT", r["low"], stop,
                                  ["Downtrend: EMA21 < EMA50", "RSI bounce from <45 to >55 (relief rally)"],
                                  ["Bearish reversal candle near EMA21/VWAP"],
                                  [f"Close above rally high {win['high'].max():.2f}", "Close above EMA50"],
                                  {"rally_high": float(win["high"].max())})
        return None
