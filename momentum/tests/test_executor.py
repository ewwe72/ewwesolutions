"""Executor: order submission, fill polling, partial-fill handling, reconciliation.

The Alpaca SDK is replaced with an in-memory ``StubBroker`` that satisfies
the ``Broker`` protocol. No network, no sleeps (the executor accepts an
injectable sleep function).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pytest

from src.data import OrderSnapshot
from src.executor import (
    Broker,
    FillResult,
    execute_rebalance_orders,
    find_reconciliation_divergences,
)


# --------------------------------------------------------------------------- #
# Broker stub                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class _StubOrder:
    """One order tracked inside the stub broker."""

    id: str
    symbol: str
    qty: int
    side: str
    fills_to_simulate: list[OrderSnapshot]  # sequence of states get_order() returns
    poll_count: int = 0


@dataclass
class StubBroker:
    """In-memory broker matching the ``Broker`` protocol.

    Behaviour is configurable per symbol via ``script``: a dict mapping
    symbol -> list of OrderSnapshot states returned by successive get_order
    calls. The first state is what get_order returns on its first call,
    second on second call, etc. If the script runs out, the last state
    repeats forever.
    """

    script: dict[str, list[OrderSnapshot]] = field(default_factory=dict)
    rejected_symbols: set[str] = field(default_factory=set)
    orders: dict[str, _StubOrder] = field(default_factory=dict)
    submitted: list[tuple[str, int, str]] = field(default_factory=list)
    _next_id: int = 0

    def submit_market_order(self, *, symbol: str, qty: int, side: str) -> str:
        if symbol in self.rejected_symbols:
            raise RuntimeError(f"simulated broker rejection for {symbol}")
        self._next_id += 1
        order_id = f"ord-{self._next_id}"
        states = self.script.get(symbol, [
            OrderSnapshot(id=order_id, symbol=symbol, status="filled",
                          filled_qty=qty, filled_avg_price=100.0),
        ])
        # Rewrite snapshots to carry the actual order id
        states = [
            OrderSnapshot(id=order_id, symbol=symbol, status=s.status,
                          filled_qty=s.filled_qty, filled_avg_price=s.filled_avg_price)
            for s in states
        ]
        self.orders[order_id] = _StubOrder(
            id=order_id, symbol=symbol, qty=qty, side=side, fills_to_simulate=states,
        )
        self.submitted.append((symbol, qty, side))
        return order_id

    def get_order(self, order_id: str) -> OrderSnapshot:
        order = self.orders[order_id]
        idx = min(order.poll_count, len(order.fills_to_simulate) - 1)
        order.poll_count += 1
        return order.fills_to_simulate[idx]


def _noop_sleep(_: float) -> None:
    return None


# --------------------------------------------------------------------------- #
# execute_rebalance_orders                                                    #
# --------------------------------------------------------------------------- #

def test_sells_submitted_before_buys() -> None:
    """The executor must submit all sell orders before any buy orders so the
    rebalance can fund buys with cash from sells."""
    broker = StubBroker()
    orders = {"AAPL": -5, "MSFT": 3, "GOOG": -2, "NVDA": 4}
    logger = logging.getLogger("test")
    execute_rebalance_orders(broker, orders, logger=logger, sleep=_noop_sleep)

    sides_in_order = [side for (_, _, side) in broker.submitted]
    # All sells before any buys
    last_sell_idx = max(i for i, s in enumerate(sides_in_order) if s == "sell")
    first_buy_idx = min(i for i, s in enumerate(sides_in_order) if s == "buy")
    assert last_sell_idx < first_buy_idx, (
        f"Buys were submitted before all sells completed. Order: {sides_in_order}"
    )


def test_records_actual_fill_qty_for_partial_fill() -> None:
    """A partial fill must be recorded as the actual qty/price reported by
    the broker, not the qty we asked for. Persisting intended fills is how
    state drifts away from reality."""
    broker = StubBroker(script={
        "AAPL": [OrderSnapshot(id="x", symbol="AAPL", status="partially_filled",
                               filled_qty=3, filled_avg_price=187.0),
                 OrderSnapshot(id="x", symbol="AAPL", status="expired",
                               filled_qty=3, filled_avg_price=187.0)],
    })
    logger = logging.getLogger("test")
    fills = execute_rebalance_orders(
        broker, {"AAPL": 10}, logger=logger, sleep=_noop_sleep,
        fill_timeout_seconds=0.1, poll_interval=0.0,
    )
    r = fills["AAPL"]
    assert r.filled_qty == 3
    assert r.submitted_qty == 10
    assert r.is_partial


def test_submission_failure_recorded_as_unfilled() -> None:
    """If the broker rejects on submit, the executor records a zero-fill
    result and moves on — no retry, no exception leaks to the caller."""
    broker = StubBroker(rejected_symbols={"FAIL"})
    logger = logging.getLogger("test")
    fills = execute_rebalance_orders(
        broker, {"FAIL": 5}, logger=logger, sleep=_noop_sleep,
    )
    r = fills["FAIL"]
    assert r.filled_qty == 0
    assert r.final_status == "submit_failed"


def test_timeout_returns_last_seen_state() -> None:
    """If the order never reaches a terminal state within the timeout, the
    executor returns whatever the broker last reported — does NOT cancel,
    does NOT raise. The next rebalance will surface any residual mismatch
    via the reconciliation check."""
    broker = StubBroker(script={
        "STUCK": [
            OrderSnapshot(id="x", symbol="STUCK", status="accepted",
                          filled_qty=0, filled_avg_price=0.0),
        ],
    })
    logger = logging.getLogger("test")
    fills = execute_rebalance_orders(
        broker, {"STUCK": 5}, logger=logger, sleep=_noop_sleep,
        fill_timeout_seconds=0.0, poll_interval=0.0,
    )
    r = fills["STUCK"]
    assert r.filled_qty == 0
    assert r.final_status == "accepted"


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #

def test_reconciliation_clean_when_books_match() -> None:
    persisted = {"AAPL": 10, "MSFT": 5}
    broker = [("AAPL", 10), ("MSFT", 5)]
    assert find_reconciliation_divergences(persisted, broker) == []


def test_reconciliation_flags_qty_mismatch() -> None:
    persisted = {"AAPL": 10}
    broker = [("AAPL", 8)]
    diffs = find_reconciliation_divergences(persisted, broker)
    assert len(diffs) == 1
    assert diffs[0].symbol == "AAPL"
    assert diffs[0].persisted_qty == 10
    assert diffs[0].broker_qty == 8


def test_reconciliation_flags_position_missing_in_broker() -> None:
    """Bot thinks it owns AAPL; Alpaca says no position. Probably a stop or
    margin call we missed. Halt."""
    persisted = {"AAPL": 10}
    broker: list[tuple[str, int]] = []
    diffs = find_reconciliation_divergences(persisted, broker)
    assert len(diffs) == 1
    assert diffs[0].persisted_qty == 10
    assert diffs[0].broker_qty == 0


def test_reconciliation_flags_position_missing_in_state() -> None:
    """Bot thinks it owns nothing; Alpaca shows AAPL. Account had unrelated
    activity or the state file got out of sync. Halt — operator's call."""
    persisted: dict[str, int] = {}
    broker = [("AAPL", 10)]
    diffs = find_reconciliation_divergences(persisted, broker)
    assert len(diffs) == 1
    assert diffs[0].persisted_qty == 0
    assert diffs[0].broker_qty == 10
