"""Persistent state for the live crypto momentum strategy.

Same shape as the stocks bot's state, with one substantive change:
``Holding.qty`` is a ``float`` here rather than an ``int`` because crypto
trades fractionally. Cost basis and entry-date semantics are identical.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


CURRENT_SCHEMA_VERSION = 1


@dataclass
class Holding:
    """One open crypto position from the bot's point of view.

    ``qty`` is float because crypto is fractionally divisible. Equality
    comparisons must use tolerance (Alpaca returns slightly different
    decimal representations across endpoints) — see executor's
    reconciliation logic.
    """

    symbol: str
    qty: float
    avg_cost: float
    entry_date: date


@dataclass
class LiveState:
    version: int = CURRENT_SCHEMA_VERSION
    last_run_date: date | None = None
    last_rebalance_date: date | None = None
    peak_equity: float = 0.0
    halt_active: bool = False
    holdings: dict[str, Holding] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_run_date": self.last_run_date.isoformat() if self.last_run_date else None,
            "last_rebalance_date": (
                self.last_rebalance_date.isoformat() if self.last_rebalance_date else None
            ),
            "peak_equity": self.peak_equity,
            "halt_active": self.halt_active,
            "holdings": {
                sym: {
                    "qty": h.qty,
                    "avg_cost": h.avg_cost,
                    "entry_date": h.entry_date.isoformat(),
                }
                for sym, h in self.holdings.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveState":
        version = int(data.get("version", 0))
        if version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"State file schema version {version} != expected "
                f"{CURRENT_SCHEMA_VERSION}. Refusing to load — investigate."
            )

        def _date(value: Any) -> date | None:
            return date.fromisoformat(value) if isinstance(value, str) else None

        holdings_data: dict[str, Any] = data.get("holdings") or {}
        holdings = {
            sym: Holding(
                symbol=sym,
                qty=float(h["qty"]),
                avg_cost=float(h["avg_cost"]),
                entry_date=date.fromisoformat(h["entry_date"]),
            )
            for sym, h in holdings_data.items()
        }

        return cls(
            version=version,
            last_run_date=_date(data.get("last_run_date")),
            last_rebalance_date=_date(data.get("last_rebalance_date")),
            peak_equity=float(data.get("peak_equity") or 0.0),
            halt_active=bool(data.get("halt_active") or False),
            holdings=holdings,
        )


def load_state(path: Path) -> LiveState:
    if not path.exists():
        return LiveState()
    with path.open(encoding="utf-8") as fh:
        return LiveState.from_dict(json.load(fh))


def save_state(state: LiveState, path: Path) -> None:
    """Atomic-ish write: write to a sibling .tmp then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, indent=2)
    tmp.replace(path)
