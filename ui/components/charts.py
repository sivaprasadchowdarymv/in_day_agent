"""Plotly charts."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def candle_chart(ind: pd.DataFrame, title: str = "", plan=None, levels: list[dict] | None = None,
                 show: tuple[str, ...] = ("ema9", "ema21", "vwap"), bars: int = 150, height: int = 720) -> go.Figure:
    d = ind.tail(bars)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.62, 0.19, 0.19], vertical_spacing=0.03,
                        subplot_titles=("", "RSI 14", "MACD"))
    x = d.index.strftime("%d-%b %H:%M")
    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"], close=d["close"], name="Price",
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), 1, 1)
    colors = {"ema9": "#f39c12", "ema21": "#2980b9", "ema50": "#8e44ad", "ema200": "#7f8c8d", "vwap": "#e84393",
              "supertrend": "#16a085", "bb_upper": "#95a5a6", "bb_lower": "#95a5a6"}
    for col in show:
        if col in d and d[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=d[col], name=col.upper(), mode="lines",
                                     line=dict(width=1.4, color=colors.get(col))), 1, 1)
    for lv in levels or []:
        fig.add_hline(y=lv["price"], line=dict(color="rgba(120,120,120,.45)", dash="dot", width=1), row=1, col=1,
                      annotation_text=f"S/R {lv['price']:,.2f} ({lv['touches']}×)", annotation_position="top left",
                      annotation_font_size=10, annotation_font_color="gray")
    if plan is not None:
        for y, name, col in ((plan.entry_trigger, "Trigger", "#2980b9"), (plan.entry_limit, "Limit", "#5dade2"),
                             (plan.stop_loss, "Stop", "#e74c3c"), (plan.target1, "T1", "#27ae60"),
                             (plan.target2, "T2", "#27ae60"), (plan.target3, "T3", "#27ae60")):
            if y:
                fig.add_hline(y=y, line=dict(color=col, width=1.2, dash="dash"), row=1, col=1,
                              annotation_text=f"{name} {y:,.2f}", annotation_position="right")
    if "rsi14" in d:
        fig.add_trace(go.Scatter(x=x, y=d["rsi14"], name="RSI", line=dict(color="#8e44ad", width=1.3)), 2, 1)
        for y in (30, 70):
            fig.add_hline(y=y, line=dict(color="gray", width=0.7, dash="dot"), row=2, col=1)
    if "macd" in d:
        fig.add_trace(go.Bar(x=x, y=d["macd_hist"], name="Hist",
                             marker_color=["#26a69a" if v >= 0 else "#ef5350" for v in d["macd_hist"].fillna(0)]), 3, 1)
        fig.add_trace(go.Scatter(x=x, y=d["macd"], name="MACD", line=dict(color="#2c3e50", width=1.1)), 3, 1)
        fig.add_trace(go.Scatter(x=x, y=d["macd_signal"], name="Signal", line=dict(color="#e67e22", width=1.1)), 3, 1)
    fig.update_layout(title=dict(text=title, x=0, y=0.995, font=dict(size=16)), height=height,
                      xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=80, b=10),
                      legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0), hovermode="x unified")
    fig.update_xaxes(type="category", nticks=10)
    return fig


def bar_chart(df: pd.DataFrame, x: str, y: str, title: str = "", height: int = 320) -> go.Figure:
    colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df[y]]
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker_color=colors, text=[f"{v:+.2f}" for v in df[y]],
                           textposition="outside"))
    fig.update_layout(title=title, height=height, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def equity_curve(trades: pd.DataFrame, ts_col: str, pnl_col: str, title: str = "Equity curve (net ₹)") -> go.Figure:
    t = trades.sort_values(ts_col)
    eq = t[pnl_col].cumsum()
    fig = go.Figure(go.Scatter(x=t[ts_col], y=eq, mode="lines+markers", line=dict(color="#2980b9")))
    fig.add_hline(y=0, line=dict(color="gray", dash="dot"))
    fig.update_layout(title=title, height=330, margin=dict(l=10, r=10, t=40, b=10))
    return fig
