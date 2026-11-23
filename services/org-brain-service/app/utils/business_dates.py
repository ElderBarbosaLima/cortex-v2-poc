"""Business dates are Japanese calendar dates (Q8): a date means midnight JST."""

from __future__ import annotations

import datetime as dt

JST = dt.timezone(dt.timedelta(hours=9))

# "Valid since forever" for a rule whose start nobody knows (a rule learned
# from corrections). valid_from is NOT NULL, so this stands for no lower bound.
NO_LOWER_BOUND = dt.date(1900, 1, 1)


def business_start(day: dt.date) -> dt.datetime:
    """The instant a business date begins: 00:00 JST."""
    return dt.datetime(day.year, day.month, day.day, tzinfo=JST)
