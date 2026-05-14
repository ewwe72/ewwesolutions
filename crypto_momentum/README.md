# Cross-Sectional Crypto Momentum

Sibling of the [stocks momentum bot](../momentum). Same architecture,
different asset class. Weekly rebalance on a top-N pick from the
Alpaca-tradable USD crypto pairs, ranked by 30-day return. Long-only
spot, fractional sizing, paper-only by code property.

> **Paper trading only.** `AlpacaClient` rejects live mode unless
> explicitly constructed with `allow_live=True`. There is no CLI path
> to set that. Runs against a **separate** Alpaca paper account from
> the stocks bot — that's why the project lives in its own directory
> with its own `.env`.

## How it differs from the stocks bot

| | Stocks bot | This bot |
|---|---|---|
| Universe | S&P 500 snapshot (~500 names) | ~15 Alpaca crypto pairs |
| Signal | 12-1 momentum (252-day, skip 21) | 30-day return, no skip |
| Rebalance cadence | Monthly, first trading day | Weekly, Mondays |
| Position count | Top 50 | Top 7 |
| Per-name cap | 3% | 18% |
| Cash reserve | 2% | 5% |
| Slippage | 5 bps/side | 25 bps/side |
| DD halt threshold | -35% / -15% | -50% / -25% |
| Markets | NYSE hours, weekdays | 24/7 |
| Position qty | int (whole shares) | float (fractional) |
| Sharpe annualisation | √252 | √365 |
| Benchmark | SPY buy-and-hold | BTC buy-and-hold |
| Discord footer | `momentum-bot alert` | `crypto-momentum alert` |

Everything else — pure-function strategy primitives, state schema,
executor pattern, reconciliation halt, feed-health gate,
`--reset-from-broker` recovery, Discord wrapper, exit-code conventions —
is structurally identical.

## Backtest results (2019–2024 walk-forward)

| | IS (2019–2022) | OOS (2023–2024) |
|---|---:|---:|
| Strategy total return | **+1645.61%** | +280.80% |
| Strategy CAGR | 104.50% | 95.23% |
| Strategy Sharpe | 1.39 | 1.49 |
| Strategy MaxDD | -67.29% | -41.33% |
| BTC buy-and-hold total | +330.53% | **+461.98%** |
| BTC buy-and-hold Sharpe | 0.88 | **2.01** |

**Honest read:**

- IS Sharpe ≈ OOS Sharpe — no obvious overfit. Both windows show real,
  positive risk-adjusted returns.
- Strategy crushed BTC in the 2019–2022 alt boom cycle. The rotating
  basket caught DOGE, SHIB, AVAX, SOL, etc. rallies that BTC missed.
- In 2023–2024, BTC dominated the asset class and the strategy **lost
  to BTC buy-and-hold** on every dimension: return, Sharpe, and MaxDD.
- Crypto momentum's measured edge is cycle-dependent in a way the
  stocks bot's edge is not. Expect it to look great in alt seasons and
  bad in BTC-dominated runs.

This is consistent with the academic literature: crypto momentum is
real but noisier than equity momentum, and the cross-section is dominated
by one asset (BTC) in a way the S&P 500 cross-section is not.

## Risk controls

Identical structural set to the stocks bot, with crypto-appropriate
thresholds:

| Trigger | Action | Exit code |
|---|---|---:|
| Reconciliation divergence (broker vs state) | Hard halt | 2 |
| Data feed unhealthy at rebalance | Skip rebalance | 3 |
| Equity ≤ peak × (1 − 0.50) | Halve sizing next rebalance | — |
| Equity ≥ peak × (1 − 0.25) | Resume full sizing (hysteresis) | — |
| `--reset-from-broker` without confirmation | Refuse, no state change | 4 |

The DD halt thresholds (−50% / −25%) are **looser** than the stocks bot's
(−35% / −15%) because crypto's normal-cycle drawdowns are larger. The
stocks values would essentially live in halt-active mode through every
crypto winter — a wholly different operational regime than what the
catastrophe-backstop semantics intend.

Reconciliation is filtered to `asset_class == "crypto"`, so any
non-crypto positions in the account (unlikely on this dedicated paper
account, but defensive) are ignored by both the reconcile check and
the `--reset-from-broker` recovery.

## Repository layout

```
.
├── config.yaml
├── requirements.txt
├── mypy.ini
├── README.md
├── src/
│   ├── strategy.py        # 30-day momentum, fractional sizing, weekly rebal
│   ├── backtest.py        # yfinance crypto data, daily calendar
│   ├── live.py            # 24/7 markets, UTC-anchored, asset_class filter
│   ├── executor.py        # fractional GTC orders, tolerance reconcile
│   ├── data.py            # CryptoHistoricalDataClient + TradingClient
│   ├── state.py           # float qty, schema v1
│   ├── logger.py
│   └── utils/
│       ├── crypto_universe.py  # Alpaca↔yfinance symbol mapping
│       └── time.py             # UTC helpers
├── scripts/
│   ├── run_live.py        # Discord wrapper
│   └── run_crypto.bat     # Task Scheduler entry point
└── tests/
    ├── test_strategy.py
    ├── test_state.py
    ├── test_live.py
    └── test_alert.py
```

## Running

### Tests + type check

```bash
pytest tests/
mypy --strict src/ scripts/
```

### Backtest

```bash
python -m src.backtest --start 2019-01-01 --end 2024-12-31 \
    --walk-forward --report reports/
```

### Live (paper)

```bash
# Dry run — plan but submit nothing
python -m scripts.run_live --state state/momentum_state.json --dry-run

# Force rebalance now regardless of weekday (operator use)
python -m scripts.run_live --state state/momentum_state.json --force-rebalance

# Normal scheduled run — rebalances only on Mondays
python -m scripts.run_live --state state/momentum_state.json
```

`scripts.run_live` is the Discord-alerting wrapper. It runs the bot,
diffs state before/after, parses today's log, and posts an embed to the
webhook in the `BIGGA` env var on notable events. Quiet on routine no-op
days; pings on rebalance executed, halt activated/resumed, or any
non-zero exit.

### Scheduling on Windows

The bot is meant to run **daily, 7 days a week** (crypto markets don't close):

```cmd
schtasks /create /tn "CryptoMomentumBot" ^
    /tr "C:\path\to\crypto_momentum\scripts\run_crypto.bat" ^
    /sc daily /st 15:35 /f
```

Pick any time — the rebalance trigger is internal day-of-week logic,
not the scheduled fire time. 15:35 Poland keeps it aligned with the
stocks bot's fire time so both books update around the same moment.

## Caveats — read before believing any backtest number

* **Survivorship-of-listing bias.** The universe is what Alpaca lists
  today. Coins that delisted, rugged, or died (LUNA, FTT, etc.) are
  absent from both IS and OOS — the walk-forward comparison cannot
  detect this. Crypto survivorship is harder to quantify than equities'
  but is non-trivial; treat backtest returns as upper bounds.
* **BTC dominance.** The 2023–2024 OOS shows that in BTC-dominated
  cycles, diversification into alts is a drag. If you expect another
  multi-year BTC-led run, buy-and-hold BTC will likely beat this
  strategy net of costs. The strategy's edge concentrates in alt-season
  regimes.
* **Yfinance crypto bars are aggregated across venues.** Alpaca's actual
  fills will differ from the backtest's assumed open prices by more
  than the 25-bps slippage estimate during volatile periods. Treat the
  cost model as optimistic in dislocated markets.
* **No funding-rate / staking yield modelling.** Spot-only; ignores
  perpetuals carry, staking, and protocol rewards. Some of the
  underlying coins (ETH, SOL, DOT) have non-trivial native yields not
  captured here.
* **Fractional sizing rounds to 8 decimals.** Alpaca accepts more
  precision but 8 dp is enough for any pair on the venue; the rounding
  is < 1 USD on a $100K book.
* **No leverage.** Long-only spot. The strategy cannot short on Alpaca
  crypto; in bear markets it goes to cash by selling all losing names
  (or rather, the top-7 are still all-positive momentum names in a
  bull, all-negative in a bear, and the bot just holds whatever's left).

## Live deployment notes

This is paper-only by code property. Before pointing at real money,
the same checklist applies as the stocks bot, plus:

1. **Real Alpaca crypto execution.** Paper fills behave like instantaneous
   market orders at the displayed quote. Real execution can be much
   worse — verify slippage assumption (25 bps) holds in live paper
   under realistic volatility before assuming it'd hold live.
2. **Custody.** Spot crypto on Alpaca is custodied by Alpaca. This is
   a different risk profile than self-custody. Understand who has the
   keys.
3. **Tax accounting.** Crypto-to-crypto in the US is a taxable event;
   weekly rotation creates substantial 1099-B reporting. The bot does
   not produce tax lots.

## License & disclaimer

Educational use. Past performance — backtest or paper — does not
predict future returns.
