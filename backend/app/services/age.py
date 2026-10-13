"""Age helpers.

Age is always derived from date of birth so a stored age cannot go stale, and
the 18+ gate is evaluated in exactly one place.
"""

from __future__ import annotations

from datetime import date, datetime

from app.core.config import settings


def age_on(date_of_birth: date | datetime, as_of: date | None = None) -> int:
    """Whole years completed. Handles leap-day birthdays correctly."""
    if isinstance(date_of_birth, datetime):
        date_of_birth = date_of_birth.date()
    reference = as_of or date.today()
    had_birthday = (reference.month, reference.day) >= (
        date_of_birth.month,
        date_of_birth.day,
    )
    return reference.year - date_of_birth.year - (0 if had_birthday else 1)


def is_eligible_age(date_of_birth: date | datetime, as_of: date | None = None) -> bool:
    """The single 18+ gate for the whole application."""
    return age_on(date_of_birth, as_of) >= settings.minimum_age


def dob_bounds_for_age_range(min_age: int, max_age: int, as_of: date | None = None):
    """Translate an age filter into a date-of-birth range.

    Filtering on an indexed `date_of_birth` column lets Postgres use an index,
    where computing each candidate's age in SQL would force a scan.

    Returns `(earliest_dob, latest_dob)` — both inclusive.
    """
    reference = as_of or date.today()
    # Someone who is exactly `max_age` was born no earlier than this.
    earliest = _shift_years(reference, -(max_age + 1))
    # Someone who is exactly `min_age` was born no later than this.
    latest = _shift_years(reference, -min_age)
    return earliest, latest


def _shift_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # 29 February in a non-leap target year.
        return value.replace(year=value.year + years, day=28)
