# Backtest Report - backtest

| Metric | Value |
|---|---:|
| Total return | 41.34% |
| CAGR | 26.26% |
| Sharpe | 1.56 |
| Sortino | 2.62 |
| Max drawdown | -14.26% |
| Max DD duration | 102 days |
| Rebalances | 18 |
| Trades (closed positions) | 442 |
| Avg names selected | 50.0 |
| Avg turnover per rebalance | 56.2% |
| Drawdown halts | 0 |
| Win rate (closed positions) | 69.68% |
| Profit factor | 9.44 |
| Avg winner | 32.14% |
| Avg loser | -7.83% |
| Expectancy / position | 20.02% |

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
- Data feed: Alpaca daily bars (consolidated for daily timeframe even
  on free tier).