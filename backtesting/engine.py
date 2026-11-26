"""Bar-replay backtester using the SAME strategy and trade-plan code as live
scanning. Supports multiple strategies/symbols/timeframes, out-of-sample
splits and anchored walk-forward evaluation.

Survivorship note: the universe is today's symbol list; delisted stocks are
absent, so results can be optimistic. Regime filters need index history and
are not applied unless `regime_by_day` is supplied.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd

from config.settings import CostConfig, TradingProfile
from data.market_hours import INTERVAL_MINUTES, parse_hhmm
from indicators.compute import compute_all
from market.regime import Regime
from risk.trade_plan import build_trade_plan
from strategies.base import Strategy, StrategyContext
from strategies.strategy_engine import get_strategy
from backtesting.metrics import compute_metrics
from backtesting.simulator import SimOrder, manage_bar, position_costs, position_pnl, try_fill


@dataclass
class BacktestConfig:
    profile: TradingProfile
    costs: CostConfig
    interval: str = "5m"
    partial_fraction: float = 0.5
    allow_short: bool = True
    regime_by_day: dict | None = None  # {date: Regime}


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    metrics: dict
    per_strategy: dict = field(default_factory=dict)
    signals: int = 0
    rejected_plans: int = 0
    notes: list[str] = field(default_factory=list)


def _session_last_bar(index: pd.DatetimeIndex) -> pd.Series:
    d = pd.Series(index.date, index=index)
    return d != d.shift(-1)


def run_symbol(df: pd.DataFrame, symbol: str, strategy: Strategy, cfg: BacktestConfig,
               ind: pd.DataFrame | None = None, sector: str = "UNKNOWN") -> tuple[list[dict], int, int]:
    """Replay one symbol through one strategy. Returns (trades, signals, rejected_plans)."""
    ind = compute_all(df, cfg.interval) if ind is None else ind
    ind = strategy.prepare(ind)
    p = cfg.profile
    ctx = StrategyContext(symbol=symbol, interval=cfg.interval, tick=p.tick_size, allow_short=cfg.allow_short,
                          no_new_entries_after=parse_hhmm(p.no_new_entries_after))
    sq = parse_hhmm(p.square_off_time)
    last_bar = _session_last_bar(ind.index).to_numpy()
    slip = cfg.costs.slippage_bps_per_side + cfg.costs.default_half_spread_bps
    trades, n_sig, n_rej = [], 0, 0
    order: SimOrder | None = None
    pos = None
    for i in range(len(ind)):
        ts = ind.index[i]
        bar = ind.iloc[i]
        is_sq = ts.time() >= sq or bool(last_bar[i])
        if pos is not None:
            manage_bar(pos, bar, ts, slip, cfg.partial_fraction, square_off=is_sq)
            if pos.closed:
                trades.append(_trade_record(pos, cfg, sector))
                pos = None
            continue  # one position at a time per (symbol, strategy)
        if order is not None:
            if ts.date() != order.signal_ts.date() or is_sq:
                order = None
            else:
                status, newpos = try_fill(order, bar, i, ts, slip)
                if status == "filled":
                    pos, order = newpos, None
                    if is_sq and not pos.closed:
                        manage_bar(pos, bar, ts, slip, cfg.partial_fraction, square_off=True)
                    if pos.closed:
                        trades.append(_trade_record(pos, cfg, sector))
                        pos = None
                    continue
                if status in ("missed", "expired"):
                    order = None
        if order is None and pos is None and not is_sq:
            sig = strategy.evaluate(ind, i, ctx)
            if sig is None:
                continue
            n_sig += 1
            if cfg.regime_by_day is not None:
                reg = cfg.regime_by_day.get(ts.date())
                if reg is None or reg in (Regime.HIGH_VOLATILITY, Regime.UNKNOWN) or (
                        strategy.suitable_regimes and reg not in strategy.suitable_regimes):
                    n_rej += 1
                    continue
            plan = build_trade_plan(sig, ind, p, cfg.costs, current_price=float(bar["close"]), price_label="HISTORICAL")
            structural = [x for x in plan.issues if "verified current price" not in x]
            if structural:
                n_rej += 1
                continue
            order = SimOrder(symbol, strategy.key, sig.direction, plan.entry_trigger, plan.entry_limit,
                             plan.stop_loss, plan.target1, plan.target2, plan.quantity, i, sig.valid_bars, ts, sector)
    if pos is not None:  # data ended with an open position: close at last close
        manage_bar(pos, ind.iloc[-1], ind.index[-1], slip, cfg.partial_fraction, square_off=True)
        trades.append(_trade_record(pos, cfg, sector))
    return trades, n_sig, n_rej


def _trade_record(pos, cfg: BacktestConfig, sector: str) -> dict:
    o = pos.order
    gross = position_pnl(pos)
    costs = position_costs(pos, cfg.costs)
    risk = abs(pos.entry_price - o.stop) * o.qty
    exit_qty = sum(q for _, q, _, _ in pos.exits)
    avg_exit = sum(px * q for px, q, _, _ in pos.exits) / exit_qty if exit_qty else None
    last_ts = pos.exits[-1][3] if pos.exits else pos.entry_ts
    mins = INTERVAL_MINUTES.get(cfg.interval, 5)
    bars = max(1, int((last_ts - pos.entry_ts).total_seconds() // 60 // mins) + 1)
    return {
        "symbol": o.symbol, "strategy": o.strategy, "direction": o.direction, "sector": sector,
        "signal_ts": o.signal_ts, "entry_ts": pos.entry_ts, "exit_ts": last_ts, "qty": o.qty,
        "entry_price": pos.entry_price, "avg_exit_price": round(avg_exit, 2) if avg_exit else None,
        "stop": o.stop, "target1": o.target1, "target2": o.target2,
        "exit_reason": " + ".join(r for _, _, r, _ in pos.exits), "gross_pnl": round(gross, 2),
        "costs": round(costs, 2), "net_pnl": round(gross - costs, 2),
        "r_multiple": round((gross - costs) / risk, 3) if risk > 0 else 0.0, "bars_held": bars,
    }


def run_backtest(data: dict[str, pd.DataFrame], strategy_keys: list[str], cfg: BacktestConfig,
                 strategy_params: dict | None = None, sectors: dict[str, str] | None = None) -> BacktestResult:
    strategy_params = strategy_params or {}
    all_trades, sig_total, rej_total = [], 0, 0
    for sym, df in data.items():
        if df is None or df.empty:
            continue
        ind = compute_all(df, cfg.interval)
        for key in strategy_keys:
            s = get_strategy(key, **strategy_params.get(key, {}))
            t, ns, nr = run_symbol(df, sym, s, cfg, ind=ind, sector=(sectors or {}).get(sym, "UNKNOWN"))
            all_trades.extend(t)
            sig_total += ns
            rej_total += nr
    trades = pd.DataFrame(all_trades)
    if not trades.empty:
        trades = trades.sort_values(["entry_ts", "symbol", "strategy"], kind="mergesort").reset_index(drop=True)
    mins = INTERVAL_MINUTES.get(cfg.interval, 5)
    metrics = compute_metrics(trades, cfg.profile.capital, mins)
    per = {}
    if not trades.empty:
        for k, g in trades.groupby("strategy"):
            per[k] = compute_metrics(g, cfg.profile.capital, mins)
    notes = ["Each (symbol, strategy) pair is simulated independently; portfolio-level limits (max trades/day) "
             "are not enforced in the backtest.",
             "Universe = current symbols (survivorship bias possible)."]
    if cfg.regime_by_day is None:
        notes.append("Market-regime filter not applied (no index history supplied).")
    return BacktestResult(trades, metrics, per, sig_total, rej_total, notes)


def split_sessions(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = sorted(set(df.index.date))
    k = max(1, int(len(days) * train_ratio))
    cut = days[k] if k < len(days) else days[-1]
    return df[df.index.date < cut], df[df.index.date >= cut]


def out_of_sample(data: dict[str, pd.DataFrame], strategy_keys: list[str], cfg: BacktestConfig,
                  train_ratio: float = 0.7) -> dict:
    """Note: indicators for the test window are warmed up with the train window
    (causal), but trades are only counted inside the test window."""
    ins, oos = {}, {}
    for sym, df in data.items():
        tr, te = split_sessions(df, train_ratio)
        ins[sym] = tr
        oos[sym] = te
    r_in = run_backtest(ins, strategy_keys, cfg)
    r_full = run_backtest(data, strategy_keys, cfg)
    cut_ts = min((te.index[0] for te in oos.values() if not te.empty), default=None)
    t = r_full.trades
    t_oos = t[pd.to_datetime(t["entry_ts"]) >= cut_ts] if (cut_ts is not None and not t.empty) else t.iloc[0:0]
    return {"in_sample": r_in.metrics,
            "out_of_sample": compute_metrics(t_oos, cfg.profile.capital, INTERVAL_MINUTES.get(cfg.interval, 5)),
            "split_at": str(cut_ts)}


def walk_forward(data: dict[str, pd.DataFrame], strategy_key: str, param_grid: dict[str, list], cfg: BacktestConfig,
                 n_folds: int = 4, min_train_trades: int = 5) -> dict:
    """Anchored walk-forward: for fold k, choose the parameter set with the best
    expectancy on sessions [0, k) and evaluate it on fold k only."""
    all_days = sorted(set(d for df in data.values() for d in df.index.date))
    if len(all_days) < n_folds + 1:
        return {"error": f"Need at least {n_folds + 1} sessions, have {len(all_days)}"}
    fold_size = len(all_days) // (n_folds + 1)
    keys = list(param_grid)
    combos = [dict(zip(keys, v)) for v in itertools.product(*param_grid.values())] or [{}]
    folds, oos_trades = [], []
    for k in range(1, n_folds + 1):
        train_end = all_days[k * fold_size]
        test_end = all_days[min((k + 1) * fold_size, len(all_days) - 1)]
        train = {s: df[df.index.date < train_end] for s, df in data.items()}
        best, best_exp = None, None
        for combo in combos:
            r = run_backtest(train, [strategy_key], cfg, {strategy_key: combo})
            m = r.metrics
            if m.get("trades", 0) >= max(min_train_trades, 1):
                if best_exp is None or m["expectancy_inr"] > best_exp:
                    best, best_exp = combo, m["expectancy_inr"]
        if best is None:
            folds.append({"fold": k, "train_end": str(train_end), "chosen": None, "note": "too few training trades"})
            continue
        upto = {s: df[df.index.date <= test_end] for s, df in data.items()}
        r = run_backtest(upto, [strategy_key], cfg, {strategy_key: best})
        t = r.trades
        if not t.empty:
            ed = pd.to_datetime(t["entry_ts"]).dt.date
            t = t[(ed >= train_end) & (ed <= test_end)]
        oos_trades.append(t)
        folds.append({"fold": k, "train_end": str(train_end), "test_end": str(test_end), "chosen": best,
                      "train_expectancy": best_exp,
                      "test": compute_metrics(t, cfg.profile.capital, INTERVAL_MINUTES.get(cfg.interval, 5))})
    allt = pd.concat(oos_trades) if oos_trades else pd.DataFrame()
    return {"folds": folds, "combined_oos": compute_metrics(allt, cfg.profile.capital,
                                                           INTERVAL_MINUTES.get(cfg.interval, 5))}
