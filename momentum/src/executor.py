"""Order execution for monthly portfolio rebalancing.

Submits market orders, polls until filled or timeout, records actual fill
prices and quantities. Designed for the cross-sectional momentum strategy:

  1. Submit ALL sell orders first (free up cash).
  2. Wait for all sells to reach a terminal state.
  3. Submit buy orders against the resulting cash balance.
  4. Wait for all buys to reach a terminal state.
  5. Return per-symbol fill records — caller updates persisted state from
     actual fills, never from intended fills.

Partial fills are recorded as-is. No retry. The next monthly rebalance
will re-derive the target portfolio from the actual holdings and reorder
the unfilled delta if it still makes sense.

This executor performs the only network-I/O in the live path. It is
deliberately small and unit-testable via a protocol-typed broker that
the AlpacaClient satisfies.
"""
from __future__ import annotations

import logging
import time as time_mod
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .data import OrderSnapshot
from .logger import log_event


# --------------------------------------------------------------------------- #
# Broker protocol — what executor needs from AlpacaClient (and any test stub) #
# --------------------------------------------------------------------------- #

class Broker(Protocol):
    """The slice of ``AlpacaClient`` the executor uses.

    Defined as a Protocol so tests can pass a lightweight in-memory stub
    without instantiating Alpaca's SDK or hitting the network.
    """

    def submit_market_order(self, *, symbol: str, qty: int, side: str) -> str: ...
    def get_order(self, order_id: str) -> OrderSnapshot: ...


# --------------------------------------------------------------------------- #
# Records                                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FillResult:
    """Outcome of one symbol's order leg."""

    symbol: str
    side: str                   # "buy" or "sell"
    submitted_qty: int          # qty we asked for
    filled_qty: int             # qty actually filled
    avg_fill_price: float       # broker-reported average (0.0 if unfilled)
    order_id: str
    final_status: str           # last-known order status string

    @property
    def is_full_fill(self) -> bool:
        return self.filled_qty == self.submitted_qty and self.submitted_qty > 0

    @property
    def is_partial(self) -> bool:
        return 0 < self.filled_qty < self.submitted_qty


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
    """Poll until the order is in a terminal state or ``timeout_seconds`` passes.

    On timeout, returns the last snapshot we saw — the caller decides how to
    interpret a still-pending order (currently: record as a non-fill, no retry).
    The ``sleep`` arg is injected so tests can run instantly without real sleeps.
    """
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
    qty: int,
    side: str,
    fill_timeout_seconds: float,
    poll_interval: float,
    logger: logging.Logger,
    sleep: Callable[[float], None] | None = None,
) -> FillResult:
    """Submit one market order, wait for terminal state, return the result."""
    try:
        order_id = broker.submit_market_order(symbol=symbol, qty=qty, side=side)
    except Exception as exc:  # noqa: BLE001 — broker errors are diverse
        log_event(
            logger,
            event_type="order_submit_failed",
            symbol=symbol,
            payload={"qty": qty, "side": side, "error": str(exc)},
            level=logging.ERROR,
        )
        return FillResult(
            symbol=symbol, side=side, submitted_qty=qty,
            filled_qty=0, avg_fill_price=0.0,
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
# Public entry point: execute one rebalance's orders                          #
# --------------------------------------------------------------------------- #

def execute_rebalance_orders(
    broker: Broker,
    orders: dict[str, int],
    *,
    logger: logging.Logger,
    fill_timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    inter_leg_sleep: float = 0.5,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, FillResult]:
    """Execute a rebalance's worth of orders, sells first then buys.

    ``orders`` is the dict produced by ``strategy.compute_rebalance_orders``:
    positive deltas = buys, negative deltas = sells. Symbols absent from the
    dict are skipped.

    The two-phase ordering (sells, wait, buys) matters: a single-day rebalance
    needs the proceeds from sells to fund the buys, and Alpaca's settled-cash
    behaviour on paper is forgiving but not infinite.

    Returns a per-symbol ``FillResult``. Partial fills are recorded as-is;
    the caller's job is to update persisted holdings from these actual fills,
    not from the intended deltas.
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
    """One mismatch between persisted state and broker positions.

    ``persisted_qty`` and ``broker_qty`` are the bot's view and Alpaca's view
    respectively. Either can be zero (i.e. one side missing entirely).
    """

    symbol: str
    persisted_qty: int
    broker_qty: int


def find_reconciliation_divergences(
    persisted_holdings: dict[str, int],
    broker_positions: Iterable[tuple[str, int]],
) -> list[ReconciliationDivergence]:
    """Compare persisted holdings to the broker's authoritative position list.

    Returns a list of divergences. An empty list means the books match.
    No mutation; no auto-correction. The caller (live.py) halts and exits
    on any divergence, because silent reconciliation is how bots go rogue:
    a missed fill becomes a hidden short, or vice versa.

    ``persisted_holdings`` maps symbol -> signed quantity (long positive).
    ``broker_positions`` is an iterable of (symbol, qty) pairs.
    """
    broker_map = dict(broker_positions)
    diffs: list[ReconciliationDivergence] = []
    all_symbols = set(persisted_holdings) | set(broker_map)
    for sym in sorted(all_symbols):
        p = int(persisted_holdings.get(sym, 0))
        b = int(broker_map.get(sym, 0))
        if p != b:
            diffs.append(ReconciliationDivergence(symbol=sym, persisted_qty=p, broker_qty=b))
    return diffs
