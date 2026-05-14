"""Cross-sectional momentum strategy.

Implements long-only 12-1 momentum on US equities, following the canonical
Jegadeesh & Titman (1993) construction with the standard 1-month skip to
strip out short-term reversal contamination.

PRIMITIVES this module provides:
  - 12-1 momentum score: price(t-skip) / price(t-lookback) - 1
  - Average dollar volume (ADV) for liquidity filtering
  - Universe eligibility check (history, price, liquidity)
  - Top-N selection by score with NaN handling
  - Equal-weight portfolio construction with per-name cap
  - Optional sector cap layering (caller supplies sector mapping)
  - Rebalance schedule (first trading day of month by default)
  - Drawdown halt with hysteresis (trip at -25%, resume at -10%)
  - Order diffing: current holdings vs target -> delta shares

NO-LOOKAHEAD CONTRACT:
  - All "as_of_date" functions use prices strictly through that date and no
    further. The 12-1 momentum signal references prices through t-skip,
    not through t.
  - Liquidity filter (ADV) uses days strictly before the rebalance date.
  - Order generation produces deltas; engine fills them at the NEXT bar's
    open. Engine never uses today's close to fill today's rebalance.

The module is framework-agnostic: pure functions on input DataFrames and
small immutable state objects. The engine (backtest or live) drives them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #

class Side(str, Enum):
    """Position side. Long-only initially; included as enum for future short leg."""

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class SymbolMetrics:
    """Per-symbol snapshot at a rebalance date."""

    symbol: str
    momentum_score: float       # 12-1 momentum, NaN if insufficient history
    adv_dollars: float          # average daily dollar volume over the ADV window
    last_price: float           # most recent close at or before as_of_date
    days_of_history: int        # number of daily closes available


@dataclass(frozen=True)
class TargetPosition:
    """A target slot in the post-rebalance portfolio."""

    symbol: str
    target_weight: float        # fraction of equity (0..1)
    target_shares: int          # integer share count (whole shares only)


@dataclass
class DrawdownState:
    """Running peak / halt state for the annual drawdown circuit-breaker.

    Mutable by design — the engine updates this in place on each rebalance.
    """

    peak_equity: float = 0.0
    halt_active: bool = False

    def reset(self) -> None:
        self.peak_equity = 0.0
        self.halt_active = False


# --------------------------------------------------------------------------- #
# Signal: 12-1 momentum                                                       #
# --------------------------------------------------------------------------- #

def compute_momentum_score(
    daily_closes: "pd.Series[float]",
    as_of_date: pd.Timestamp,
    *,
    lookback_days: int = 252,
    skip_days: int = 21,
) -> float:
    """12-1 momentum: ``price(t - skip) / price(t - lookback) - 1``.

    ``daily_closes`` is a date-indexed close series, sorted ascending.
    Returns ``NaN`` if either the lookback price or the skip price is missing.

    Important: the signal explicitly avoids reading prices in the window
    ``(t - skip, t]``. This is the standard short-term-reversal exclusion.
    The most recent 21 trading days are ignored to prevent the "winner that
    just popped" from contaminating "winner over the past year."
    """
    if daily_closes.empty:
        return float("nan")
    available = daily_closes.loc[daily_closes.index <= as_of_date]
    if len(available) < lookback_days + 1:
        return float("nan")
    # price(t - skip) — index is 1 (skip_days=21 -> position -22 from the end if 0-indexed,
    # but using positional .iloc is clearer than date arithmetic with non-trading days)
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
    lookback: int = 60,
) -> float:
    """Average daily dollar volume = mean of close × volume over the window.

    Uses only days at or before ``as_of_date``. Returns NaN if insufficient
    history.
    """
    closes_in = daily_closes.loc[daily_closes.index <= as_of_date]
    vols_in = daily_volumes.loc[daily_volumes.index <= as_of_date]
    if len(closes_in) < lookback or len(vols_in) < lookback:
        return float("nan")
    # Align: take the last `lookback` rows from each
    c = closes_in.iloc[-lookback:].to_numpy(dtype=float)
    v = vols_in.iloc[-lookback:].to_numpy(dtype=float)
    return float(np.nanmean(c * v))


# --------------------------------------------------------------------------- #
# Universe eligibility                                                        #
# --------------------------------------------------------------------------- #

def is_eligible(
    metrics: SymbolMetrics,
    *,
    min_days_history: int = 252,
    min_price: float = 5.0,
    min_adv_dollars: float = 10_000_000.0,
) -> bool:
    """Combined liquidity / price / history filter.

    Rejects:
      - Penny stocks (last_price < min_price)
      - Illiquid names (ADV < min_adv_dollars)
      - Short-history names (not enough days for momentum signal)
      - Names with NaN scores or NaN ADV
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

    Ties are broken by symbol (alphabetical, ascending) for determinism.
    NaN scores are filtered out (defensive — ``is_eligible`` should already
    have filtered them). Returns fewer than N if the candidate set is small.
    """
    cleaned = [m for m in candidates if np.isfinite(m.momentum_score)]
    cleaned.sort(key=lambda m: (-m.momentum_score, m.symbol))
    return cleaned[:n]


def equal_weight_targets(
    selected: Sequence[SymbolMetrics],
    *,
    equity: float,
    position_cap_pct: float = 0.03,
    cash_reserve_pct: float = 0.02,
) -> list[TargetPosition]:
    """Equal weight across selected names, capped per-name, with a cash reserve.

    investable = equity × (1 - cash_reserve_pct)
    raw_weight = investable / N
    final_weight = min(raw_weight, position_cap_pct × equity)
    target_shares = floor(final_weight × equity / last_price)

    The cash reserve protects against partial fills causing margin trips and
    leaves room for small price drift between signal time and fill time.
    """
    if not selected or equity <= 0:
        return []
    investable = equity * (1.0 - cash_reserve_pct)
    raw_weight_per_name = investable / len(selected) / equity  # back to weight fraction
    capped_weight = min(raw_weight_per_name, position_cap_pct)

    out: list[TargetPosition] = []
    for m in selected:
        if m.last_price <= 0:
            continue
        notional = capped_weight * equity
        shares = max(0, math.floor(notional / m.last_price))
        if shares == 0:
            continue
        out.append(TargetPosition(
            symbol=m.symbol,
            target_weight=capped_weight,
            target_shares=shares,
        ))
    return out


def apply_sector_cap(
    targets: Sequence[TargetPosition],
    sector_of: Mapping[str, str],
    *,
    max_sector_weight: float = 0.25,
) -> list[TargetPosition]:
    """Scale positions down within any sector that exceeds the cap.

    Implementation: for each sector, sum the target weights of all positions
    in that sector. If sum > max, scale every position in that sector by
    (max / sum). Excess weight goes to cash (not redistributed to other
    sectors — that would require iterative rebalancing and complicate the
    math without much benefit).

    Names not in ``sector_of`` are treated as their own "unknown" sector and
    capped individually only by the per-name cap from ``equal_weight_targets``.
    """
    if not targets:
        return []

    by_sector: dict[str, list[TargetPosition]] = {}
    for t in targets:
        sector = sector_of.get(t.symbol, "_unknown")
        by_sector.setdefault(sector, []).append(t)

    out: list[TargetPosition] = []
    for sector, positions in by_sector.items():
        total = sum(p.target_weight for p in positions)
        if total <= max_sector_weight:
            out.extend(positions)
            continue
        scale = max_sector_weight / total
        for p in positions:
            new_weight = p.target_weight * scale
            new_shares = max(0, int(p.target_shares * scale))
            if new_shares == 0:
                continue
            out.append(TargetPosition(
                symbol=p.symbol,
                target_weight=new_weight,
                target_shares=new_shares,
            ))
    return out


# --------------------------------------------------------------------------- #
# Order diffing                                                               #
# --------------------------------------------------------------------------- #

def compute_rebalance_orders(
    current_holdings: Mapping[str, int],
    target_positions: Sequence[TargetPosition],
) -> dict[str, int]:
    """Compute share-delta orders to move from current to target.

    Returns a dict ``{symbol: delta_shares}``. Positive deltas are buys,
    negative deltas are sells. Symbols held but not in target get a full
    sell (delta = -current_shares). Symbols in target but not held get a
    full buy (delta = +target_shares). Symbols with delta == 0 are omitted.
    """
    target_map = {t.symbol: t.target_shares for t in target_positions}

    orders: dict[str, int] = {}
    for sym in set(current_holdings) | set(target_map):
        current_qty = current_holdings.get(sym, 0)
        target_qty = target_map.get(sym, 0)
        delta = target_qty - current_qty
        if delta != 0:
            orders[sym] = delta
    return orders


# --------------------------------------------------------------------------- #
# Rebalance schedule                                                          #
# --------------------------------------------------------------------------- #

def is_first_trading_day_of_month(
    today: pd.Timestamp,
    *,
    trading_calendar: "pd.DatetimeIndex | None" = None,
) -> bool:
    """True if ``today`` is the first trading day of its month.

    If ``trading_calendar`` is provided, uses it directly: today is the first
    if there is no earlier date in the same year-month within the calendar.
    Otherwise falls back to a weekday check — Monday through Friday, with
    today being the earliest weekday of its month. That fallback ignores
    market holidays so the calendar version is preferred when available.
    """
    if trading_calendar is not None:
        same_month = trading_calendar[
            (trading_calendar.year == today.year)
            & (trading_calendar.month == today.month)
        ]
        if len(same_month) == 0:
            return False
        return bool(same_month[0].date() == today.date())

    # Fallback: assume Mon-Fri = trading day
    if today.weekday() >= 5:
        return False
    # Walk back through weekdays-of-this-month and check if any are earlier
    for d in range(1, today.day):
        prior = today.replace(day=d)
        if prior.weekday() < 5:
            return False
    return True


# --------------------------------------------------------------------------- #
# Drawdown circuit breaker                                                    #
# --------------------------------------------------------------------------- #

def update_drawdown_state(
    state: DrawdownState,
    current_equity: float,
    *,
    halt_threshold: float = -0.25,
    resume_threshold: float = -0.10,
) -> float:
    """Mutate ``state`` and return the leverage multiplier (0.5 or 1.0).

    Logic:
      - Track the all-time peak equity.
      - Compute current drawdown vs peak.
      - If not yet halted and DD <= halt_threshold → set halt_active = True.
      - If halted and DD >= resume_threshold → clear halt_active.
      - When halt_active, sizing should target 50% of normal (multiplier 0.5).
      - Hysteresis: the halt resumes at a *less negative* threshold than it
        trips, to prevent flip-flopping near the cliff.

    Thresholds are negative numbers (e.g. -0.25 means -25%).
    """
    if current_equity <= 0:
        return 0.5  # degenerate; be conservative

    if current_equity > state.peak_equity:
        state.peak_equity = current_equity

    if state.peak_equity <= 0:
        return 1.0

    dd = current_equity / state.peak_equity - 1.0  # negative if underwater

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
    momentum_lookback: int = 252,
    momentum_skip: int = 21,
    adv_lookback: int = 60,
) -> SymbolMetrics:
    """Assemble a SymbolMetrics from a daily-OHLCV DataFrame.

    ``daily_bars`` is date-indexed (ascending) with at least ``close`` and
    ``volume`` columns. Caller's contract: bars indexed after ``as_of_date``
    are tolerated but ignored — only data through that date contributes.
    """
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
            closes, as_of_date, lookback_days=momentum_lookback, skip_days=momentum_skip,
        ),
        adv_dollars=compute_adv_dollars(
            closes, volumes, as_of_date, lookback=adv_lookback,
        ),
        last_price=last_price,
        days_of_history=days,
    )
