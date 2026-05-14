"""Live driver — feed-health gate and --reset-from-broker.

Mirrors the stocks bot's test_live.py with crypto-specific differences:
asset_class filtering, fractional qty in PositionSnapshot, and the
canary list uses crypto symbols.
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
    idx = pd.date_range(end=last_date, periods=n_rows, freq="D")
    return pd.DataFrame(
        {"open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 1_000_000.0},
        index=idx,
    )


YESTERDAY = pd.Timestamp("2025-06-08")
TWO_DAYS_AGO = pd.Timestamp("2025-06-06")
WAY_OLD = pd.Timestamp("2025-05-20")


# --------------------------------------------------------------------------- #
# _check_feed_health                                                          #
# --------------------------------------------------------------------------- #

def test_feed_health_passes_with_fresh_universe_and_canary() -> None:
    universe = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD")
    daily = {sym: _frame_with_last_bar(YESTERDAY) for sym in universe}
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.80,
        canary_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    assert result.ok
    assert result.fresh_pct == 1.0


def test_feed_health_fails_when_too_many_pairs_missing() -> None:
    universe = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD")
    daily = {
        "BTC/USD": _frame_with_last_bar(YESTERDAY),
        "ETH/USD": pd.DataFrame(),
        "SOL/USD": pd.DataFrame(),
        "AVAX/USD": pd.DataFrame(),
        "LINK/USD": _frame_with_last_bar(YESTERDAY),
    }
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.80,
        canary_symbols=("BTC/USD",),
    )
    assert not result.ok
    assert result.fresh_pct == pytest.approx(0.4)


def test_feed_health_fails_when_all_canaries_stale() -> None:
    """Catches uniformly-stale feed that aggregate window would miss."""
    universe = ("BTC/USD", "ETH/USD", "SOL/USD")
    daily = {sym: _frame_with_last_bar(TWO_DAYS_AGO) for sym in universe}
    result = _check_feed_health(
        daily_by_symbol=daily,
        requested_symbols=universe,
        yesterday=YESTERDAY,
        fresh_cutoff=TWO_DAYS_AGO,
        min_fresh_pct=0.80,
        canary_symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
    )
    assert not result.ok
    assert all(not v for v in result.canary_fresh.values())


# --------------------------------------------------------------------------- #
# _reset_state_from_broker — asset_class filter                               #
# --------------------------------------------------------------------------- #

class _StubAlpacaClient:
    def __init__(self, positions: list[PositionSnapshot]) -> None:
        self._positions = positions

    def positions(self) -> list[PositionSnapshot]:
        return list(self._positions)


def _silent_logger() -> Any:
    import logging
    lg = logging.getLogger("test_crypto_live_silent")
    lg.handlers = []
    lg.addHandler(logging.NullHandler())
    return lg


def test_reset_filters_to_crypto_asset_class(tmp_path: Path) -> None:
    """If somehow an equity position is in the account, the reset must
    ignore it — the crypto bot doesn't own that."""
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(
            peak_equity=100_000.0,
            halt_active=False,
            holdings={"STALE/USD": Holding("STALE/USD", 1.0, 100.0, date(2025, 1, 1))},
        ),
        state_path,
    )

    broker = _StubAlpacaClient([
        PositionSnapshot("BTC/USD", 0.5, 50000.0, 25000.0, 0.0, asset_class="crypto"),
        PositionSnapshot("ETH/USD", 7.5, 2500.0, 18750.0, 0.0, asset_class="crypto"),
        # Non-crypto leftover that shouldn't end up in our state
        PositionSnapshot("AAPL", 10.0, 175.0, 1750.0, 0.0, asset_class="us_equity"),
    ])
    rc = _reset_state_from_broker(
        state_path=state_path,
        client=broker,  # type: ignore[arg-type]
        today=date(2025, 6, 9),
        logger=_silent_logger(),
    )
    assert rc == 0

    new_state = load_state(state_path)
    assert set(new_state.holdings) == {"BTC/USD", "ETH/USD"}
    assert new_state.holdings["BTC/USD"].qty == 0.5
    # Idempotency anchors preserved
    assert new_state.peak_equity == 100_000.0


def test_reset_with_empty_broker_clears_holdings(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(holdings={"BTC/USD": Holding("BTC/USD", 0.1, 50000.0, date(2025, 1, 1))}),
        state_path,
    )
    broker = _StubAlpacaClient([])
    rc = _reset_state_from_broker(
        state_path=state_path,
        client=broker,  # type: ignore[arg-type]
        today=date(2025, 6, 9),
        logger=_silent_logger(),
    )
    assert rc == 0
    assert load_state(state_path).holdings == {}


# --------------------------------------------------------------------------- #
# CLI confirmation gate                                                       #
# --------------------------------------------------------------------------- #

def test_cli_reset_without_confirmation_exits_4(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    save_state(
        LiveState(holdings={"KEEP/USD": Holding("KEEP/USD", 1.0, 100.0, date(2025, 1, 1))}),
        state_path,
    )
    original = state_path.read_bytes()

    proc = subprocess.run(
        [sys.executable, "-m", "src.live", "--state", str(state_path), "--reset-from-broker"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 4, proc.stderr
    assert "requires --i-understand-this-overwrites-state" in proc.stderr
    assert state_path.read_bytes() == original
