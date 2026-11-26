"""D. Relative-volume breakout, E. Support/resistance breakout,
H. Breakdown & bearish momentum."""
from __future__ import annotations

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class RelativeVolumeBreakout(Strategy):
    key = "rvol_breakout"
    name = "D. Relative-volume breakout"
    setup_type = "breakout"
    directions = ("LONG",)
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.SIDEWAYS)
    entry_rules = ("A strong bullish candle closes at a new intraday high with bar relative volume ≥ 2 and "
                   "time-of-day relative volume ≥ 1.5, above VWAP. Entry on a break of its high.")
    confirmation_rules = "Strong-body candle (body ≥ 60% of range, close in top 20%)."
    stop_method = "Below the breakout candle's low minus 0.1×ATR."
    invalidation_rules = "Close back below the prior intraday high before entry."
    default_params = {"bar_rvol": 2.0, "tod_rvol": 1.5, "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        p = self.params
        if i < 25 or not self._ok_time(ind.index[i], ctx):
            return None
        r, prev = ind.iloc[i], ind.iloc[i - 1]
        if not valid(r["rvol_bar"], r["atr14"]) or prev.name.date() != ind.index[i].date():
            return None
        tod = r["rvol_tod"] if valid(r["rvol_tod"]) else None
        prior_high = prev["day_high"]
        if r["close"] > prior_high and r["strong_bull"] and r["rvol_bar"] >= p["bar_rvol"] and \
                (tod is None or tod >= p["tod_rvol"]) and ((not valid(r["vwap"])) or r["close"] > r["vwap"]):
            return self._make(ind, i, ctx, "LONG", r["high"], r["low"] - 0.1 * r["atr14"],
                              [f"New intraday high above {prior_high:.2f} on {r['rvol_bar']:.1f}× volume"],
                              ["Strong bullish candle"] + ([f"Time-of-day RVOL {tod:.2f}"] if tod else []),
                              [f"Close back below {prior_high:.2f}"], {"prior_day_high": float(prior_high)})
        return None


class SupportResistanceBreakout(Strategy):
    key = "sr_breakout"
    name = "E. Support/resistance breakout"
    setup_type = "breakout"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.SIDEWAYS, Regime.MILD_BEARISH,
                        Regime.STRONG_BEARISH)
    entry_rules = ("Resistance = highest confirmed swing high of the last 75 candles (or previous-day high if "
                   "higher and nearby). A candle closes ≥ 0.1×ATR beyond it after the prior candle closed below. "
                   "Mirror logic for support breakdowns.")
    confirmation_rules = "Bar relative volume ≥ 1.3; close beyond level (not just a wick)."
    stop_method = "Back inside the broken level by 0.5×ATR (failed-breakout logic)."
    invalidation_rules = "A candle closes back on the other side of the broken level before entry."
    default_params = {"lookback": 75, "min_rvol": 1.3, "valid_bars": 3}

    def prepare(self, ind):
        n = self.params["lookback"]
        ind = ind.copy()
        sh = ind["last_swing_high"].where(ind["last_swing_high"] != ind["last_swing_high"].shift())
        sl = ind["last_swing_low"].where(ind["last_swing_low"] != ind["last_swing_low"].shift())
        ind["res_level"] = sh.rolling(n, min_periods=1).max().shift(1)
        ind["sup_level"] = sl.rolling(n, min_periods=1).min().shift(1)
        return ind

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        if i < 30 or not self._ok_time(ind.index[i], ctx) or "res_level" not in ind:
            return None
        r, prev = ind.iloc[i], ind.iloc[i - 1]
        if not valid(r["atr14"]):
            return None
        atr = r["atr14"]
        rvol = r["rvol_bar"] if valid(r["rvol_bar"]) else 0.0
        res = r["res_level"]
        if valid(r.get("pdh")) and valid(res) and r["pdh"] > res and (r["pdh"] - res) < atr:
            res = r["pdh"]
        sup = r["sup_level"]
        if valid(r.get("pdl")) and valid(sup) and r["pdl"] < sup and (sup - r["pdl"]) < atr:
            sup = r["pdl"]
        if valid(res) and r["close"] >= res + 0.1 * atr and prev["close"] <= res and rvol >= self.params["min_rvol"]:
            return self._make(ind, i, ctx, "LONG", r["high"], res - 0.5 * atr,
                              [f"Closed above resistance {res:.2f}"], [f"Relative volume {rvol:.2f}"],
                              [f"Close back below {res:.2f}"], {"resistance": float(res)})
        if ctx.allow_short and valid(sup) and r["close"] <= sup - 0.1 * atr and prev["close"] >= sup \
                and rvol >= self.params["min_rvol"]:
            return self._make(ind, i, ctx, "SHORT", r["low"], sup + 0.5 * atr,
                              [f"Closed below support {sup:.2f}"], [f"Relative volume {rvol:.2f}"],
                              [f"Close back above {sup:.2f}"], {"support": float(sup)})
        return None


class BreakdownBearishMomentum(Strategy):
    key = "breakdown"
    name = "H. Breakdown & bearish momentum"
    setup_type = "breakdown"
    directions = ("SHORT",)
    suitable_regimes = (Regime.STRONG_BEARISH, Regime.MILD_BEARISH, Regime.SIDEWAYS)
    entry_rules = ("A strong bearish candle closes at a new intraday low (or below previous-day low) with bar "
                   "relative volume ≥ 1.5, below VWAP and with EMA9 < EMA21. Entry on a break of its low.")
    confirmation_rules = "RSI < 45 and MACD histogram negative."
    stop_method = "Above the breakdown candle's high plus 0.1×ATR."
    invalidation_rules = "Close back above the broken low / VWAP before entry."
    default_params = {"min_rvol": 1.5, "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        if not ctx.allow_short or i < 30 or not self._ok_time(ind.index[i], ctx):
            return None
        r, prev = ind.iloc[i], ind.iloc[i - 1]
        if not valid(r["atr14"], r["rsi14"], r["macd_hist"], r["ema9"], r["ema21"]):
            return None
        if prev.name.date() != ind.index[i].date():
            return None
        level = prev["day_low"]
        if valid(r.get("pdl")) and r["pdl"] < level and r["close"] < r["pdl"]:
            level = r["pdl"]
        rvol = r["rvol_bar"] if valid(r["rvol_bar"]) else 0.0
        if r["close"] < level and r["strong_bear"] and rvol >= self.params["min_rvol"] and r["ema9"] < r["ema21"] \
                and ((not valid(r["vwap"])) or r["close"] < r["vwap"]) and r["rsi14"] < 45 and r["macd_hist"] < 0:
            return self._make(ind, i, ctx, "SHORT", r["low"], r["high"] + 0.1 * r["atr14"],
                              [f"Broke below {level:.2f} with a strong bearish candle"],
                              [f"Relative volume {rvol:.2f}", f"RSI {r['rsi14']:.1f}", "MACD histogram negative"],
                              [f"Close back above {level:.2f}"], {"broken_low": float(level)})
        return None
