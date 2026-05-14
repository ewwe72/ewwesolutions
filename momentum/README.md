# Cross-Sectional Momentum on the S&P 500

A long-only monthly momentum strategy on US large caps, with a backtest harness
and an Alpaca paper-trading driver. Implements the canonical Jegadeesh & Titman
(1993) 12-1 construction: rank by trailing 12-month return excluding the most
recent 21 trading days, hold the top N equal-weighted, rebalance monthly.

> **Paper trading only.** `AlpacaClient` rejects live mode unless explicitly
> constructed with `allow_live=True`. There is no CLI path to set that.

---

## What the bot does

Once per trading day (designed to be run from cron/Task Scheduler):

1. **Reconcile.** Compare persisted holdings against Alpaca's authoritative
   position list. Any divergence → hard halt, non-zero exit, no trading. The
   operator must resolve it manually before the next run.
2. **Update drawdown state.** Pull account equity, update peak-equity and
   the catastrophe drawdown flag. Runs daily, not just on rebalance days.
3. **Rebalance — first trading day of the month only.**
   * Load ~450 days of daily bars from yfinance for the S&P 500 universe.
   * For each symbol, compute the 12-1 momentum score
     (`price(t−21) / price(t−252) − 1`) and 60-day average dollar volume.
   * Filter: ≥252 days of history, last price ≥ \$5, ADV ≥ \$10M.
   * Pick the top 50 by momentum score, equal-weight with a 3% per-name cap
     and a 2% cash reserve.
   * Diff against current holdings and submit sells-then-buys via Alpaca.
   * Update persisted state from actual fills (not intended fills).

**Universe:** a snapshot of S&P 500 constituents committed in `src/utils/sp500.py`.
This is **not** point-in-time membership — see the survivorship-bias caveat below.

---

## Risk controls — read carefully

The safeguards in this bot, in order of how much they actually protect you:

| Trigger | Action | Exit code |
|---|---|---:|
| Reconciliation divergence (broker vs state) | Hard halt, no trades | 2 |
| Data-feed unhealthy at rebalance time (see below) | Skip rebalance, no trades | 3 |
| Account equity ≤ `peak × (1 − 0.35)` | Cut next rebalance sizing to 50% of equity | — |
| Account equity ≥ `peak × (1 − 0.15)` | Resume full sizing (hysteresis) | — |

**Data-feed sanity gate** (configured under `live:` in `config.yaml`):
On rebalance mornings, the bot refuses to trade unless
(a) `≥ min_universe_fresh_pct` of the universe (default 85%) has a bar dated
within `staleness_window_days` (default 3) of the prior trading day, and
(b) at least one `canary_symbol` (AAPL/MSFT/GOOGL by default) has a bar
dated exactly the prior trading day. (a) catches yfinance returning empty
frames for a chunk of the universe; (b) catches a uniformly-stale feed
that sneaks through (a). Both must pass; failure exits 3.

**Recovery escape hatch.** When reconciliation fails and the operator has
determined the broker is the source of truth, `--reset-from-broker
--i-understand-this-overwrites-state` rewrites persisted holdings from
Alpaca's positions, sets cost basis to broker `avg_entry_price`, and
sets entry-date to today (real entry dates cannot be recovered). Drawdown
state and idempotency anchors are left untouched. This is the *only* way
to clear a reconciliation halt without hand-editing JSON; it is
deliberately verbose to require to keep it from being a one-flag mistake.

Notes on what is **not** here:

* No per-trade stop loss. The strategy has no concept of a stop — exits happen
  only at the next monthly rebalance.
* No daily-loss or weekly-loss kill switch. The DD throttle uses
  peak-to-current equity, sampled once per run, with thresholds calibrated for
  bear markets (2008-style), not normal-DD events.
* No PDT protection. A small account that runs this can violate the
  pattern-day-trader rule on rebalance days. Address before any live use.
* No external alerting on the non-zero exit codes. The bot exits 2/3/4 on
  failure, but you still need something *outside* the process (cron mailer,
  webhook in a wrapper script, dashboard) to actually tell you.

---

## Repository layout

```
.
├── config.yaml               # all tunable parameters
├── requirements.txt
├── mypy.ini
├── README.md
├── src/
│   ├── strategy.py           # 12-1 momentum, eligibility, top-N, equal-weight, diff
│   ├── backtest.py           # event-loop backtest + walk-forward + report
│   ├── live.py               # one-shot-per-day live driver
│   ├── executor.py           # order submission and reconciliation
│   ├── data.py               # Alpaca trading + account wrappers
│   ├── state.py              # JSON state persistence (holdings, peak equity, halt flag)
│   ├── logger.py             # structured JSON logging
│   └── utils/
│       ├── sp500.py          # universe snapshot (survivorship-biased)
│       ├── universe.py       # NYSE trading calendar
│       └── time.py           # ET timezone helpers
└── tests/
    ├── test_strategy.py
    ├── test_lookahead.py     # no-lookahead assertions on the engine
    ├── test_drawdown_halt_engine.py
    ├── test_state.py
    └── test_executor.py
```

---

## Setup

Requires Python 3.11+ (uses `zoneinfo` from stdlib).

```bash
python -m venv venv
venv\Scripts\activate          # PowerShell; on POSIX: source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` with your Alpaca paper credentials:

```
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
APCA_API_BASE_URL=https://paper-api.alpaca.markets
```

Paper keys: <https://app.alpaca.markets/paper/dashboard/overview>.

`live.py` uses Alpaca for execution and account state, but pulls bars from
**yfinance** (no credentials needed). Alpaca's free tier doesn't backfill daily
bars far enough to support the 2010 IS window.

---

## Running

### Tests and type-check

```bash
pytest tests/ -v
mypy --strict src/
```

### Backtest

```bash
python -m src.backtest --start 2010-01-01 --end 2024-12-31 \
    --walk-forward --report reports_momentum/
```

`--walk-forward` splits at end-of-2018 and writes both `in_sample_*` and
`out_of_sample_*` reports plus a `walk_forward_summary.md`. Parameters are
identical across windows — there is no per-fold optimisation.

Optional flags:

```
--n-positions 50      Top-N names to hold (default 50)
--position-cap 0.03   Max weight per name (default 3%)
```

Each report directory contains: `*_metrics.json`, `*_trades.csv`,
`*_equity.csv`, `*_rebalances.csv`, `*_equity.png`, `*_drawdown.png`,
`*_returns.png`, `*_report.md`.

### Live (paper)

```bash
python -m src.live --state state/momentum_state.json --dry-run
python -m src.live --state state/momentum_state.json
```

`--dry-run` plans the rebalance and logs the intended orders without
submitting any. The first non-dry run should be on a rebalance day (first
trading day of the month) — on other days the bot only updates drawdown
state.

```
--force-rebalance                       Force the rebalance flow today regardless
                                        of the calendar. Bypasses both the
                                        "first trading day of month" check and
                                        the "already rebalanced today" guard.
                                        Operator use only — useful for starting
                                        paper-trading mid-month or recovering
                                        after manual state surgery. The bot
                                        will still rebalance again on the next
                                        scheduled first-of-month, so you pay
                                        slippage twice for that cycle.

--reset-from-broker
  --i-understand-this-overwrites-state  Overwrite persisted holdings from
                                        Alpaca positions. Required after a
                                        reconciliation halt that the operator
                                        has investigated and decided to resolve
                                        in favor of the broker. Exits after
                                        rewriting; does not run a rebalance.
```

**Exit codes** (so an external scheduler / alerting wrapper can act on them):

| Code | Meaning |
|---:|---|
| 0 | Success (or no-op day, or clean dry-run) |
| 2 | Reconciliation divergence — broker positions don't match persisted state |
| 3 | Data feed unhealthy at rebalance time — too few fresh bars, or canaries stale |
| 4 | `--reset-from-broker` invoked without the confirmation flag |

### Discord alerting wrapper

The bot itself only signals via exit codes and JSON logs. To get push
notifications on the events that matter, run it through the wrapper at
`scripts/run_live.py`:

```bash
python -m scripts.run_live --state state/momentum_state.json
```

The wrapper takes `--state` itself and forwards every other argument to
`src.live` unchanged (so `--dry-run`, `--force-rebalance`, etc. work the
same way). After the bot exits, it diffs the state file before vs after,
parses today's log lines, and posts a Discord embed when something noteworthy
happened. The wrapper's own exit code is the bot's exit code, so any
external scheduler that already watches exit codes keeps working.

**What gets alerted:**

| Event | Color | Why |
|---|---|---|
| Exit 2 (reconciliation failed) | Red | Needs human resolution before next run |
| Exit 3 (feed unhealthy) | Red | Rebalance was skipped; you'll want to know |
| Exit 4 (misuse of `--reset-from-broker`) | Red | Confirms the safety gate held |
| Other non-zero exit | Red | Unexpected — investigate |
| Drawdown halt activated this run | Orange | Sizing dropped to 50% — material |
| Drawdown halt resumed this run | Blue | Back to 100% sizing |
| Rebalance executed this run | Green | Once-monthly confirmation |
| Routine success (no rebalance, no halt change) | (silent) | Avoids spam |
| `--reset-from-broker` invoked | (silent) | Operator is at terminal already |

**Configuration:** put the Discord webhook URL in `.env` as `BIGGA`:

```
BIGGA=https://discord.com/api/webhooks/<id>/<token>
```

If the env var is unset, the wrapper still runs the bot normally; it just
prints a stderr line saying the alert was skipped. The wrapper never
crashes the trading run because of webhook problems.

**Scheduling on Windows** (Task Scheduler, runs weekdays 09:35 ET ≈ 14:35 UTC):

```powershell
schtasks /create /tn "MomentumBot" /tr "python -m scripts.run_live --state state\momentum_state.json" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:35 /sd 01/01/2026
```

Adjust `09:35` to your local clock if you're not on ET. The 5-minute buffer
after open lets yfinance refresh yesterday's bar; the feed-health gate will
catch it if that hasn't happened.

---

## Caveats — read before believing any backtest number

* **Survivorship bias.** `src/utils/sp500.py` is a current snapshot of S&P
  membership, not point-in-time. Names that were dropped from the index
  between 2010 and now (acquisitions, bankruptcies, demotions) are absent.
  Long-only equity backtests against survivor-only universes typically
  overstate returns by 1–3% per year. The bias acts equally in IS and OOS,
  so the walk-forward comparison does **not** detect it.
* **High win rate is not edge.** A long-only equal-weight rebalance of a
  bull-market survivor universe will book most legs at a profit by
  construction. The reports' 70-77% per-leg win rate is not a momentum signal
  — it is selection plus drift.
* **Slippage model is flat 5 bps both sides.** No volume-impact term. Reasonable
  for a ~\$100K notional in S&P 500 names; optimistic at higher notionals.
* **Mark-to-market fallback for missing closes uses cost basis.** A delisting
  mid-month is valued at entry price until the next rebalance. Real delistings
  often go to near zero. Effect is small because the survivor-biased universe
  rarely contains delistings.
* **End-of-backtest liquidations count as trades.** Positions still open on
  `end_date` are closed at that day's close and recorded in trade stats.
  This slightly biases trade-count and win/loss splits by whatever the final
  month did.

---

## Live deployment notes

The strategy was sized assuming a paper bankroll. Before pointing this at real
money:

1. **PDT.** Resolve before any account under \$25K. With ~30 names changing per
   rebalance the bot will routinely exceed 3 round-trips in 5 trading days.
2. **Replay paper for at least a month** and reconcile P&L against broker
   statements daily.
3. **Halve sizing.** Drop `position_cap_pct` from 0.03 to 0.015 for the first
   live month, then ratchet up if the backtest-to-live regression looks sane.
4. **Add a broker-side max-loss bracket.** The in-process drawdown throttle
   only fires while this script is alive. A broker-side circuit breaker that
   doesn't depend on the process is a separate, independent safeguard.

None of these are implemented. They are checklist items.

---

## License & disclaimer

Educational use. Past performance — backtest or paper — does not predict
future returns. You alone are responsible for any decision to deploy against
real capital.
