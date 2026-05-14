"""Live driver: data-feed sanity gate and --reset-from-broker recovery.

The non-trivial parts of ``src.live`` are pure functions and small CLI
shims. The feed-health check is exercised directly. ``--reset-from-broker``
is exercised end-to-end against a stub Alpaca client.

What is intentionally NOT tested here:
  * Network I/O (yfinance, Alpaca trading endpoints) — those are exercised
    by ``test_executor.py`` against a broker stub and by manual paper runs.
  * ``run_once`` end-to-end — that pulls a 450-day yfinance window over
    500+ symbols. Smoke-test it manually via ``--dry-run``.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.data import PositionSnapshot
from src.live import (
    _check_feed_health,
    _reset_state_from_broker,
)
from src.state import Holding, LiveState, load_state, save_state


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _frame_with_last_bar(last_date: pd.Timestamp, n_rows: int = 5) -> pd.DataFrame:
    """Build a tiny OHLCV-shaped frame whose final index is ``last_date``."""
    idx = pd.date_range(end=last_date, periods=n_rows, freq="B")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1_000_000},
        index=idx,
    )


YESTERDAY = pd.Timestamp("2025-06-02")  # arbitrary trading day
TWO_DAYS_AGO = pd.Timestamp("2025-05-30")
WAY_OLD = pd.Timestamp("2025-05-15")


# --------------------------------------------------------------------------- #
# _check_feed_health                                                          #
# --------------------------------------------------------------------------- #

def test_feed_health_passes_when_universe_mostly_fresh_and_canary_present() -> None:
    universe = ("AAA", "BBB", "CCC", "DDD", "AAPL")
    daily = {
        "AAA": _frame_with_last_bar(YESTERDAY),
        "BBB": _frame_with_last_bar(YESTERDAY),
        "CCC": _frame_with_last_bar(TWO_DAYS_AGO),   # within 3-day window
        "DDD": _frame_with_last_bar(YESTERDAY),
        "AAPL": _frame_with_last_bar(YESTERDAY),     # canary fresh-to-yesterday
    }
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.85,
        canary_symbols=("AAPL",),
    )
    assert result.ok
    assert result.n_fresh == 5
    assert result.fresh_pct == 1.0
    assert result.reasons == ()
    assert result.canary_fresh == {"AAPL": True}


def test_feed_health_fails_when_too_few_symbols_loaded() -> None:
    """Mostly-empty load — n_fresh / n_requested below threshold."""
    universe = ("AAA", "BBB", "CCC", "DDD", "AAPL")
    daily = {
        "AAA": _frame_with_last_bar(YESTERDAY),
        "BBB": pd.DataFrame(),   # empty
        "CCC": pd.DataFrame(),
        "DDD": pd.DataFrame(),
        "AAPL": _frame_with_last_bar(YESTERDAY),
    }
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.85,
        canary_symbols=("AAPL",),
    )
    assert not result.ok
    assert result.n_fresh == 2
    assert result.fresh_pct == pytest.approx(0.4)
    assert any("fresh_pct" in r for r in result.reasons)


def test_feed_health_fails_when_universe_uniformly_stale() -> None:
    """All symbols load but every last-bar is older than the window — feed is broken."""
    universe = ("AAA", "BBB", "AAPL", "MSFT", "GOOGL")
    daily = {sym: _frame_with_last_bar(WAY_OLD) for sym in universe}
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.85,
        canary_symbols=("AAPL", "MSFT", "GOOGL"),
    )
    assert not result.ok
    # Both checks fail: aggregate fresh_pct AND no canary at yesterday.
    assert any("fresh_pct" in r for r in result.reasons)
    assert any("canary" in r for r in result.reasons)
    assert result.canary_fresh == {"AAPL": False, "MSFT": False, "GOOGL": False}


def test_feed_health_fails_on_canary_alone_when_aggregate_window_is_lenient() -> None:
    """Aggregate fresh-pct passes (everyone within window) but no canary
    has yesterday's bar specifically — catches uniformly 1-day-stale feed
    that the aggregate window misses."""
    universe = ("AAA", "BBB", "AAPL", "MSFT", "GOOGL")
    daily = {sym: _frame_with_last_bar(TWO_DAYS_AGO) for sym in universe}
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,    # within window
        min_fresh_pct=0.85,
        canary_symbols=("AAPL", "MSFT", "GOOGL"),
    )
    assert not result.ok
    assert result.fresh_pct == 1.0    # aggregate fine
    assert all(not v for v in result.canary_fresh.values())
    assert any("canary" in r for r in result.reasons)


def test_feed_health_passes_with_one_canary_at_yesterday() -> None:
    """Any single canary with yesterday's bar is enough — bar might be missing
    legitimately for one or two of them (corp action, halt) without the whole
    feed being broken."""
    universe = ("AAA", "AAPL", "MSFT", "GOOGL")
    daily = {
        "AAA": _frame_with_last_bar(YESTERDAY),
        "AAPL": _frame_with_last_bar(TWO_DAYS_AGO),   # not at yesterday
        "MSFT": _frame_with_last_bar(YESTERDAY),       # this one rescues us
        "GOOGL": _frame_with_last_bar(TWO_DAYS_AGO),
    }
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.85,
        canary_symbols=("AAPL", "MSFT", "GOOGL"),
    )
    assert result.ok
    assert result.canary_fresh == {"AAPL": False, "MSFT": True, "GOOGL": False}


def test_feed_health_empty_universe_returns_not_ok() -> None:
    """Defensive: a zero-symbol request shouldn't pass."""
    result = _check_feed_health(
        daily_by_symbol={},
        requested_symbols=(),
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.85,
        canary_symbols=("AAPL",),
    )
    # fresh_pct=0.0 < 0.85 → fails on aggregate, and canary is missing → fails again.
    assert not result.ok


# --------------------------------------------------------------------------- #
# _reset_state_from_broker                                                    #
# --------------------------------------------------------------------------- #

class _StubAlpacaClient:
    """Minimal stand-in: only ``positions()`` is exercised by the reset path."""

    def __init__(self, positions: list[PositionSnapshot]) -> None:
        self._positions = positions

    def positions(self) -> list[PositionSnapshot]:
        return list(self._positions)


def _silent_logger() -> Any:
    import logging
    lg = logging.getLogger("test_live_silent")
    lg.handlers = []
    lg.addHandler(logging.NullHandler())
    return lg


def test_reset_from_broker_overwrites_holdings(tmp_path: Path) -> None:
    """Holdings get rewritten from broker positions; idempotency anchors
    and DD state are preserved."""
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(
            last_run_date=date(2025, 6, 1),
            last_rebalance_date=date(2025, 6, 2),
            peak_equity=123_456.0,
            halt_active=True,
            holdings={
                "STALE1": Holding("STALE1", 10, 50.0, date(2024, 1, 1)),
                "STALE2": Holding("STALE2", 5, 200.0, date(2024, 1, 1)),
            },
        ),
        state_path,
    )

    broker = _StubAlpacaClient([
        PositionSnapshot("AAPL", 12, 175.50, market_value=2106.0, unrealized_pl=0.0),
        PositionSnapshot("MSFT", 7, 410.25, market_value=2871.75, unrealized_pl=0.0),
    ])
    rc = _reset_state_from_broker(
        state_path=state_path,
        client=broker,  # type: ignore[arg-type]
        today=date(2025, 6, 3),
        logger=_silent_logger(),
    )
    assert rc == 0

    new_state = load_state(state_path)
    assert set(new_state.holdings) == {"AAPL", "MSFT"}
    assert new_state.holdings["AAPL"].qty == 12
    assert new_state.holdings["AAPL"].avg_cost == 175.50
    assert new_state.holdings["AAPL"].entry_date == date(2025, 6, 3)

    # Unchanged: DD state, idempotency anchors
    assert new_state.peak_equity == 123_456.0
    assert new_state.halt_active is True
    assert new_state.last_run_date == date(2025, 6, 1)
    assert new_state.last_rebalance_date == date(2025, 6, 2)


def test_reset_from_broker_with_empty_broker_clears_holdings(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(holdings={"AAA": Holding("AAA", 1, 10.0, date(2024, 1, 1))}),
        state_path,
    )
    broker = _StubAlpacaClient([])
    rc = _reset_state_from_broker(
        state_path=state_path,
        client=broker,  # type: ignore[arg-type]
        today=date(2025, 6, 3),
        logger=_silent_logger(),
    )
    assert rc == 0
    new_state = load_state(state_path)
    assert new_state.holdings == {}


# --------------------------------------------------------------------------- #
# CLI: --reset-from-broker safety gate                                        #
# --------------------------------------------------------------------------- #

def test_cli_reset_without_confirmation_exits_4_and_does_not_touch_state(
    tmp_path: Path,
) -> None:
    """Without the confirmation flag, --reset-from-broker must refuse and
    leave the state file untouched. Run as a subprocess so we exercise the
    real argparse wiring without spinning up Alpaca."""
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(holdings={"KEEP": Holding("KEEP", 99, 1.0, date(2024, 1, 1))}),
        state_path,
    )
    original_bytes = state_path.read_bytes()

    proc = subprocess.run(
        [sys.executable, "-m", "src.live", "--state", str(state_path),
         "--reset-from-broker"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 4, proc.stderr
    assert "requires --i-understand-this-overwrites-state" in proc.stderr
    # State must be byte-identical — confirmation gate happens before
    # AlpacaClient is even constructed.
    assert state_path.read_bytes() == original_bytes
