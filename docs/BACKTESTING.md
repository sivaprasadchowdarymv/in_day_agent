# Backtesting guide

**Backtests describe the past under stated assumptions. They never prove future profitability.**

## How it works
1. Download history for each symbol. Yahoo limits intraday history: about 60 days for 5m/15m/30m and about 730 days for 1h.
2. Compute indicators once. They only use current and past data.
3. Replay every candle `i`:
   - If a position is open, manage it.
   - Otherwise, if an order is pending, try to fill it.
   - Otherwise, call `strategy.evaluate(ind, i)`. This is the same function used for live scanning.
4. Each signal goes through `build_trade_plan`, the same code as live scanning. That includes the R:R, net-R:R-after-costs and stop-width checks and the position sizing. Plans that fail are counted as `rejected_plans`.

## Fill assumptions (shared with paper trading: `backtesting/simulator.py`)
| Situation | Assumption |
|---|---|
| Price touches the trigger | Fill at the trigger plus slippage (slippage + half-spread bps) |
| Bar opens beyond the trigger but within the limit | Fill at the open plus slippage |
| Bar opens beyond the limit | **MISSED**, no chase |
| Entry bar | Only the stop is checked, because the order of moves inside a bar is unknown |
| Stop and target both inside one bar | **Stop first** (conservative) |
| Bar opens through the stop | Exit at the open (gap loss) |
| T1 hit | 50% exits at T1 and the stop moves to breakeven. The rest exits at T2. If there is no T2, everything exits at T1 |
| Square-off time (15:15) or last bar of the session | Exit at the close |
| Order not filled within `valid_bars` | Expired |

Costs use the configured cost model per leg: brokerage, STT, stamp duty, exchange charge, SEBI fee and GST. Slippage is already in the fill prices.

## Metrics
- Win rate, profit factor and expectancy (₹ and R-multiple)
- Average win and average loss
- Maximum drawdown (₹ and % of capital)
- **Sharpe and Sortino:** from *daily* net P&L divided by capital, with a risk-free rate of 0, annualised by √252. With few trading days these numbers are statistically weak.
- Number of trades, average holding time, gross P&L, total costs and net P&L

## Evaluation modes
- **Full period:** all candles.
- **Out-of-sample split:** the first X% of sessions are in-sample. Metrics are reported separately for trades entered after the split. Indicators warm up on the earlier data but cannot see the future.
- **Walk-forward (anchored):** for fold *k*, each parameter combination is tested on sessions `[0, k)`. The one with the best expectancy (and at least N trades) is then evaluated **only** on fold *k*. Combined out-of-sample metrics are reported.

## Known biases and limits
- **Survivorship:** the symbol list is today's. Delisted or suspended stocks are missing.
- **Independent simulation:** each (symbol, strategy) pair is simulated on its own. Portfolio limits such as maximum trades per day are not enforced in the backtest.
- **Regime filter:** not applied unless you pass `regime_by_day` to `BacktestConfig`.
- **No partial entry fills or queue position:** entries are all-or-nothing.
- **Free intraday data** can have gaps. Check the data warnings shown before the results.
