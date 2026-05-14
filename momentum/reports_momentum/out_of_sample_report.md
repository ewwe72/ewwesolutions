# Backtest Report - out_of_sample

| Metric | Value |
|---|---:|
| Total return | 205.47% |
| CAGR | 20.47% |
| Sharpe | 0.93 |
| Sortino | 1.16 |
| Max drawdown | -35.97% |
| Max DD duration | 401 days |
| Rebalances | 72 |
| Trades (closed positions) | 1968 |
| Avg names selected | 50.0 |
| Avg turnover per rebalance | 57.6% |
| Drawdown halts | 4 |
| Win rate (closed positions) | 72.82% |
| Profit factor | 9.43 |
| Avg winner | 30.04% |
| Avg loser | -8.53% |
| Expectancy / position | 19.55% |

## Modelling notes

- Universe: S&P 500 constituents as of 2026-01 (snapshot list).
- **Survivorship bias warning:** the universe list is static; symbols
  removed from the index historically are not in the universe. This
  likely inflates measured returns by ~1-3%/year.
- Signal: 12-1 momentum (price 21 days ago / price 252 days ago - 1).
- Selection: top N by score, equal-weighted with per-name and
  cash-reserve caps.
- Rebalance: first trading day of each month.
- Costs: 5 bps slippage per side, $0 commission (Alpaca reality).
- Risk: drawdown halt reduces sizing to 50% when DD <= -25%; resumes
  full size when DD >= -10% (hysteresis prevents flip-flopping).
- Data feed: Yahoo Finance (yfinance), split/dividend-adjusted
  (auto_adjust=True). Alpaca remains the broker for live trading.