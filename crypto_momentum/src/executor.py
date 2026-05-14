"""Order execution for crypto rebalancing.

Mirror of the stocks bot's executor with two substantive changes:

  1. Quantities are floats throughout (crypto fractional sizing).
  2. Reconciliation uses a tolerance band (``QTY_TOLERANCE``) rather
     than exact equality — Alpaca occasionally returns slightly
     different decimal reps for the same position across endpoints.

Two-phase ordering (sells → wait → buys) is preserved: even on a 24/7
venue with paper money, settled-cash semantics still matter and the
self-funding rebalance logic is cleaner that way.
"""
from __future__ import annotations

import logging
import time as time_mod
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .data import OrderSnapshot
from .logger import log_event


QTY_TOLERANCE: float = 1e-8


# --------------------------------------------------------------------------- #
# Broker protocol                                                             #
# --------------------------------------------------------------------------- #

class Broker(Protocol):
    """Slice of ``AlpacaClient`` the executor needs.

    Defined as a Protocol so tests can substitute an in-memory stub.
    """

    def submit_market_order(self, *, symbol: str, qty: float, side: str) -> str: ...
    def get_order(self, order_id: str) -> OrderSnapshot: ...


# --------------------------------------------------------------------------- #
# Records                                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FillResult:
    """Outcome of one symbol's order leg."""

    symbol: str
    side: str
    submitted_qty: float
    filled_qty: float
    avg_fill_price: float
    order_id: str
    final_status: str

    @property
    def is_full_fill(self) -> bool:
        return (
            self.submitted_qty > 0
            and abs(self.filled_qty - self.submitted_qty) <= QTY_TOLERANCE
        )

    @property
    def is_partial(self) -> bool:
        return (
            0 < self.filled_qty
            and (self.submitted_qty - self.filled_qty) > QTY_TOLERANCE
        )


# --------------------------------------------------------------------------- #
# Polling                                                                     #
# --------------------------------------------------------------------------- #

def _wait_for_terminal(
    broker: Broker,
    order_id: str,
    *,
    timeout_seconds: float,
    poll_interval: float,
    sleep: Callable[[float], None] | None = None,
) -> OrderSnapshot:
    """Poll until terminal state or timeout."""
    sleep_fn = sleep if sleep is not None else time_mod.sleep
    deadline = time_mod.monotonic() + timeout_seconds
    snap = broker.get_order(order_id)
    while not snap.is_terminal and time_mod.monotonic() < deadline:
        sleep_fn(poll_interval)
        snap = broker.get_order(order_id)
    return snap


def _execute_leg(
    broker: Broker,
    *,
    symbol: str,
    qty: float,
    side: str,
    fill_timeout_seconds: float,
    poll_interval: float,
    logger: logging.Logger,
    sleep: Callable[[float], None] | None = None,
) -> FillResult:
    """Submit one market order, wait for terminal state, return the result."""
    try:
        order_id = broker.submit_market_order(symbol=symbol, qty=qty, side=side)
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger,
            event_type="order_submit_failed",
            symbol=symbol,
            payload={"qty": qty, "side": side, "error": str(exc)},
            level=logging.ERROR,
        )
        return FillResult(
            symbol=symbol, side=side, submitted_qty=qty,
            filled_qty=0.0, avg_fill_price=0.0,
            order_id="", final_status="submit_failed",
        )

    log_event(
        logger,
        event_type="order_submitted",
        symbol=symbol,
        payload={"qty": qty, "side": side, "order_id": order_id},
    )

    snap = _wait_for_terminal(
        broker, order_id,
        timeout_seconds=fill_timeout_seconds,
        poll_interval=poll_interval,
        sleep=sleep,
    )

    result = FillResult(
        symbol=symbol, side=side, submitted_qty=qty,
        filled_qty=snap.filled_qty,
        avg_fill_price=snap.filled_avg_price,
        order_id=order_id,
        final_status=snap.status,
    )

    if result.is_full_fill:
        log_event(
            logger,
            event_type="order_filled",
            symbol=symbol,
            payload={
                "qty": result.filled_qty,
                "avg_price": result.avg_fill_price,
                "order_id": order_id,
            },
        )
    elif result.is_partial:
        log_event(
            logger,
            event_type="order_partial_fill",
            symbol=symbol,
            payload={
                "submitted": qty, "filled": result.filled_qty,
                "avg_price": result.avg_fill_price,
                "final_status": result.final_status,
                "order_id": order_id,
            },
            level=logging.WARNING,
        )
    else:
        log_event(
            logger,
            event_type="order_unfilled",
            symbol=symbol,
            payload={
                "submitted": qty,
                "final_status": result.final_status,
                "order_id": order_id,
            },
            level=logging.WARNING,
        )
    return result


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #

def execute_rebalance_orders(
    broker: Broker,
    orders: dict[str, float],
    *,
    logger: logging.Logger,
    fill_timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    inter_leg_sleep: float = 0.5,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, FillResult]:
    """Execute a rebalance's orders, sells first then buys.

    Positive deltas = buys, negative deltas = sells. Symbols absent are
    skipped. Returns a per-symbol ``FillResult``; the caller updates
    persisted holdings from actual fills.
    """
    sells = {sym: qty for sym, qty in orders.items() if qty < 0}
    buys = {sym: qty for sym, qty in orders.items() if qty > 0}

    sleep_fn = sleep if sleep is not None else time_mod.sleep
    results: dict[str, FillResult] = {}

    log_event(
        logger,
        event_type="rebalance_orders_begin",
        payload={"n_sells": len(sells), "n_buys": len(buys)},
    )

    for sym, qty in sells.items():
        results[sym] = _execute_leg(
            broker, symbol=sym, qty=-qty, side="sell",
            fill_timeout_seconds=fill_timeout_seconds,
            poll_interval=poll_interval, logger=logger, sleep=sleep,
        )

    if buys:
        sleep_fn(inter_leg_sleep)

    for sym, qty in buys.items():
        results[sym] = _execute_leg(
            broker, symbol=sym, qty=qty, side="buy",
            fill_timeout_seconds=fill_timeout_seconds,
            poll_interval=poll_interval, logger=logger, sleep=sleep,
        )

    log_event(
        logger,
        event_type="rebalance_orders_complete",
        payload={
            "n_orders": len(results),
            "n_full_fills": sum(1 for r in results.values() if r.is_full_fill),
            "n_partial": sum(1 for r in results.values() if r.is_partial),
            "n_unfilled": sum(
                1 for r in results.values()
                if r.filled_qty == 0 and r.submitted_qty > 0
            ),
        },
    )
    return results


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ReconciliationDivergence:
    """One mismatch between persisted state and broker positions."""

    symbol: str
    persisted_qty: float
    broker_qty: float


def find_reconciliation_divergences(
    persisted_holdings: dict[str, float],
    broker_positions: Iterable[tuple[str, float]],
) -> list[ReconciliationDivergence]:
    """Compare persisted holdings to broker positions, tolerance-aware.

    Float qty comparison uses ``QTY_TOLERANCE`` to absorb the small
    decimal-representation drift Alpaca sometimes returns. A difference
    larger than the tolerance is a real divergence — the live driver
    halts on it the same way the stocks bot halts on integer mismatch.
    """
    broker_map = dict(broker_positions)
    diffs: list[ReconciliationDivergence] = []
    all_symbols = set(persisted_holdings) | set(broker_map)
    for sym in sorted(all_symbols):
        p = float(persisted_holdings.get(sym, 0.0))
        b = float(broker_map.get(sym, 0.0))
        if abs(p - b) > QTY_TOLERANCE:
            diffs.append(ReconciliationDivergence(symbol=sym, persisted_qty=p, broker_qty=b))
    return diffs
