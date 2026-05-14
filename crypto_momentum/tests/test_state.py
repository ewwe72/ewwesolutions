"""State persistence — same contract as the stocks bot but float qty."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.state import Holding, LiveState, load_state, save_state


def test_load_missing_returns_default(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing.json")
    assert state.peak_equity == 0.0
    assert state.halt_active is False
    assert state.holdings == {}
    assert state.last_run_date is None
    assert state.last_rebalance_date is None


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = LiveState(
        last_run_date=date(2025, 6, 2),
        last_rebalance_date=date(2025, 6, 2),
        peak_equity=125_678.90,
        halt_active=True,
        holdings={
            "BTC/USD": Holding("BTC/USD", 0.37412, 50000.0, date(2025, 6, 2)),
            "ETH/USD": Holding("ETH/USD", 7.2891, 2500.0, date(2025, 6, 2)),
        },
    )
    save_state(original, path)
    reloaded = load_state(path)

    assert reloaded.last_run_date == date(2025, 6, 2)
    assert reloaded.last_rebalance_date == date(2025, 6, 2)
    assert reloaded.peak_equity == pytest.approx(125_678.90)
    assert reloaded.halt_active is True
    assert reloaded.holdings.keys() == {"BTC/USD", "ETH/USD"}
    # Fractional qty round-trip
    assert reloaded.holdings["BTC/USD"].qty == pytest.approx(0.37412)
    assert reloaded.holdings["ETH/USD"].qty == pytest.approx(7.2891)


def test_schema_mismatch_refuses_to_load(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 999, "holdings": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema version"):
        load_state(path)


def test_atomic_write_does_not_leave_tmp_artefact(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(LiveState(peak_equity=1.0), path)
    assert path.exists()
    assert not (path.with_suffix(path.suffix + ".tmp")).exists()
