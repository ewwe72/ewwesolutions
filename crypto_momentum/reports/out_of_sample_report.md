# Crypto Momentum Backtest - out_of_sample

| Metric | Value |
|---|---:|
| Total return | 280.80% |
| CAGR | 95.23% |
| Sharpe | 1.49 |
| Sortino | 2.22 |
| Max drawdown | -41.33% |
| Max DD duration | 235 days |
| Rebalances | 105 |
| Trades (closed positions) | 420 |
| Avg names selected | 7.0 |
| Avg turnover per rebalance | 47.1% |
| Drawdown halts | 0 |
| Win rate (closed positions) | 66.67% |
| Profit factor | 7.52 |
| Avg winner | 32.23% |
| Avg loser | -8.57% |
| Expectancy / position | 18.63% |

## Benchmark (BTC buy-and-hold, same window)

| Metric | Strategy | BTC | Δ |
|---|---:|---:|---:|
| Total return | 280.80% | 461.98% | -181.18% |
| CAGR | 95.23% | 137.20% | -41.97% |
| Sharpe | 1.49 | 2.01 | -0.52 |
| Max drawdown | -41.33% | -26.18% | -15.15% |

## Modelling notes

- Universe: Alpaca-tradable USD crypto pairs (~15 names). Survivorship
  bias applies: pairs that delisted (LUNA, FTT, etc.) are absent. Crypto
  survivorship is harder to quantify than equities' but is non-trivial.
- Signal: trailing 30-day return, no skip. Crypto literature finds the
  edge on 1-4 week horizons rather than the 12-month horizon used for
  equities.
- Selection: top-N by score, equal-weighted with per-name cap and cash
  reserve. Fractional sizing throughout (no share rounding).
- Rebalance: weekly on the configured weekday (default Monday), with a
  minimum-days-between guard to prevent DST-induced doubles.
- Costs: 25 bps slippage per side. Crypto execution on Alpaca's venue
  is materially worse than equity execution; this is a realistic estimate.
- Sharpe annualises to sqrt(365), not sqrt(252) — crypto trades every day.
- Risk: DD halt at -50%, resume at -25%. Looser than stocks (-35/-15)
  because crypto's normal-cycle drawdowns are larger; tighter thresholds
  would essentially live in halt-active mode through every winter.
- Data feed: Yahoo Finance (yfinance) for crypto bars. Alpaca remains
  the broker for live trading.