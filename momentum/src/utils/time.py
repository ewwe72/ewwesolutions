"""Timezone and trading-session helpers.

All trading logic operates in America/New_York via stdlib ``zoneinfo`` so
DST transitions are handled correctly (no pytz, no manual offset math).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def et_now() -> datetime:
    """Current wall-clock time in US Eastern."""
    return datetime.now(tz=ET)


def to_et(dt: datetime) -> datetime:
    """Convert any aware datetime to ET. Raises on naive input."""
    if dt.tzinfo is None:
        raise ValueError("Refusing to convert naive datetime; supply tzinfo.")
    return dt.astimezone(ET)


def parse_hhmm(hhmm: str) -> time:
    """Parse 'HH:MM' into a naive ``time`` object."""
    hour_str, minute_str = hhmm.split(":")
    return time(hour=int(hour_str), minute=int(minute_str))


def combine_et(d: date, t: time) -> datetime:
    """Build an ET-aware datetime from a date + naive time."""
    return datetime.combine(d, t, tzinfo=ET)


@dataclass(frozen=True)
class TradingDay:
    """The key timestamps for one trading session, all ET-aware."""

    session_date: date
    premarket_start: datetime
    regular_open: datetime
    opening_range_end: datetime
    entry_cutoff: datetime
    eod_flat: datetime
    regular_close: datetime


def build_trading_day(
    session_date: date,
    *,
    premarket_start_hhmm: str,
    opening_range_minutes: int,
    entry_cutoff_hhmm: str,
    eod_flat_hhmm: str,
) -> TradingDay:
    """Construct the canonical timestamps for one session.

    The regular session is always 09:30 ET open / 16:00 ET close. Early-close
    days (half-days) are handled by the market calendar, not by this function:
    on those days the caller should pass an ``eod_flat_hhmm`` of '12:55' etc.
    """
    regular_open = combine_et(session_date, time(9, 30))
    regular_close = combine_et(session_date, time(16, 0))
    return TradingDay(
        session_date=session_date,
        premarket_start=combine_et(session_date, parse_hhmm(premarket_start_hhmm)),
        regular_open=regular_open,
        opening_range_end=regular_open + timedelta(minutes=opening_range_minutes),
        entry_cutoff=combine_et(session_date, parse_hhmm(entry_cutoff_hhmm)),
        eod_flat=combine_et(session_date, parse_hhmm(eod_flat_hhmm)),
        regular_close=regular_close,
    )
