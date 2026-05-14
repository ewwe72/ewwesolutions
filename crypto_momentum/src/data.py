"""Alpaca crypto data + account wrappers.

Same shape as the stocks bot's data.py but built on
``CryptoHistoricalDataClient`` and with float-typed quantities. Crypto
orders are GTC market orders (DAY makes no sense on a 24/7 venue), and
fractional sizing is native — qty is a float at every layer.

Paper/live distinction is enforced the same way as the stocks bot:
``AlpacaClient`` refuses ``mode='live'`` unless ``allow_live=True``, and
no CLI path exposes that. This is the *only* safety property preventing
a misconfigured environment from sending real-money orders.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, cast

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import MarketOrderRequest


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float
    pattern_day_trader: bool   # always False for crypto-only accounts; kept for parity


@dataclass(frozen=True)
class PositionSnapshot:
    """One open position.

    ``qty`` is float because crypto is fractionally divisible.
    ``asset_class`` is exposed so the live driver can filter out any
    non-crypto leftovers in the account (defensive — this paper account
    is dedicated to crypto, but a robust reconcile should still slice
    by class).
    """

    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    asset_class: str            # "crypto", "us_equity", etc.


@dataclass(frozen=True)
class OrderSnapshot:
    """Minimal order state used by the executor for fill polling."""

    id: str
    symbol: str
    status: str
    filled_qty: float           # float for crypto
    filled_avg_price: float

    @property
    def is_terminal(self) -> bool:
        return self.status in {"filled", "rejected", "canceled", "expired", "done_for_day"}


class AlpacaClient:
    """Trading + crypto-bars wrapper.

    Reads credentials from environment. ``allow_live=True`` is the only
    way to point this at the live URL; no CLI flag exposes it.
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
        # CryptoHistoricalDataClient does NOT require credentials for
        # public bar data, but accepts them. Pass them for consistency
        # and for higher rate limits.
        self.data = CryptoHistoricalDataClient(api_key=key, secret_key=secret)

    # ------- account ------- #
    def account(self) -> AccountSnapshot:
        a = self.trading.get_account()
        assert isinstance(a, TradeAccount), f"Unexpected account type: {type(a)}"
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
        """All positions (any asset class). Caller filters by ``asset_class``."""
        out: list[PositionSnapshot] = []
        for p in self.trading.get_all_positions():
            assert isinstance(p, Position), f"Unexpected position type: {type(p)}"
            out.append(
                PositionSnapshot(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    market_value=float(p.market_value) if p.market_value else 0.0,
                    unrealized_pl=float(p.unrealized_pl) if p.unrealized_pl else 0.0,
                    asset_class=str(p.asset_class) if p.asset_class else "unknown",
                )
            )
        return out

    # ------- bars ------- #
    def daily_bars_multi(
        self,
        symbols: Iterable[str],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        """Daily OHLCV for many crypto pairs over [start, end].

        Returns a dict keyed by Alpaca symbol (``BTC/USD`` etc.). Symbols
        with no data over the window get an empty DataFrame. The 24/7
        nature of crypto means every calendar day should have a bar; a
        missing bar is a feed problem, not a holiday.
        """
        sym_list = list(symbols)
        if not sym_list:
            return {}

        req = CryptoBarsRequest(
            symbol_or_symbols=sym_list,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        result = self.data.get_crypto_bars(req)
        assert isinstance(result, BarSet), f"Unexpected bars response type: {type(result)}"
        df = result.df
        out: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in sym_list}
        if df.empty:
            return out
        # CryptoBarsRequest returns a (symbol, timestamp) multi-index frame.
        if isinstance(df.index, pd.MultiIndex):
            for sym in sym_list:
                if sym in df.index.get_level_values(0):
                    sub = cast(pd.DataFrame, df.xs(sym, level=0)).copy()
                    assert isinstance(sub.index, pd.DatetimeIndex)
                    # Normalise to date (drop hours) and ensure tz-naive
                    # for downstream pandas-comparison consistency.
                    sub.index = sub.index.tz_convert("UTC").normalize().tz_localize(None)
                    out[sym] = sub
        return out

    # ------- orders ------- #
    def submit_market_order(self, *, symbol: str, qty: float, side: str) -> str:
        """Submit a market order for ``qty`` units of ``symbol``.

        Crypto orders use GTC TimeInForce — DAY is meaningless on a 24/7
        venue. Alpaca accepts ``qty`` as a string of arbitrary precision;
        we format float qty to 8 decimal places (more than enough for
        any pair on the venue).
        """
        alpaca_side = AlpacaOrderSide.BUY if side == "buy" else AlpacaOrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=str(round(qty, 8)),
            side=alpaca_side,
            time_in_force=TimeInForce.GTC,
        )
        order = self.trading.submit_order(order_data=req)
        assert isinstance(order, Order), f"Unexpected order response type: {type(order)}"
        return str(order.id)

    def get_order(self, order_id: str) -> OrderSnapshot:
        """Fetch one order's current state."""
        order = self.trading.get_order_by_id(order_id)
        assert isinstance(order, Order), f"Unexpected order type: {type(order)}"
        return OrderSnapshot(
            id=str(order.id),
            symbol=order.symbol,
            status=str(order.status) if order.status else "unknown",
            filled_qty=float(order.filled_qty) if order.filled_qty else 0.0,
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else 0.0,
        )
