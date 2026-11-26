"""Backtest performance metrics. Sharpe/Sortino use DAILY net P&L returns on
the configured capital, risk-free rate = 0, annualised with √252."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(trades: pd.DataFrame, capital: float, interval_minutes: int = 5) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0, "note": "No trades generated"}
    net = trades["net_pnl"]
    wins, losses = net[net > 0], net[net <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    equity = net.cumsum()
    peak = equity.cummax().clip(lower=0)
    dd = equity - peak
    daily = trades.groupby(pd.to_datetime(trades["exit_ts"]).dt.date)["net_pnl"].sum()
    rets = daily / capital
    sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 1 and rets.std(ddof=1) > 0 else None
    downside = rets[rets < 0]
    sortino = (float(rets.mean() / downside.std(ddof=1) * np.sqrt(252))
               if len(downside) > 1 and downside.std(ddof=1) > 0 else None)
    return {
        "trades": int(len(trades)),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
        "expectancy_inr": round(float(net.mean()), 2),
        "expectancy_r": round(float(trades["r_multiple"].mean()), 3) if "r_multiple" in trades else None,
        "avg_win_inr": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_inr": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "max_drawdown_inr": round(float(dd.min()), 2),
        "max_drawdown_pct_of_capital": round(float(dd.min()) / capital * 100, 2),
        "sharpe_daily_annualised": round(sharpe, 2) if sharpe is not None else None,
        "sortino_daily_annualised": round(sortino, 2) if sortino is not None else None,
        "avg_holding_minutes": round(float(trades["bars_held"].mean()) * interval_minutes, 1),
        "gross_pnl_inr": round(float(trades["gross_pnl"].sum()), 2),
        "total_costs_inr": round(float(trades["costs"].sum()), 2),
        "net_pnl_inr": round(float(net.sum()), 2),
        "trading_days": int(len(daily)),
        "disclaimer": "Historical simulation with estimated costs; not a guarantee of future returns.",
    }
