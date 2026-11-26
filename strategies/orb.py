"""B. Opening Range Breakout (first breakout of the session only)."""
from __future__ import annotations

from datetime import time

from market.regime import Regime
from strategies.base import Signal, Strategy, StrategyContext, valid


class OpeningRangeBreakout(Strategy):
    key = "orb"
    name = "B. Opening Range Breakout"
    setup_type = "breakout"
    suitable_regimes = (Regime.STRONG_BULLISH, Regime.MILD_BULLISH, Regime.MILD_BEARISH, Regime.STRONG_BEARISH,
                        Regime.SIDEWAYS)
    entry_rules = ("After the 15-minute opening range completes, the FIRST candle that closes above the OR high "
                   "(long) or below the OR low (short). Entry on a break of that candle's extreme. Valid until 11:30.")
    confirmation_rules = "Breakout candle relative volume ≥ 1.2 and close on the correct side of VWAP."
    stop_method = ("Opposite side of the opening range; if the range is wider than 1.5×ATR, the OR midpoint "
                   "is used instead.")
    invalidation_rules = "Price closes back inside the opening range before entry."
    default_params = {"min_rvol": 1.2, "last_entry": "11:30", "valid_bars": 2}

    def evaluate(self, ind, i, ctx: StrategyContext) -> Signal | None:
        if i < 3 or not self._ok_time(ind.index[i], ctx):
            return None
        h, m = map(int, self.params["last_entry"].split(":"))
        if ind.index[i].time() > time(h, m):
            return None
        r = ind.iloc[i]
        if not valid(r["or_high"], r["or_low"], r["atr14"]):
            return None
        day = ind.index[i].date()
        prior = ind.iloc[:i]
        prior = prior[(prior.index.date == day) & prior["or_high"].notna()]
        rvol = r["rvol_bar"] if valid(r["rvol_bar"]) else 0.0
        vwap_ok_long = (not valid(r["vwap"])) or r["close"] > r["vwap"]
        vwap_ok_short = (not valid(r["vwap"])) or r["close"] < r["vwap"]
        rng = r["or_high"] - r["or_low"]
        mid = (r["or_high"] + r["or_low"]) / 2
        wide = rng > 1.5 * r["atr14"]
        levels = {"or_high": float(r["or_high"]), "or_low": float(r["or_low"])}
        if r["close"] > r["or_high"] and not (prior["close"] > prior["or_high"]).any() \
                and rvol >= self.params["min_rvol"] and vwap_ok_long:
            stop = mid if wide else r["or_low"]
            return self._make(ind, i, ctx, "LONG", r["high"], stop,
                              [f"First close above opening-range high {r['or_high']:.2f}"],
                              [f"Relative volume {rvol:.2f}", "Close above VWAP" if valid(r["vwap"]) else "VWAP n/a"],
                              [f"Close back below OR high {r['or_high']:.2f}"], levels)
        if ctx.allow_short and r["close"] < r["or_low"] and not (prior["close"] < prior["or_low"]).any() \
                and rvol >= self.params["min_rvol"] and vwap_ok_short:
            stop = mid if wide else r["or_high"]
            return self._make(ind, i, ctx, "SHORT", r["low"], stop,
                              [f"First close below opening-range low {r['or_low']:.2f}"],
                              [f"Relative volume {rvol:.2f}", "Close below VWAP" if valid(r["vwap"]) else "VWAP n/a"],
                              [f"Close back above OR low {r['or_low']:.2f}"], levels)
        return None
