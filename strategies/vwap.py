"""A. VWAP breakout and retest."""
from __future__ import annotations

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class VWAPBreakoutRetest(Strategy):
    key = "vwap_retest"
    name = "A. VWAP breakout & retest"
    setup_type = "breakout"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.SIDEWAYS,
                        Regime.MILD_BEARISH, Regime.STRONG_BEARISH)
    entry_rules = ("Price crosses VWAP (close on the other side) within the last N candles, then a later candle "
                   "retests VWAP (wick within 0.2×ATR) and closes back in the breakout direction. Entry on a break "
                   "of the retest candle's high (long) / low (short).")
    confirmation_rules = "Retest candle closes in trade direction; EMA9 on the same side of VWAP; relative volume ≥ 1."
    stop_method = "Beyond the retest candle's extreme or VWAP (whichever is further) plus 0.1×ATR buffer."
    invalidation_rules = "A completed candle closing back across VWAP before entry triggers."
    default_params = {"cross_lookback": 6, "retest_atr": 0.2, "valid_bars": 3}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        p = self.params
        if i < p["cross_lookback"] + 2 or not self._ok_time(ind.index[i], ctx):
            return None
        r = ind.iloc[i]
        if not valid(r["vwap"], r["atr14"], r["ema9"]):
            return None
        day = ind.index[i].date()
        win = ind.iloc[i - p["cross_lookback"]: i]
        win = win[win.index.date == day]
        if len(win) < 2:
            return None
        atr = r["atr14"]
        rvol = r["rvol_bar"] if valid(r["rvol_bar"]) else 1.0
        above = (win["close"] > win["vwap"]).to_numpy()
        crossed_up = any((not above[k - 1]) and above[k] for k in range(1, len(above)))
        crossed_dn = any(above[k - 1] and (not above[k]) for k in range(1, len(above)))
        # LONG
        if crossed_up and r["low"] <= r["vwap"] + p["retest_atr"] * atr and r["close"] > r["vwap"] \
                and r["close"] > r["open"] and r["ema9"] >= r["vwap"] and rvol >= 1.0:
            stop = min(r["low"], r["vwap"]) - 0.1 * atr
            return self._make(ind, i, ctx, "LONG", r["high"], stop,
                              [f"Crossed above VWAP ({r['vwap']:.2f}) in last {p['cross_lookback']} candles",
                               "Retested VWAP and closed back above it"],
                              ["Bullish retest candle", "EMA9 above VWAP", f"Relative volume {rvol:.2f}"],
                              [f"Candle close below VWAP {r['vwap']:.2f} before entry"],
                              {"vwap": float(r["vwap"])})
        if ctx.allow_short and crossed_dn and r["high"] >= r["vwap"] - p["retest_atr"] * atr \
                and r["close"] < r["vwap"] and r["close"] < r["open"] and r["ema9"] <= r["vwap"] and rvol >= 1.0:
            stop = max(r["high"], r["vwap"]) + 0.1 * atr
            return self._make(ind, i, ctx, "SHORT", r["low"], stop,
                              [f"Crossed below VWAP ({r['vwap']:.2f}) in last {p['cross_lookback']} candles",
                               "Retested VWAP from below and was rejected"],
                              ["Bearish retest candle", "EMA9 below VWAP", f"Relative volume {rvol:.2f}"],
                              [f"Candle close above VWAP {r['vwap']:.2f} before entry"],
                              {"vwap": float(r["vwap"])})
        return None
