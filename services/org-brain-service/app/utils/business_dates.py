"""Business dates are Japanese calendar dates (Q8): a date means midnight JST."""

from __future__ import annotations

import datetime as dt

JST = dt.timezone(dt.timedelta(hours=9))


def business_start(day: dt.date) -> dt.datetime:
    """The instant a business date begins: 00:00 JST."""
    return dt.datetime(day.year, day.month, day.day, tzinfo=JST)
