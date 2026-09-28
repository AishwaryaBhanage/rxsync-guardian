"""Opening hours and processing-time arithmetic.

Every decision about *when* something happens goes through this module, so the
"closed on Sunday" rule has exactly one enforcement point rather than being
re-checked at each call site.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

SUNDAY = 6  # date.weekday(): Monday is 0

# Base *open* hours from "received" to "ready", before the area multiplier and
# jitter. These are smaller than the wall-clock medians in the spec: work only
# advances while the pharmacy is open, so a job that cannot finish before
# closing resumes the next morning and its elapsed time absorbs the overnight
# gap. See specs/01-simulator.md.
BASE_PROCESS_HOURS = {"large": 2.0, "medium": 4.5, "small": 6.0}
AREA_MULTIPLIER = {"urban": 1.0, "rural": 1.3}

# A pharmacy shut every day would make advance_business_hours loop forever;
# bound the walk and fail loudly instead.
_MAX_DAYS_SCANNED = 60


@dataclass(frozen=True)
class OpeningHours:
    """Opening window per weekday, indexed Monday..Sunday. None means closed."""

    windows: tuple[tuple[int, int] | None, ...]

    def __post_init__(self) -> None:
        if len(self.windows) != 7:
            raise ValueError("windows must hold one entry per weekday")

    def is_open_on(self, day: date) -> bool:
        return self.windows[day.weekday()] is not None

    def window_for(self, day: date) -> tuple[datetime, datetime] | None:
        """Concrete open/close datetimes on `day`, or None when closed."""
        hours = self.windows[day.weekday()]
        if hours is None:
            return None
        open_hour, close_hour = hours
        return (
            datetime.combine(day, time(hour=open_hour)),
            datetime.combine(day, time(hour=close_hour)),
        )

    def encode(self) -> str:
        """Round-trippable text form for the truth CSV, e.g. `9-18|...|closed`."""
        return "|".join("closed" if w is None else f"{w[0]}-{w[1]}" for w in self.windows)


def advance_business_hours(start: datetime, hours: float, opening: OpeningHours) -> datetime:
    """Move `start` forward by `hours` of *open* time.

    Closed days and after-hours gaps are skipped rather than consumed, so a
    pharmacy shut on Sunday can never produce a timestamp on a Sunday: there is
    no open window there for the clock to land in.

    `hours=0` therefore returns the next moment the pharmacy is open, which is
    what `next_open_moment` relies on.
    """
    remaining = timedelta(minutes=round(hours * 60))  # whole minutes: no float drift
    cursor = start
    for _ in range(_MAX_DAYS_SCANNED):
        window = opening.window_for(cursor.date())
        if window is None:
            cursor = _next_midnight(cursor)
            continue
        opens_at, closes_at = window
        cursor = max(cursor, opens_at)
        if cursor >= closes_at:
            cursor = _next_midnight(cursor)
            continue
        available = closes_at - cursor
        if remaining <= available:
            return cursor + remaining
        remaining -= available
        cursor = _next_midnight(cursor)
    raise ValueError(f"no open window within {_MAX_DAYS_SCANNED} days of {start:%Y-%m-%d}")


def next_open_moment(start: datetime, opening: OpeningHours) -> datetime:
    """The first instant at or after `start` when the pharmacy is open."""
    return advance_business_hours(start, 0.0, opening)


def processing_hours(size: str, area: str, rng: random.Random) -> float:
    """Open hours from "received" to "ready" for one fill.

    Jitter is lognormal with mu=0, whose median is exactly 1.0, so the
    large-before-small ordering the acceptance criterion checks is preserved by
    construction instead of depending on the draw.
    """
    base = BASE_PROCESS_HOURS[size] * AREA_MULTIPLIER[area]
    return base * rng.lognormvariate(0.0, 0.25)


def _next_midnight(moment: datetime) -> datetime:
    return datetime.combine(moment.date() + timedelta(days=1), time.min)
