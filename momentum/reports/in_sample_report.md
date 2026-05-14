# Backtest Report - in_sample

| Metric | Value |
|---|---:|
| Total return | 326.81% |
| CAGR | 17.52% |
| Sharpe | 0.92 |
| Sortino | 1.17 |
| Max drawdown | -24.91% |
| Max DD duration | 159 days |
| Rebalances | 108 |
| Trades (closed positions) | 3344 |
| Avg names selected | 50.0 |
| Avg turnover per rebalance | 59.1% |
| Drawdown halts | 0 |
| Win rate (closed positions) | 77.30% |
| Profit factor | 10.60 |
| Avg winner | 23.27% |
| Avg loser | -7.48% |
| Expectancy / position | 16.29% |

## Benchmark (SPY buy-and-hold, same window)

| Metric | Strategy | SPY | Δ |
|---|---:|---:|---:|
| Total return | 326.81% | 164.34% | +162.47% |
| CAGR | 17.52% | 11.42% | +6.10% |
| Sharpe | 0.92 | 0.80 | +0.12 |
| Max drawdown | -24.91% | -19.35% | -5.56% |

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
- Risk: drawdown halt reduces sizing to 50% when DD <= halt_threshold;
  resumes full size when DD >= resume_threshold (hysteresis prevents
  flip-flopping). See config.yaml for the exact values used.
- Data feed: Yahoo Finance (yfinance), split/dividend-adjusted
  (auto_adjust=True). Alpaca remains the broker for live trading.