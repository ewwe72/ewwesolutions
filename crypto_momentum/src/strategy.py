"""Cross-sectional momentum strategy for crypto.

Sibling of the stocks bot's strategy module with three substantive
differences:

  1. **Signal lookback.** 30 calendar days, no skip. Crypto momentum
     literature (Liu/Tsyvinski 2021; Hubrich 2022) finds the cleanest
     edge on 1-4 week horizons; the Jegadeesh/Titman 12-1 construction
     used for equities is too slow for crypto.
  2. **Fractional sizing.** Quantities are floats, not ints. Crypto
     trades fractionally on Alpaca (e.g. 0.00342 BTC). The TargetPosition
     and order-diff types carry float qty throughout.
  3. **Rebalance schedule.** Weekly on the configured weekday (default
     Monday). The stocks bot's "first trading day of month" check is
     replaced by a simple day-of-week match; crypto has no holidays
     and no concept of "first trading day."

NO-LOOKAHEAD CONTRACT: identical to the stocks bot. Signal at T uses
bars indexed < T strictly; fills happen at T's open in the backtest
engine; drawdown state uses equity through yesterday's close.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Sequence

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #

class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class SymbolMetrics:
    """Per-symbol snapshot at a rebalance date."""

    symbol: str
    momentum_score: float       # 30-day return, NaN if insufficient history
    adv_dollars: float          # average daily dollar volume over the ADV window
    last_price: float           # most recent close at or before as_of_date
    days_of_history: int


@dataclass(frozen=True)
class TargetPosition:
    """A target slot in the post-rebalance portfolio."""

    symbol: str
    target_weight: float        # fraction of equity (0..1)
    target_qty: float           # fractional crypto units


@dataclass
class DrawdownState:
    """Running peak / halt state for the drawdown circuit-breaker."""

    peak_equity: float = 0.0
    halt_active: bool = False

    def reset(self) -> None:
        self.peak_equity = 0.0
        self.halt_active = False


# --------------------------------------------------------------------------- #
# Signal: N-day momentum                                                      #
# --------------------------------------------------------------------------- #

def compute_momentum_score(
    daily_closes: "pd.Series[float]",
    as_of_date: pd.Timestamp,
    *,
    lookback_days: int = 30,
    skip_days: int = 0,
) -> float:
    """N-day momentum: ``price(t - skip) / price(t - lookback) - 1``.

    With ``skip=0`` (the default for crypto) this is just the trailing
    N-day return. ``skip_days`` is kept as a parameter for symmetry with
    the stocks bot and to allow experimentation, but the crypto literature
    does not motivate a non-zero skip.

    Returns NaN if either price is missing.
    """
    if daily_closes.empty:
        return float("nan")
    available = daily_closes.loc[daily_closes.index <= as_of_date]
    if len(available) < lookback_days + 1:
        return float("nan")
    if len(available) <= skip_days:
        return float("nan")
    skip_price = float(available.iloc[-1 - skip_days])
    lookback_price = float(available.iloc[-1 - lookback_days])
    if lookback_price <= 0 or not np.isfinite(skip_price) or not np.isfinite(lookback_price):
        return float("nan")
    return skip_price / lookback_price - 1.0


# --------------------------------------------------------------------------- #
# Liquidity filter: average dollar volume                                     #
# --------------------------------------------------------------------------- #

def compute_adv_dollars(
    daily_closes: "pd.Series[float]",
    daily_volumes: "pd.Series[float]",
    as_of_date: pd.Timestamp,
    *,
    lookback: int = 14,
) -> float:
    """Average daily dollar volume = mean of close × volume over the window."""
    closes_in = daily_closes.loc[daily_closes.index <= as_of_date]
    vols_in = daily_volumes.loc[daily_volumes.index <= as_of_date]
    if len(closes_in) < lookback or len(vols_in) < lookback:
        return float("nan")
    c = closes_in.iloc[-lookback:].to_numpy(dtype=float)
    v = vols_in.iloc[-lookback:].to_numpy(dtype=float)
    return float(np.nanmean(c * v))


# --------------------------------------------------------------------------- #
# Universe eligibility                                                        #
# --------------------------------------------------------------------------- #

def is_eligible(
    metrics: SymbolMetrics,
    *,
    min_days_history: int = 30,
    min_price: float = 0.01,
    min_adv_dollars: float = 5_000_000.0,
) -> bool:
    """Liquidity / price / history filter for crypto pairs.

    Same shape as the stocks bot's filter, with crypto-appropriate
    thresholds: low min_price (crypto includes sub-dollar tokens like
    SHIB), lower min_history (30 trading days vs 252 for stocks),
    lower ADV threshold ($5M vs $10M because Alpaca's crypto venues
    are thinner than NYSE).
    """
    if not np.isfinite(metrics.momentum_score):
        return False
    if not np.isfinite(metrics.adv_dollars):
        return False
    if metrics.days_of_history < min_days_history:
        return False
    if metrics.last_price < min_price:
        return False
    if metrics.adv_dollars < min_adv_dollars:
        return False
    return True


# --------------------------------------------------------------------------- #
# Selection and portfolio construction                                        #
# --------------------------------------------------------------------------- #

def select_top_n(
    candidates: Sequence[SymbolMetrics],
    n: int,
) -> list[SymbolMetrics]:
    """Sort eligible candidates by momentum_score descending; return top N.

    Ties broken alphabetically for determinism. Returns fewer than N if
    fewer eligible candidates exist (smaller crypto universe makes this
    relatively common — e.g. early-history backtest dates).
    """
    cleaned = [m for m in candidates if np.isfinite(m.momentum_score)]
    cleaned.sort(key=lambda m: (-m.momentum_score, m.symbol))
    return cleaned[:n]


def equal_weight_targets(
    selected: Sequence[SymbolMetrics],
    *,
    equity: float,
    position_cap_pct: float = 0.18,
    cash_reserve_pct: float = 0.05,
) -> list[TargetPosition]:
    """Equal weight across selected names, capped per-name, with a cash reserve.

    Returns fractional ``target_qty`` (float). Unlike the stocks bot's
    ``math.floor(...)``-then-int path, crypto quantities are not rounded —
    Alpaca accepts arbitrary-precision fractional crypto orders.
    """
    if not selected or equity <= 0:
        return []
    investable = equity * (1.0 - cash_reserve_pct)
    raw_weight_per_name = investable / len(selected) / equity
    capped_weight = min(raw_weight_per_name, position_cap_pct)

    out: list[TargetPosition] = []
    for m in selected:
        if m.last_price <= 0:
            continue
        notional = capped_weight * equity
        qty = notional / m.last_price
        if qty <= 0:
            continue
        out.append(TargetPosition(
            symbol=m.symbol,
            target_weight=capped_weight,
            target_qty=qty,
        ))
    return out


# --------------------------------------------------------------------------- #
# Order diffing                                                               #
# --------------------------------------------------------------------------- #

# Crypto position-quantity diffs are taken in a tolerance band rather than
# strict equality: Alpaca occasionally returns slightly different decimal
# representations for the same position across endpoints (e.g. 0.10000000
# vs 0.09999999). A delta within QTY_TOLERANCE is treated as "no order
# needed" rather than triggering a noise trade.
QTY_TOLERANCE: float = 1e-8


def compute_rebalance_orders(
    current_holdings: dict[str, float],
    target_positions: Sequence[TargetPosition],
) -> dict[str, float]:
    """Compute fractional-qty deltas to move from current to target.

    Returns ``{symbol: delta_qty}``. Positive = buy, negative = sell.
    Symbols held but not in target get a full sell. Symbols with absolute
    delta below ``QTY_TOLERANCE`` are omitted (noise filter).
    """
    target_map = {t.symbol: t.target_qty for t in target_positions}

    orders: dict[str, float] = {}
    for sym in set(current_holdings) | set(target_map):
        current_qty = current_holdings.get(sym, 0.0)
        target_qty = target_map.get(sym, 0.0)
        delta = target_qty - current_qty
        if abs(delta) > QTY_TOLERANCE:
            orders[sym] = delta
    return orders


# --------------------------------------------------------------------------- #
# Rebalance schedule                                                          #
# --------------------------------------------------------------------------- #

def is_rebalance_day(
    today: date,
    *,
    target_weekday: int = 0,
    last_rebalance_date: date | None = None,
    min_days_between: int = 6,
) -> bool:
    """True if ``today`` is the configured rebalance weekday.

    ``target_weekday`` follows ``date.weekday()`` semantics: 0=Monday,
    6=Sunday. The ``min_days_between`` guard prevents a Saturday→Sunday
    or Sunday→Monday DST-induced double-rebalance — if the last rebalance
    was less than ``min_days_between`` days ago, today is not a rebalance
    day regardless of weekday.

    No holiday / closure logic: crypto markets are 24/7. The only "skip"
    case is the min-days guard.
    """
    if today.weekday() != target_weekday:
        return False
    if last_rebalance_date is not None:
        if (today - last_rebalance_date) < timedelta(days=min_days_between):
            return False
    return True


# --------------------------------------------------------------------------- #
# Drawdown circuit breaker                                                    #
# --------------------------------------------------------------------------- #

def update_drawdown_state(
    state: DrawdownState,
    current_equity: float,
    *,
    halt_threshold: float = -0.50,
    resume_threshold: float = -0.25,
) -> float:
    """Mutate ``state`` and return the leverage multiplier (0.5 or 1.0).

    Logic mirrors the stocks bot exactly. Defaults are looser
    (-50% / -25% vs the stocks' -35% / -15%) because crypto drawdowns
    are routinely larger than equities'; a tighter halt would live in
    "halt active" mode through every crypto winter.
    """
    if current_equity <= 0:
        return 0.5

    if current_equity > state.peak_equity:
        state.peak_equity = current_equity

    if state.peak_equity <= 0:
        return 1.0

    dd = current_equity / state.peak_equity - 1.0

    if not state.halt_active and dd <= halt_threshold:
        state.halt_active = True
    elif state.halt_active and dd >= resume_threshold:
        state.halt_active = False

    return 0.5 if state.halt_active else 1.0


# --------------------------------------------------------------------------- #
# Convenience: build SymbolMetrics from raw daily bars                        #
# --------------------------------------------------------------------------- #

def build_symbol_metrics(
    symbol: str,
    daily_bars: pd.DataFrame,
    as_of_date: pd.Timestamp,
    *,
    momentum_lookback: int = 30,
    momentum_skip: int = 0,
    adv_lookback: int = 14,
) -> SymbolMetrics:
    """Assemble a SymbolMetrics from a daily-OHLCV DataFrame."""
    if daily_bars.empty:
        return SymbolMetrics(
            symbol=symbol, momentum_score=float("nan"),
            adv_dollars=float("nan"), last_price=float("nan"),
            days_of_history=0,
        )
    closes = daily_bars["close"].astype(float)
    volumes = daily_bars["volume"].astype(float)
    closes_in = closes.loc[closes.index <= as_of_date]
    days = len(closes_in)
    last_price = float(closes_in.iloc[-1]) if days > 0 else float("nan")
    return SymbolMetrics(
        symbol=symbol,
        momentum_score=compute_momentum_score(
            closes, as_of_date,
            lookback_days=momentum_lookback, skip_days=momentum_skip,
        ),
        adv_dollars=compute_adv_dollars(
            closes, volumes, as_of_date, lookback=adv_lookback,
        ),
        last_price=last_price,
        days_of_history=days,
    )
