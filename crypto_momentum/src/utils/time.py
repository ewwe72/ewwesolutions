"""Time helpers for the crypto bot.

Crypto markets are 24/7. There is no NYSE calendar, no holidays, no
weekend exclusion. The only operational time boundary the bot cares
about is "what day is it" (for the weekly rebalance trigger), and
"that's a fresh bar from yesterday" (for the data-feed gate).

We use UTC throughout — local time is irrelevant because the bot is
scheduled by the host OS in local time but its internal date math
should be regime-stable across DST transitions. Polish hosts' local
date can flip a few hours before UTC's; we anchor to UTC to avoid
"the rebalance fired Monday locally but Sunday in UTC" ambiguity.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Wall-clock UTC. Easier to mock than ``datetime.utcnow()`` (deprecated)."""
    return datetime.now(tz=timezone.utc)


def is_rebalance_weekday(today_weekday: int, *, target_weekday: int) -> bool:
    """Match Python's ``date.weekday()``: 0=Monday, 6=Sunday."""
    return today_weekday == target_weekday
