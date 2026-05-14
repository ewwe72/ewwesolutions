# Crypto Momentum Backtest - in_sample

| Metric | Value |
|---|---:|
| Total return | 1645.61% |
| CAGR | 104.50% |
| Sharpe | 1.39 |
| Sortino | 1.85 |
| Max drawdown | -67.29% |
| Max DD duration | 605 days |
| Rebalances | 208 |
| Trades (closed positions) | 771 |
| Avg names selected | 6.9 |
| Avg turnover per rebalance | 21.5% |
| Drawdown halts | 116 |
| Win rate (closed positions) | 63.16% |
| Profit factor | 6.03 |
| Avg winner | 53.94% |
| Avg loser | -15.35% |
| Expectancy / position | 28.42% |

## Benchmark (BTC buy-and-hold, same window)

| Metric | Strategy | BTC | Δ |
|---|---:|---:|---:|
| Total return | 1645.61% | 330.53% | +1315.08% |
| CAGR | 104.50% | 44.08% | +60.42% |
| Sharpe | 1.39 | 0.88 | +0.52 |
| Max drawdown | -67.29% | -76.63% | +9.34% |

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