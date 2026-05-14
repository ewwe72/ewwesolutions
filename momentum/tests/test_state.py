"""State persistence: round-trip, schema versioning, missing-file behavior."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.state import CURRENT_SCHEMA_VERSION, Holding, LiveState, load_state, save_state


def test_load_missing_returns_default(tmp_path: Path) -> None:
    """First-ever run: no state file yet. Should produce an empty state, not crash."""
    state = load_state(tmp_path / "does_not_exist.json")
    assert state.version == CURRENT_SCHEMA_VERSION
    assert state.last_run_date is None
    assert state.last_rebalance_date is None
    assert state.peak_equity == 0.0
    assert state.halt_active is False
    assert state.holdings == {}


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    """Save then load returns identical state. The state file is the source
    of truth between runs, so a lossy round-trip is a correctness bug."""
    original = LiveState(
        last_run_date=date(2026, 4, 1),
        last_rebalance_date=date(2026, 4, 1),
        peak_equity=123456.78,
        halt_active=True,
        holdings={
            "AAPL": Holding(
                symbol="AAPL", qty=12, avg_cost=187.45,
                entry_date=date(2026, 2, 3),
            ),
            "MSFT": Holding(
                symbol="MSFT", qty=5, avg_cost=415.20,
                entry_date=date(2026, 3, 1),
            ),
        },
    )
    path = tmp_path / "state.json"
    save_state(original, path)

    loaded = load_state(path)
    assert loaded.last_run_date == original.last_run_date
    assert loaded.last_rebalance_date == original.last_rebalance_date
    assert loaded.peak_equity == pytest.approx(original.peak_equity)
    assert loaded.halt_active is True
    assert set(loaded.holdings) == set(original.holdings)
    for sym, h in original.holdings.items():
        assert loaded.holdings[sym].qty == h.qty
        assert loaded.holdings[sym].avg_cost == pytest.approx(h.avg_cost)
        assert loaded.holdings[sym].entry_date == h.entry_date


def test_schema_mismatch_refuses_to_load(tmp_path: Path) -> None:
    """A state file with a different schema version must NOT silently load —
    that would be a recipe for misinterpreting persisted fields and trading
    on garbage. Operator must intervene."""
    path = tmp_path / "state.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"version": 999, "holdings": {}}, fh)

    with pytest.raises(ValueError, match="schema version"):
        load_state(path)


def test_atomic_write_does_not_leave_tmp_artefact(tmp_path: Path) -> None:
    """save_state writes to a .tmp sibling then renames. On success, the
    .tmp file should not exist afterwards."""
    state = LiveState(peak_equity=1000.0)
    path = tmp_path / "state.json"
    save_state(state, path)
    assert path.exists()
    assert not (tmp_path / "state.json.tmp").exists()
