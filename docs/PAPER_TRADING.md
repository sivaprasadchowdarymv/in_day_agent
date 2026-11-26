# Paper trading guide

Paper trading simulates orders. **No order is ever sent to a broker.**

## Lifecycle
```
PENDING ──trigger touched──► OPEN ──T1 (50%) → stop to breakeven──► T2 / stop / square-off ──► CLOSED
   │ price gaps past the limit ► EXPIRED (MISSED – not chased)
   │ not triggered by valid_until (30 min, never after 14:45) ► EXPIRED
   └ user cancel ► CANCELLED
```

1. **Create an order**
   - From **Top 3 Recommendations → Paper-trade SYMBOL**. This copies the trigger, limit, stop, targets and risk-sized quantity.
   - Or from **Paper Trading → Manual paper order**. The quantity is risk-sized from your profile.
2. **Update.** **Update prices & process orders** downloads the 1-minute candles since the last update. Each order is then run through `backtesting/simulator.py`, the same fill rules as the backtester.
3. **Monitor.**
   - Open positions show unrealised P&L from the latest labelled quote.
   - Every fill and exit is written to the event log with its data label.
4. **Close early.** **Close / cancel selected** exits at the latest quote or at a price you enter.
5. **Review.**
   - Closed trades show gross P&L, estimated costs and net P&L.
   - **Performance Analytics** shows the equity curve and daily and strategy-wise results.
   - **Export CSV** downloads the trades.

## Data labels
Paper fills use the configured provider. With Yahoo this is **DELAYED** data, and every event records its label. Treat paper results as an approximation: real fills depend on the live order book.

## Risk guard integration
Today's paper trades feed the account guard:

- realised net P&L
- number of trades taken
- consecutive losses and cooldown
- open exposure
- open positions by sector

New scans respect these limits. For example, after the daily loss limit, the result is **NO TRADE**.

## Demo clock note
In simulated mode with a demo clock, scans replay a past moment. Paper orders keep using the real clock, so they are best used with real (delayed) data during market hours.
