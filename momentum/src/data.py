"""Alpaca data + account wrappers.

Thin, typed shims around ``alpaca-py``. The rest of the system depends only on
``AlpacaClient`` so the underlying SDK could be swapped without touching
strategy or risk code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, cast

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest

from .utils.time import ET


@dataclass(frozen=True)
class AccountSnapshot:
    """Just the fields the bot actually uses."""

    equity: float
    cash: float
    buying_power: float
    pattern_day_trader: bool


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: int                # signed: positive = long, negative = short
    avg_entry_price: float
    market_value: float
    unrealized_pl: float


@dataclass(frozen=True)
class OrderSnapshot:
    """Minimal order state used by the live executor for fill polling."""

    id: str
    symbol: str
    status: str             # "new" | "accepted" | "filled" | "partially_filled" | "rejected" | "canceled" | ...
    filled_qty: int
    filled_avg_price: float

    @property
    def is_terminal(self) -> bool:
        """True when the order will not change further (filled/rejected/canceled/expired)."""
        return self.status in {"filled", "rejected", "canceled", "expired", "done_for_day"}


class AlpacaClient:
    """Composed trading + historical-data client.

    Reads credentials from environment. The paper/live distinction is purely a
    base-URL flip — ``mode='paper'`` is enforced unless ``allow_live=True``.
    """

    def __init__(self, *, mode: str = "paper", allow_live: bool = False) -> None:
        key = os.environ["APCA_API_KEY_ID"]
        secret = os.environ["APCA_API_SECRET_KEY"]
        base_url = os.environ["APCA_API_BASE_URL"]

        is_paper = "paper" in base_url.lower()
        if mode == "live" and not allow_live:
            raise RuntimeError(
                "Live mode requires explicit allow_live=True. Live trading is "
                "out of scope for this build per spec."
            )
        if mode == "paper" and not is_paper:
            raise RuntimeError(f"Mode 'paper' but APCA_API_BASE_URL={base_url!r}")

        self.trading = TradingClient(api_key=key, secret_key=secret, paper=is_paper)
        self.data = StockHistoricalDataClient(api_key=key, secret_key=secret)

    # ------- account ------- #
    def account(self) -> AccountSnapshot:
        a = self.trading.get_account()
        assert isinstance(a, TradeAccount), f"Unexpected account type: {type(a)}"
        # alpaca-py returns these as Optional[str]; runtime asserts catch nulls early.
        assert a.equity is not None
        assert a.cash is not None
        assert a.buying_power is not None
        return AccountSnapshot(
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            pattern_day_trader=bool(a.pattern_day_trader),
        )

    def positions(self) -> list[PositionSnapshot]:
        out: list[PositionSnapshot] = []
        for p in self.trading.get_all_positions():
            assert isinstance(p, Position), f"Unexpected position type: {type(p)}"
            out.append(
                PositionSnapshot(
                    symbol=p.symbol,
                    qty=int(float(p.qty)),
                    avg_entry_price=float(p.avg_entry_price),
                    market_value=float(p.market_value) if p.market_value else 0.0,
                    unrealized_pl=float(p.unrealized_pl) if p.unrealized_pl else 0.0,
                )
            )
        return out

    # ------- bars ------- #
    def daily_bars(self, symbol: str, *, lookback: int = 250) -> pd.DataFrame:
        """Last ``lookback`` daily bars, indexed by date (ET). Ascending."""
        end = datetime.now(tz=ET)
        start = end - timedelta(days=int(lookback * 1.5) + 30)  # cushion for weekends/holidays
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        result = self.data.get_stock_bars(req)
        assert isinstance(result, BarSet), f"Unexpected bars response type: {type(result)}"
        bars = result.df
        if bars.empty:
            return bars
        if isinstance(bars.index, pd.MultiIndex):
            bars = cast(pd.DataFrame, bars.xs(symbol, level=0))
        # alpaca-py returns UTC timestamps; convert to ET for date alignment.
        assert isinstance(bars.index, pd.DatetimeIndex)
        bars.index = bars.index.tz_convert(ET).normalize()
        return bars.tail(lookback)

    def daily_bars_multi(
        self,
        symbols: Iterable[str],
        *,
        start: datetime,
        end: datetime,
        batch_size: int = 50,
    ) -> dict[str, pd.DataFrame]:
        """Daily OHLCV for many symbols, chunked into batched requests.

        Returns a dict keyed by symbol. Symbols with no data over the window
        (delisted, IPO too recent) get an empty DataFrame.

        Why chunking: Alpaca's multi-symbol response is a single MultiIndex
        DataFrame. Requesting 500 symbols × 14 years in one shot occasionally
        times out or hits server-side row limits. 50/batch is a safe default.
        """
        symbols_list = list(symbols)
        out: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in symbols_list}

        for i in range(0, len(symbols_list), batch_size):
            batch = symbols_list[i:i + batch_size]
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
            result = self.data.get_stock_bars(req)
            assert isinstance(result, BarSet), f"Unexpected bars response: {type(result)}"
            df = result.df
            if df.empty:
                continue
            assert isinstance(df.index, pd.MultiIndex)
            # Split the MultiIndex DataFrame into per-symbol frames
            for sym in batch:
                if sym not in df.index.get_level_values(0):
                    continue
                sub = cast(pd.DataFrame, df.xs(sym, level=0))
                assert isinstance(sub.index, pd.DatetimeIndex)
                sub.index = sub.index.tz_convert(ET).normalize()
                out[sym] = sub
        return out

    def intraday_bars(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
        minutes: int = 5,
    ) -> pd.DataFrame:
        """5-min bars (or other minute granularity), indexed in ET."""
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(amount=minutes, unit=TimeFrameUnit.Minute),
            start=start,
            end=end,
        )
        result = self.data.get_stock_bars(req)
        assert isinstance(result, BarSet), f"Unexpected bars response type: {type(result)}"
        bars = result.df
        if bars.empty:
            return bars
        if isinstance(bars.index, pd.MultiIndex):
            bars = cast(pd.DataFrame, bars.xs(symbol, level=0))
        assert isinstance(bars.index, pd.DatetimeIndex)
        bars.index = bars.index.tz_convert(ET)
        return bars

    # ------- orders ------- #
    def submit_market_order(
        self,
        *,
        symbol: str,
        qty: int,
        side: str,
    ) -> str:
        """Submit a plain market order, returning the broker order ID.

        Used by the monthly-rebalance executor where stops are not part of
        the trade (exits happen at the NEXT rebalance, not on price triggers).
        ``side`` is "buy" or "sell".
        """
        alp_side = AlpacaOrderSide.BUY if side == "buy" else AlpacaOrderSide.SELL
        order = self.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol, qty=qty, side=alp_side, time_in_force=TimeInForce.DAY
            )
        )
        assert isinstance(order, Order), f"Unexpected order type: {type(order)}"
        return str(order.id)

    def get_order(self, order_id: str) -> "OrderSnapshot":
        """Fetch current state of one order. Used to poll for fills."""
        o = self.trading.get_order_by_id(order_id)
        assert isinstance(o, Order), f"Unexpected order type: {type(o)}"
        return OrderSnapshot(
            id=str(o.id),
            symbol=str(o.symbol),
            status=str(o.status.value) if hasattr(o.status, "value") else str(o.status),
            filled_qty=int(float(o.filled_qty)) if o.filled_qty else 0,
            filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else 0.0,
        )

    def submit_market_with_stop(
        self,
        *,
        symbol: str,
        qty: int,
        side: str,
        stop_price: float,
    ) -> tuple[str, str]:
        """Submit a market entry plus a stop loss. Returns (entry_id, stop_id).

        We don't use a take-profit leg because the spec specifies EOD exit
        rather than a fixed R-multiple target. The EOD exit is submitted
        separately at 15:55 ET by the live loop.
        """
        alp_side = AlpacaOrderSide.BUY if side == "long" else AlpacaOrderSide.SELL
        stop_side = AlpacaOrderSide.SELL if side == "long" else AlpacaOrderSide.BUY

        entry = self.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol, qty=qty, side=alp_side, time_in_force=TimeInForce.DAY
            )
        )
        stop = self.trading.submit_order(
            StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=stop_side,
                stop_price=round(stop_price, 2),
                time_in_force=TimeInForce.DAY,
            )
        )
        assert isinstance(entry, Order), f"Unexpected order type: {type(entry)}"
        assert isinstance(stop, Order), f"Unexpected order type: {type(stop)}"
        return str(entry.id), str(stop.id)

    def replace_stop(self, *, order_id: str, new_stop: float) -> str:
        """Cancel + resubmit the stop. Alpaca doesn't fully support order
        replacement on stop orders, so cancel/replace is the safe pattern."""
        # In practice the executor cancels then submits a new stop with the same
        # qty/side. Kept here as a stub so the executor can mock against it.
        raise NotImplementedError(
            "Cancel-then-resubmit pattern is implemented in executor.py; "
            "this method exists to mark the integration point."
        )

    def cancel_order(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)

    def close_position(self, symbol: str) -> None:
        self.trading.close_position(symbol)

    def close_all_positions(self) -> None:
        self.trading.close_all_positions(cancel_orders=True)

    def open_orders(self, symbols: Iterable[str] | None = None) -> list[dict[str, str]]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=list(symbols) if symbols else None,
        )
        out: list[dict[str, str]] = []
        for o in self.trading.get_orders(filter=req):
            assert isinstance(o, Order), f"Unexpected order type: {type(o)}"
            out.append({
                "id": str(o.id),
                "symbol": str(o.symbol),
                "side": str(o.side),
                "qty": str(o.qty),
                "order_type": str(o.order_type),
            })
        return out
