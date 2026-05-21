# test_date_inference.py — Regression tests for bulletin year inference.
#
# These tests cover the season-boundary logic in _infer_date_with_year so that
# the off-by-one that mis-dated Nov 17 bulletins cannot regress silently.
# The reference date is simulated as 2026-05-21 (the date of the original
# backfill run).
#
# Key verified gaps from 2026-05-21:
#   date(2026, 11, 17) → 180 days ahead  — boundary; must step back to 2025
#   date(2026, 11, 18) → 181 days ahead  — over threshold; must step back to 2025
#   date(2026, 11, 16) → 179 days ahead  — under threshold; stays in 2026
#   date(2026, 12, 31) → 224 days ahead  — well over; steps back to 2025
"""Regression tests for _infer_date_with_year (season-boundary year inference)."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import _infer_date_with_year  # noqa: E402

# Simulated reference date for all tests in this module.
TODAY = date(2026, 5, 21)


class TestInferDateWithYear:
    """Tests for _infer_date_with_year()."""

    def test_nov_17_is_exactly_180_days_ahead(self) -> None:
        """Verify the assumed gap so the boundary test is grounded in arithmetic."""
        delta = (date(2026, 11, 17) - TODAY).days
        assert delta == 180, f"Expected 180-day gap, got {delta}"

    def test_nov_17_steps_back_to_previous_year(self) -> None:
        """Nov 17 is exactly 180 days ahead of May 21 — must step back to 2025.

        With the original > 180 threshold this was NOT caught (180 is not
        strictly greater than 180).  The corrected >= 180 threshold fixes it.
        """
        result = _infer_date_with_year(month=11, day=17, today=TODAY)
        assert result == date(2025, 11, 17)

    def test_nov_18_also_steps_back(self) -> None:
        """Nov 18 is 181 days ahead — also >= 180, so steps back to 2025."""
        delta = (date(2026, 11, 18) - TODAY).days
        assert delta == 181
        result = _infer_date_with_year(month=11, day=18, today=TODAY)
        assert result == date(2025, 11, 18)

    def test_nov_16_stays_in_current_year(self) -> None:
        """Nov 16 is 179 days ahead — under the threshold, stays in 2026."""
        delta = (date(2026, 11, 16) - TODAY).days
        assert delta == 179
        result = _infer_date_with_year(month=11, day=16, today=TODAY)
        assert result == date(2026, 11, 16)

    def test_recent_past_date_stays_in_current_year(self) -> None:
        """A date in the recent past (same month) should stay in the current year."""
        result = _infer_date_with_year(month=5, day=1, today=TODAY)
        assert result == date(2026, 5, 1)

    def test_invalid_date_returns_none(self) -> None:
        """An impossible month/day combination (e.g. Feb 30) returns None."""
        result = _infer_date_with_year(month=2, day=30, today=TODAY)
        assert result is None

    def test_dec_31_steps_back(self) -> None:
        """Dec 31 is 224 days ahead — well over the threshold, steps to 2025."""
        result = _infer_date_with_year(month=12, day=31, today=TODAY)
        assert result == date(2025, 12, 31)

    def test_today_itself_stays_current_year(self) -> None:
        """The reference date itself (0 days) stays in the current year."""
        result = _infer_date_with_year(month=5, day=21, today=TODAY)
        assert result == date(2026, 5, 21)
