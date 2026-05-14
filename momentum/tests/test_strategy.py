"""Unit tests for the cross-sectional momentum primitives."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.strategy import (
    DrawdownState,
    Side,
    SymbolMetrics,
    TargetPosition,
    apply_sector_cap,
    build_symbol_metrics,
    compute_adv_dollars,
    compute_momentum_score,
    compute_rebalance_orders,
    equal_weight_targets,
    is_eligible,
    is_first_trading_day_of_month,
    select_top_n,
    update_drawdown_state,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _daily(start: str, n: int, prices: list[float] | None = None) -> "pd.Series[float]":
    """Date-indexed Series of closes, ascending business days from ``start``."""
    idx = pd.bdate_range(start=start, periods=n)
    if prices is None:
        prices = list(range(100, 100 + n))
    return pd.Series([float(p) for p in prices], index=idx, name="close")


def _metrics(
    symbol: str = "SPY",
    *,
    momentum_score: float = 0.10,
    adv_dollars: float = 1e9,
    last_price: float = 100.0,
    days_of_history: int = 300,
) -> SymbolMetrics:
    return SymbolMetrics(
        symbol=symbol,
        momentum_score=momentum_score,
        adv_dollars=adv_dollars,
        last_price=last_price,
        days_of_history=days_of_history,
    )


# --------------------------------------------------------------------------- #
# Momentum score                                                              #
# --------------------------------------------------------------------------- #

def test_momentum_score_basic_calculation() -> None:
    # 252 days of data, geometric drift of 1% per day until t-21, flat after.
    # 12-1 momentum = price(t-21) / price(t-252) - 1
    closes = _daily("2023-01-02", n=300, prices=[100.0 * (1.001 ** i) for i in range(300)])
    as_of = closes.index[-1]
    # price(t-21) is closes.iloc[-22], price(t-252) is closes.iloc[-253]
    expected = float(closes.iloc[-22] / closes.iloc[-253] - 1)
    score = compute_momentum_score(closes, as_of, lookback_days=252, skip_days=21)
    assert score == pytest.approx(expected, rel=1e-9)


def test_momentum_score_insufficient_history_returns_nan() -> None:
    closes = _daily("2023-01-02", n=200)
    score = compute_momentum_score(closes, closes.index[-1], lookback_days=252, skip_days=21)
    assert np.isnan(score)


def test_momentum_score_empty_series_returns_nan() -> None:
    closes = pd.Series([], dtype=float, index=pd.DatetimeIndex([]))
    assert np.isnan(compute_momentum_score(closes, pd.Timestamp("2024-01-02")))


def test_momentum_score_flat_prices_returns_zero() -> None:
    closes = _daily("2023-01-02", n=300, prices=[100.0] * 300)
    score = compute_momentum_score(closes, closes.index[-1])
    assert score == pytest.approx(0.0)


def test_momentum_score_ignores_future_bars() -> None:
    """No-lookahead: data after as_of_date must not affect the score."""
    closes_a = _daily("2023-01-02", n=300, prices=[100.0 * (1.001 ** i) for i in range(300)])
    as_of = closes_a.index[260]  # cut off well before the end

    # Same data, but with future bars wildly different — same score expected
    prices_b = list(closes_a.iloc[:261].values) + [1e6] * (len(closes_a) - 261)
    closes_b = pd.Series(prices_b, index=closes_a.index)

    score_a = compute_momentum_score(closes_a.iloc[:261], as_of)
    score_b = compute_momentum_score(closes_b, as_of)
    assert score_a == pytest.approx(score_b, rel=1e-12)


def test_momentum_score_respects_custom_skip_and_lookback() -> None:
    closes = _daily("2023-01-02", n=300, prices=[100.0 + i for i in range(300)])
    as_of = closes.index[-1]
    # Use lookback=100, skip=10. Skip price is iloc[-11]; lookback price is iloc[-101].
    score = compute_momentum_score(closes, as_of, lookback_days=100, skip_days=10)
    expected = float(closes.iloc[-11] / closes.iloc[-101] - 1)
    assert score == pytest.approx(expected)


def test_momentum_score_nonpositive_lookback_price_returns_nan() -> None:
    # Construct a 20-bar series where the lookback price is exactly 0.
    # With lookback_days=10, the lookback target is iloc[-11] of a 20-bar
    # series = index 9. Put zero there.
    prices = [10.0] * 20
    prices[9] = 0.0
    closes = _daily("2023-01-02", n=20, prices=prices)
    score = compute_momentum_score(closes, closes.index[-1], lookback_days=10, skip_days=2)
    assert np.isnan(score)


# --------------------------------------------------------------------------- #
# ADV                                                                         #
# --------------------------------------------------------------------------- #

def test_adv_basic_calculation() -> None:
    closes = _daily("2023-01-02", n=100, prices=[100.0] * 100)
    volumes = pd.Series([1_000_000.0] * 100, index=closes.index)
    adv = compute_adv_dollars(closes, volumes, closes.index[-1], lookback=60)
    assert adv == pytest.approx(100.0 * 1_000_000.0)


def test_adv_insufficient_history_returns_nan() -> None:
    closes = _daily("2023-01-02", n=30, prices=[100.0] * 30)
    volumes = pd.Series([1_000_000.0] * 30, index=closes.index)
    assert np.isnan(compute_adv_dollars(closes, volumes, closes.index[-1], lookback=60))


def test_adv_ignores_data_after_as_of() -> None:
    closes = _daily("2023-01-02", n=120)
    volumes = pd.Series([1_000_000.0] * 120, index=closes.index)
    # Spike volume on the LAST bar; as_of cuts before it
    volumes.iloc[-1] = 1e15
    cutoff = closes.index[-2]
    adv = compute_adv_dollars(closes, volumes, cutoff, lookback=60)
    # ADV should not include the spike (it's after cutoff)
    assert adv < 1e10


# --------------------------------------------------------------------------- #
# Eligibility filter                                                          #
# --------------------------------------------------------------------------- #

def test_is_eligible_all_filters_pass() -> None:
    assert is_eligible(_metrics())


def test_is_eligible_rejects_penny_stock() -> None:
    assert not is_eligible(_metrics(last_price=3.0))


def test_is_eligible_rejects_illiquid() -> None:
    assert not is_eligible(_metrics(adv_dollars=1_000_000.0))


def test_is_eligible_rejects_short_history() -> None:
    assert not is_eligible(_metrics(days_of_history=100))


def test_is_eligible_rejects_nan_score() -> None:
    assert not is_eligible(_metrics(momentum_score=float("nan")))


def test_is_eligible_rejects_nan_adv() -> None:
    assert not is_eligible(_metrics(adv_dollars=float("nan")))


def test_is_eligible_thresholds_are_inclusive_for_history_exclusive_for_price() -> None:
    # Exactly at history minimum: pass
    assert is_eligible(_metrics(days_of_history=252), min_days_history=252)
    # One below: fail
    assert not is_eligible(_metrics(days_of_history=251), min_days_history=252)
    # Exactly at min_price: pass (using >= semantically)
    assert is_eligible(_metrics(last_price=5.0), min_price=5.0)
    # Just below: fail
    assert not is_eligible(_metrics(last_price=4.99), min_price=5.0)


# --------------------------------------------------------------------------- #
# Top-N selection                                                             #
# --------------------------------------------------------------------------- #

def test_select_top_n_orders_by_score_desc() -> None:
    cands = [
        _metrics("A", momentum_score=0.10),
        _metrics("B", momentum_score=0.30),
        _metrics("C", momentum_score=0.20),
    ]
    top = select_top_n(cands, n=2)
    assert [m.symbol for m in top] == ["B", "C"]


def test_select_top_n_breaks_ties_alphabetically() -> None:
    cands = [
        _metrics("Z", momentum_score=0.10),
        _metrics("A", momentum_score=0.10),
        _metrics("M", momentum_score=0.10),
    ]
    top = select_top_n(cands, n=2)
    assert [m.symbol for m in top] == ["A", "M"]


def test_select_top_n_filters_nan_scores() -> None:
    cands = [
        _metrics("A", momentum_score=0.10),
        _metrics("B", momentum_score=float("nan")),
        _metrics("C", momentum_score=0.05),
    ]
    top = select_top_n(cands, n=10)
    assert [m.symbol for m in top] == ["A", "C"]


def test_select_top_n_returns_fewer_if_candidates_smaller() -> None:
    cands = [_metrics("A", momentum_score=0.10)]
    assert len(select_top_n(cands, n=50)) == 1


def test_select_top_n_empty_input_returns_empty() -> None:
    assert select_top_n([], n=50) == []


# --------------------------------------------------------------------------- #
# Equal-weight targets                                                        #
# --------------------------------------------------------------------------- #

def test_equal_weight_targets_basic_under_cap() -> None:
    # 50 names, equity 100k, 2% cash reserve, 3% cap.
    # raw_weight = 0.98 / 50 = 0.0196 (1.96%) — below 3% cap.
    selected = [_metrics(f"S{i:03d}", last_price=100.0) for i in range(50)]
    targets = equal_weight_targets(
        selected, equity=100_000.0, position_cap_pct=0.03, cash_reserve_pct=0.02,
    )
    assert len(targets) == 50
    for t in targets:
        assert t.target_weight == pytest.approx(0.0196)
        # shares = floor(0.0196 * 100000 / 100.0) = floor(19.6) = 19
        assert t.target_shares == 19


def test_equal_weight_targets_cap_binds_for_small_n() -> None:
    # 5 names, equity 100k. raw_weight = 0.98 / 5 = 0.196 (19.6%) — above 3% cap.
    # All names should be capped at 3%.
    selected = [_metrics(f"S{i}", last_price=100.0) for i in range(5)]
    targets = equal_weight_targets(
        selected, equity=100_000.0, position_cap_pct=0.03, cash_reserve_pct=0.02,
    )
    for t in targets:
        assert t.target_weight == pytest.approx(0.03)


def test_equal_weight_targets_zero_equity_returns_empty() -> None:
    selected = [_metrics("A")]
    assert equal_weight_targets(selected, equity=0.0) == []


def test_equal_weight_targets_empty_selection_returns_empty() -> None:
    assert equal_weight_targets([], equity=100_000.0) == []


def test_equal_weight_targets_zero_price_skipped() -> None:
    selected = [
        _metrics("A", last_price=100.0),
        _metrics("B", last_price=0.0),  # bad price; should be skipped
    ]
    targets = equal_weight_targets(selected, equity=100_000.0)
    assert {t.symbol for t in targets} == {"A"}


def test_equal_weight_targets_shares_rounded_down() -> None:
    # equity 100k, 1 name, cap 3% → notional 3000. Price 113 → 26.55 → 26 shares.
    selected = [_metrics("A", last_price=113.0)]
    targets = equal_weight_targets(
        selected, equity=100_000.0, position_cap_pct=0.03, cash_reserve_pct=0.0,
    )
    assert targets[0].target_shares == 26


# --------------------------------------------------------------------------- #
# Sector cap                                                                  #
# --------------------------------------------------------------------------- #

def test_sector_cap_no_op_when_under_cap() -> None:
    targets = [
        TargetPosition("AAPL", 0.02, 100),
        TargetPosition("MSFT", 0.02, 100),
    ]
    sectors = {"AAPL": "tech", "MSFT": "tech"}
    out = apply_sector_cap(targets, sectors, max_sector_weight=0.25)
    # 0.02 + 0.02 = 0.04 < 0.25 → unchanged
    assert {t.symbol for t in out} == {"AAPL", "MSFT"}


def test_sector_cap_scales_down_overweight_sector() -> None:
    # Tech sector totals 0.40 (above 0.25 cap) → scale by 0.25/0.40 = 0.625
    targets = [
        TargetPosition("AAPL", 0.20, 1000),
        TargetPosition("MSFT", 0.20, 1000),
    ]
    sectors = {"AAPL": "tech", "MSFT": "tech"}
    out = apply_sector_cap(targets, sectors, max_sector_weight=0.25)
    for t in out:
        assert t.target_weight == pytest.approx(0.20 * 0.625)
        assert t.target_shares == int(1000 * 0.625)


def test_sector_cap_treats_unknown_sector_per_name() -> None:
    targets = [TargetPosition("WEIRD", 0.05, 100)]
    out = apply_sector_cap(targets, {}, max_sector_weight=0.25)
    assert len(out) == 1  # unknown sector at 0.05 < 0.25 → kept


def test_sector_cap_empty_input() -> None:
    assert apply_sector_cap([], {}) == []


# --------------------------------------------------------------------------- #
# Order diffing                                                               #
# --------------------------------------------------------------------------- #

def test_rebalance_orders_new_positions() -> None:
    current: dict[str, int] = {}
    targets = [TargetPosition("A", 0.02, 100), TargetPosition("B", 0.02, 50)]
    orders = compute_rebalance_orders(current, targets)
    assert orders == {"A": 100, "B": 50}


def test_rebalance_orders_close_dropped_positions() -> None:
    current = {"A": 100, "B": 50}
    targets = [TargetPosition("A", 0.02, 100)]  # B dropped
    orders = compute_rebalance_orders(current, targets)
    assert orders == {"B": -50}


def test_rebalance_orders_adjust_existing() -> None:
    current = {"A": 100}
    targets = [TargetPosition("A", 0.02, 150)]  # increase A by 50
    orders = compute_rebalance_orders(current, targets)
    assert orders == {"A": 50}


def test_rebalance_orders_zero_delta_omitted() -> None:
    current = {"A": 100}
    targets = [TargetPosition("A", 0.02, 100)]  # no change
    assert compute_rebalance_orders(current, targets) == {}


def test_rebalance_orders_mixed_scenario() -> None:
    current = {"A": 100, "B": 50, "C": 200}
    targets = [
        TargetPosition("A", 0.02, 100),  # unchanged → omitted
        TargetPosition("B", 0.02, 75),   # increase by 25
        TargetPosition("D", 0.02, 30),   # new
        # C dropped → close
    ]
    orders = compute_rebalance_orders(current, targets)
    assert orders == {"B": 25, "C": -200, "D": 30}


# --------------------------------------------------------------------------- #
# Rebalance schedule                                                          #
# --------------------------------------------------------------------------- #

def test_first_trading_day_with_calendar() -> None:
    # Trading days in Jan 2024 (Mon-Fri minus holidays)
    cal = pd.bdate_range("2024-01-02", "2024-01-31")
    assert is_first_trading_day_of_month(pd.Timestamp("2024-01-02"), trading_calendar=cal)
    assert not is_first_trading_day_of_month(pd.Timestamp("2024-01-03"), trading_calendar=cal)
    assert not is_first_trading_day_of_month(pd.Timestamp("2024-01-15"), trading_calendar=cal)


def test_first_trading_day_fallback_no_calendar() -> None:
    # 2024-01-01 is Monday (New Year's), but fallback doesn't know about holidays
    # → falls back to first weekday of month. For Jan 2024 that's Jan 1 (Mon).
    # For Feb 2024 the first weekday is Feb 1 (Thu).
    assert is_first_trading_day_of_month(pd.Timestamp("2024-01-01"))
    assert not is_first_trading_day_of_month(pd.Timestamp("2024-01-02"))
    assert is_first_trading_day_of_month(pd.Timestamp("2024-02-01"))


def test_first_trading_day_calendar_handles_holiday_shift() -> None:
    # If 2024-01-01 is excluded from the calendar (holiday), first trading
    # day becomes 2024-01-02
    cal = pd.bdate_range("2024-01-02", "2024-01-31")
    assert not is_first_trading_day_of_month(pd.Timestamp("2024-01-01"), trading_calendar=cal)
    assert is_first_trading_day_of_month(pd.Timestamp("2024-01-02"), trading_calendar=cal)


# --------------------------------------------------------------------------- #
# Drawdown circuit breaker                                                    #
# --------------------------------------------------------------------------- #

def test_drawdown_tracks_peak() -> None:
    state = DrawdownState()
    update_drawdown_state(state, 100_000)
    assert state.peak_equity == 100_000
    update_drawdown_state(state, 105_000)
    assert state.peak_equity == 105_000
    update_drawdown_state(state, 102_000)  # below peak, peak unchanged
    assert state.peak_equity == 105_000


def test_drawdown_trips_at_threshold() -> None:
    state = DrawdownState()
    update_drawdown_state(state, 100_000)
    # 25.1% drawdown → trip
    mult = update_drawdown_state(state, 74_900, halt_threshold=-0.25, resume_threshold=-0.10)
    assert state.halt_active is True
    assert mult == 0.5


def test_drawdown_does_not_trip_above_threshold() -> None:
    state = DrawdownState()
    update_drawdown_state(state, 100_000)
    mult = update_drawdown_state(state, 80_000, halt_threshold=-0.25, resume_threshold=-0.10)
    assert state.halt_active is False
    assert mult == 1.0


def test_drawdown_hysteresis_does_not_resume_until_recovery() -> None:
    state = DrawdownState()
    update_drawdown_state(state, 100_000)
    # Trip at -25%
    update_drawdown_state(state, 70_000, halt_threshold=-0.25, resume_threshold=-0.10)
    assert state.halt_active is True
    # Recover to -15% — still halted (below -10% resume threshold)
    mult = update_drawdown_state(state, 85_000, halt_threshold=-0.25, resume_threshold=-0.10)
    assert state.halt_active is True
    assert mult == 0.5
    # Recover to -5% — resume
    mult = update_drawdown_state(state, 95_000, halt_threshold=-0.25, resume_threshold=-0.10)
    assert state.halt_active is False
    assert mult == 1.0


def test_drawdown_reset_clears_state() -> None:
    state = DrawdownState(peak_equity=100_000, halt_active=True)
    state.reset()
    assert state.peak_equity == 0.0
    assert state.halt_active is False


def test_drawdown_zero_equity_returns_conservative_multiplier() -> None:
    state = DrawdownState()
    mult = update_drawdown_state(state, 0.0)
    assert mult == 0.5


# --------------------------------------------------------------------------- #
# build_symbol_metrics                                                        #
# --------------------------------------------------------------------------- #

def test_build_symbol_metrics_assembles_all_fields() -> None:
    n = 300
    idx = pd.bdate_range(start="2022-01-03", periods=n)
    closes = [100.0 * (1.001 ** i) for i in range(n)]
    volumes = [1_000_000.0] * n
    df = pd.DataFrame(
        {"close": closes, "volume": volumes,
         "open": closes, "high": closes, "low": closes},
        index=idx,
    )
    m = build_symbol_metrics("AAPL", df, idx[-1])
    assert m.symbol == "AAPL"
    assert m.days_of_history == n
    assert m.last_price == pytest.approx(closes[-1])
    assert np.isfinite(m.momentum_score)
    assert np.isfinite(m.adv_dollars)


def test_build_symbol_metrics_empty_bars_returns_nan() -> None:
    df = pd.DataFrame(
        {"close": [], "volume": [], "open": [], "high": [], "low": []},
        index=pd.DatetimeIndex([]),
    )
    m = build_symbol_metrics("AAPL", df, pd.Timestamp("2024-01-02"))
    assert m.days_of_history == 0
    assert np.isnan(m.momentum_score)
    assert np.isnan(m.adv_dollars)
    assert np.isnan(m.last_price)


# --------------------------------------------------------------------------- #
# Side enum                                                                   #
# --------------------------------------------------------------------------- #

def test_side_enum_values() -> None:
    assert Side.LONG.value == "long"
    assert Side.SHORT.value == "short"
