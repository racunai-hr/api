"""Date helpers — timezone-aware utilities."""

from datetime import date


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return (first_day, last_day) for a calendar month."""
    from calendar import monthrange

    last = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


__all__ = ['month_bounds']
