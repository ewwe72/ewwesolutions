"""Crypto strategy primitives.

Mirrors the stocks bot's strategy tests, adjusted for crypto parameters
(30-day lookback, no skip, float sizing, weekly rebalance gate).
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.strategy import (
    SymbolMetrics,
    TargetPosition,
    DrawdownState,
    QTY_TOLERANCE,
    build_symbol_metrics,
    compute_adv_dollars,
    compute_momentum_score,
    compute_rebalance_orders,
    equal_weight_targets,
    is_eligible,
    is_rebalance_day,
    select_top_n,
    update_drawdown_state,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _daily_series(values: list[float], *, end: pd.Timestamp = pd.Timestamp("2025-06-30")) -> "pd.Series[float]":
    idx = pd.date_range(end=end, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


# --------------------------------------------------------------------------- #
# compute_momentum_score                                                      #
# --------------------------------------------------------------------------- #

def test_momentum_score_30_day_return_no_skip() -> None:
    """Default lookback=30, skip=0 gives raw 30-day return."""
    prices = [100.0] * 31 + [150.0]  # 32 days, last is +50%
    s = _daily_series(prices)
    score = compute_momentum_score(s, s.index[-1], lookback_days=30, skip_days=0)
    assert score == pytest.approx(0.5)


def test_momentum_score_insufficient_history_returns_nan() -> None:
    s = _daily_series([100.0] * 20)
    assert math.isnan(
        compute_momentum_score(s, s.index[-1], lookback_days=30, skip_days=0)
    )


def test_momentum_score_ignores_bars_after_as_of() -> None:
    """No-lookahead: as_of_date cuts off future bars."""
    # 60 days, all flat at 100 except last 10 which jump to 200.
    # If we ask for momentum as of day 50, the jump shouldn't show.
    prices = [100.0] * 50 + [200.0] * 10
    s = _daily_series(prices, end=pd.Timestamp("2025-06-30"))
    cutoff = s.index[49]
    score = compute_momentum_score(s, cutoff, lookback_days=30, skip_days=0)
    assert score == pytest.approx(0.0)


def test_momentum_score_zero_lookback_price_returns_nan() -> None:
    # Series length 31, lookback=30 means lookback_price = iloc[-31] = iloc[0].
    # Putting the zero at position 0 makes that the lookback price the divisor
    # check rejects.
    prices = [0.0] + [100.0] * 30
    s = _daily_series(prices)
    assert math.isnan(
        compute_momentum_score(s, s.index[-1], lookback_days=30, skip_days=0)
    )


# --------------------------------------------------------------------------- #
# compute_adv_dollars                                                         #
# --------------------------------------------------------------------------- #

def test_adv_basic_calculation() -> None:
    closes = _daily_series([100.0] * 20)
    vols = _daily_series([1000.0] * 20)
    adv = compute_adv_dollars(closes, vols, closes.index[-1], lookback=14)
    assert adv == pytest.approx(100_000.0)


def test_adv_insufficient_history_returns_nan() -> None:
    closes = _daily_series([100.0] * 10)
    vols = _daily_series([1000.0] * 10)
    assert math.isnan(
        compute_adv_dollars(closes, vols, closes.index[-1], lookback=14)
    )


# --------------------------------------------------------------------------- #
# is_eligible                                                                 #
# --------------------------------------------------------------------------- #

def _metric(**overrides: object) -> SymbolMetrics:
    defaults = dict(
        symbol="BTC/USD",
        momentum_score=0.2,
        adv_dollars=100_000_000.0,
        last_price=50000.0,
        days_of_history=60,
    )
    defaults.update(overrides)
    return SymbolMetrics(**defaults)  # type: ignore[arg-type]


def test_is_eligible_default_pair_passes() -> None:
    assert is_eligible(_metric())


def test_is_eligible_rejects_below_min_price() -> None:
    # SHIB-tier sub-cent token (price < 0.01 default)
    assert not is_eligible(_metric(last_price=0.000005))


def test_is_eligible_rejects_illiquid_pair() -> None:
    assert not is_eligible(_metric(adv_dollars=100_000.0))  # below $5M


def test_is_eligible_rejects_short_history() -> None:
    assert not is_eligible(_metric(days_of_history=20))


def test_is_eligible_rejects_nan_score() -> None:
    assert not is_eligible(_metric(momentum_score=float("nan")))


# --------------------------------------------------------------------------- #
# select_top_n                                                                #
# --------------------------------------------------------------------------- #

def test_select_top_n_orders_by_score_desc() -> None:
    metrics = [
        _metric(symbol="LOW", momentum_score=0.1),
        _metric(symbol="HIGH", momentum_score=0.9),
        _metric(symbol="MID", momentum_score=0.5),
    ]
    out = select_top_n(metrics, n=2)
    assert [m.symbol for m in out] == ["HIGH", "MID"]


def test_select_top_n_returns_fewer_if_universe_smaller() -> None:
    """Crypto universe is small (~15 pairs) — top-N may legitimately return < N."""
    metrics = [_metric(symbol="A"), _metric(symbol="B")]
    out = select_top_n(metrics, n=7)
    assert len(out) == 2


# --------------------------------------------------------------------------- #
# equal_weight_targets — fractional sizing                                    #
# --------------------------------------------------------------------------- #

def test_equal_weight_targets_returns_fractional_qty() -> None:
    """No share rounding: target_qty is float."""
    metrics = [
        _metric(symbol="BTC/USD", last_price=50000.0),
        _metric(symbol="ETH/USD", last_price=2500.0),
    ]
    targets = equal_weight_targets(metrics, equity=100_000.0, position_cap_pct=0.18, cash_reserve_pct=0.05)
    by_sym = {t.symbol: t for t in targets}
    # investable = 95k, raw weight per name = 47.5k/100k = 47.5%, capped to 18%
    # so each gets 18% × 100k = 18k notional
    assert by_sym["BTC/USD"].target_qty == pytest.approx(18000.0 / 50000.0)
    assert by_sym["ETH/USD"].target_qty == pytest.approx(18000.0 / 2500.0)
    # Result must not be int-rounded — sanity check that we get the float
    assert isinstance(by_sym["BTC/USD"].target_qty, float)
    assert by_sym["BTC/USD"].target_qty != int(by_sym["BTC/USD"].target_qty)


def test_equal_weight_targets_under_cap() -> None:
    """When universe is large enough that raw weight < cap, raw weight binds."""
    metrics = [_metric(symbol=f"S{i}", last_price=100.0) for i in range(10)]
    targets = equal_weight_targets(metrics, equity=100_000.0, position_cap_pct=0.18, cash_reserve_pct=0.05)
    # 10 names, 5% reserve, raw weight = 95% / 10 = 9.5% per name (< 18% cap)
    for t in targets:
        assert t.target_weight == pytest.approx(0.095)


def test_equal_weight_targets_empty_returns_empty() -> None:
    assert equal_weight_targets([], equity=100_000.0) == []
    assert equal_weight_targets([_metric()], equity=0.0) == []


# --------------------------------------------------------------------------- #
# compute_rebalance_orders — tolerance                                        #
# --------------------------------------------------------------------------- #

def test_rebalance_orders_new_positions() -> None:
    targets = [
        TargetPosition(symbol="BTC/USD", target_weight=0.18, target_qty=0.36),
        TargetPosition(symbol="ETH/USD", target_weight=0.18, target_qty=7.2),
    ]
    orders = compute_rebalance_orders({}, targets)
    assert orders == {"BTC/USD": 0.36, "ETH/USD": 7.2}


def test_rebalance_orders_close_dropped_positions() -> None:
    targets: list[TargetPosition] = []
    orders = compute_rebalance_orders({"OLDCOIN/USD": 5.0}, targets)
    assert orders == {"OLDCOIN/USD": -5.0}


def test_rebalance_orders_tolerance_filters_micro_deltas() -> None:
    """A delta below QTY_TOLERANCE (1e-8) is ignored — prevents noise trades
    from broker-side decimal representation drift."""
    targets = [TargetPosition(symbol="BTC/USD", target_weight=0.1, target_qty=0.1)]
    # Held qty differs from target by less than tolerance
    orders = compute_rebalance_orders({"BTC/USD": 0.1 + QTY_TOLERANCE / 10}, targets)
    assert orders == {}


def test_rebalance_orders_large_enough_delta_passes_tolerance() -> None:
    targets = [TargetPosition(symbol="BTC/USD", target_weight=0.1, target_qty=0.1)]
    orders = compute_rebalance_orders({"BTC/USD": 0.05}, targets)
    assert "BTC/USD" in orders
    assert orders["BTC/USD"] == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# is_rebalance_day — weekly trigger                                           #
# --------------------------------------------------------------------------- #

def test_is_rebalance_day_matches_target_weekday() -> None:
    # 2025-06-02 is a Monday
    assert is_rebalance_day(date(2025, 6, 2), target_weekday=0)


def test_is_rebalance_day_rejects_non_matching_weekday() -> None:
    # 2025-06-03 is Tuesday
    assert not is_rebalance_day(date(2025, 6, 3), target_weekday=0)


def test_is_rebalance_day_blocked_by_min_days_guard() -> None:
    """Last rebal was Monday; today is the next Monday (7 days later) — allowed.
    Last rebal was Tuesday; today is Sunday (5 days later, day-of-week change
    due to DST or operator intervention) — blocked by min-days guard."""
    today = date(2025, 6, 9)   # Monday
    last = date(2025, 6, 2)    # Monday, 7 days ago
    assert is_rebalance_day(today, target_weekday=0, last_rebalance_date=last)

    # Same weekday but only 5 days apart shouldn't normally happen, but
    # if some operator intervention created that scenario the guard should
    # block it.
    last_too_recent = date(2025, 6, 5)  # Thursday
    today_mon = date(2025, 6, 9)        # Monday — only 4 days later
    assert not is_rebalance_day(
        today_mon, target_weekday=0,
        last_rebalance_date=last_too_recent,
        min_days_between=6,
    )


def test_is_rebalance_day_no_prior_rebalance_just_uses_weekday() -> None:
    assert is_rebalance_day(date(2025, 6, 2), target_weekday=0, last_rebalance_date=None)


# --------------------------------------------------------------------------- #
# Drawdown circuit breaker                                                    #
# --------------------------------------------------------------------------- #

def test_drawdown_tracks_peak() -> None:
    state = DrawdownState()
    update_drawdown_state(state, 100000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.peak_equity == 100000.0
    update_drawdown_state(state, 120000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.peak_equity == 120000.0
    # Peak doesn't ratchet down on a drawdown
    update_drawdown_state(state, 90000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.peak_equity == 120000.0


def test_drawdown_trips_at_50_pct_default() -> None:
    state = DrawdownState(peak_equity=100000.0)
    update_drawdown_state(state, 49000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.halt_active is True


def test_drawdown_does_not_trip_above_threshold() -> None:
    """Crypto winters routinely have 40% DDs — they MUST NOT trip the halt
    at the default -50% threshold."""
    state = DrawdownState(peak_equity=100000.0)
    update_drawdown_state(state, 55000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.halt_active is False


def test_drawdown_hysteresis_does_not_resume_too_early() -> None:
    state = DrawdownState(peak_equity=100000.0, halt_active=True)
    # Halfway recovered to -40% — still in halt
    update_drawdown_state(state, 60000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.halt_active is True
    # Recovered to -20% — clear halt
    update_drawdown_state(state, 80000.0, halt_threshold=-0.50, resume_threshold=-0.25)
    assert state.halt_active is False


# --------------------------------------------------------------------------- #
# build_symbol_metrics                                                        #
# --------------------------------------------------------------------------- #

def test_build_symbol_metrics_assembles_all_fields() -> None:
    idx = pd.date_range(end=pd.Timestamp("2025-06-30"), periods=60, freq="D")
    df = pd.DataFrame({
        "open": np.linspace(100, 150, 60),
        "high": np.linspace(105, 155, 60),
        "low": np.linspace(95, 145, 60),
        "close": np.linspace(100, 150, 60),
        "volume": [1_000_000.0] * 60,
    }, index=idx)
    m = build_symbol_metrics("BTC/USD", df, idx[-1], momentum_lookback=30, momentum_skip=0, adv_lookback=14)
    assert m.symbol == "BTC/USD"
    assert m.days_of_history == 60
    assert m.last_price == pytest.approx(150.0)
    assert np.isfinite(m.momentum_score)
    assert np.isfinite(m.adv_dollars)


def test_build_symbol_metrics_empty_bars_returns_nan_metrics() -> None:
    m = build_symbol_metrics("ETH/USD", pd.DataFrame(), pd.Timestamp("2025-06-30"))
    assert math.isnan(m.momentum_score)
    assert math.isnan(m.adv_dollars)
    assert m.days_of_history == 0
