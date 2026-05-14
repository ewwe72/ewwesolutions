"""Trading universe and NYSE calendar.

The calendar wrapper uses ``pandas_market_calendars`` which already knows
about NYSE holidays and half-days.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from typing import Sequence

import pandas as pd
import pandas_market_calendars as mcal

from .time import ET


@lru_cache(maxsize=1)
def _nyse() -> mcal.MarketCalendar:
    return mcal.get_calendar("NYSE")


def is_trading_day(d: date) -> bool:
    """True if NYSE is open on this calendar date (full or half day)."""
    schedule = _nyse().schedule(start_date=d.isoformat(), end_date=d.isoformat())
    return not schedule.empty


def early_close_et(d: date) -> datetime | None:
    """Return the early-close timestamp in ET if ``d`` is a half-day, else None.

    Half-days (e.g. day after Thanksgiving, Christmas Eve) close at 13:00 ET.
    """
    schedule = _nyse().schedule(start_date=d.isoformat(), end_date=d.isoformat())
    if schedule.empty:
        return None
    close_utc: pd.Timestamp = schedule.iloc[0]["market_close"]
    close_et = close_utc.tz_convert(ET)
    # Normal close is 16:00 ET; anything earlier is a half-day.
    if close_et.hour < 16:
        return close_et.to_pydatetime()
    return None


def trading_days(start: date, end: date) -> Sequence[date]:
    """All NYSE trading days in [start, end] inclusive."""
    schedule = _nyse().schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return [ts.date() for ts in schedule.index]
