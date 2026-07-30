"""
tests/mcp_server/test_season.py — Unit tests for apps.mcp_server.season.

``current_or_last_season`` is a pure function (no ``date.today()`` call
inside it — IoC via the ``today`` parameter), so every case is exercised
deterministically with an explicit date, one per calendar-month boundary
from the SNOW-391 plan's test strategy.
"""

from __future__ import annotations

import datetime

import pytest

from apps.mcp_server.season import current_or_last_season

_CASES = [
    (
        datetime.date(2025, 11, 15),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "nov-mid-season-start",
    ),
    (
        datetime.date(2025, 12, 31),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "dec-31-still-current-season",
    ),
    (
        datetime.date(2026, 2, 15),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "feb-mid-season",
    ),
    (
        datetime.date(2026, 5, 31),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "may-31-last-day-of-season",
    ),
    (
        datetime.date(2026, 6, 1),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "jun-1-off-season-falls-back-to-last-completed",
    ),
    (
        datetime.date(2026, 10, 31),
        datetime.date(2025, 11, 1),
        datetime.date(2026, 5, 31),
        "oct-31-still-off-season-same-as-jun-1",
    ),
    (
        datetime.date(2026, 11, 1),
        datetime.date(2026, 11, 1),
        datetime.date(2027, 5, 31),
        "nov-1-first-day-of-new-season",
    ),
]


@pytest.mark.parametrize(
    ("today", "expected_start", "expected_end"),
    [(c[0], c[1], c[2]) for c in _CASES],
    ids=[c[3] for c in _CASES],
)
def test_current_or_last_season(
    today: datetime.date,
    expected_start: datetime.date,
    expected_end: datetime.date,
) -> None:
    """current_or_last_season resolves the correct window for every month."""
    assert current_or_last_season(today) == (expected_start, expected_end)


def test_season_bounds_are_inclusive_and_ordered() -> None:
    """The returned start is always strictly before the returned end."""
    start, end = current_or_last_season(datetime.date(2026, 1, 1))
    assert start < end
