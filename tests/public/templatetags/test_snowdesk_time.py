"""
tests/public/templatetags/test_snowdesk_time.py — Tests for the
``snowdesk_time`` template filters.

Covers ``parse_iso`` (Z-suffix and explicit-offset normalisation, naive
fallback to UTC, falsy / malformed input) and the integer→string mapping
filters ``danger_level_key`` / ``danger_level_label`` (full 1–5 range
plus the falsy / out-of-range / non-int guards).

All datetime literals carry ``tzinfo`` per the project test conventions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from public.templatetags.snowdesk_time import (
    danger_level_key,
    danger_level_label,
    parse_iso,
)


class TestParseIso:
    """Tests for the ``parse_iso`` template filter."""

    @pytest.mark.parametrize("falsy", [None, ""])
    def test_falsy_input_returns_none(self, falsy: str | None) -> None:
        """``None`` and the empty string short-circuit to ``None``."""
        assert parse_iso(falsy) is None

    def test_z_suffix_normalised_to_utc(self) -> None:
        """A trailing ``Z`` is normalised to ``+00:00`` and parsed as UTC."""
        result = parse_iso("2026-01-15T16:00:00Z")
        assert result == datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)

    def test_explicit_offset_preserved(self) -> None:
        """An explicit ``+HH:MM`` offset is preserved."""
        result = parse_iso("2026-01-15T16:00:00+02:00")
        assert result is not None
        assert result.utcoffset() is not None
        assert result.hour == 16
        assert result.tzinfo is not None

    def test_naive_string_assumed_utc(self) -> None:
        """A naive ISO string is parsed and stamped with UTC tzinfo."""
        result = parse_iso("2026-01-15T16:00:00")
        assert result == datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_whitespace_is_stripped(self) -> None:
        """Leading/trailing whitespace is stripped before parsing."""
        result = parse_iso("  2026-01-15T16:00:00Z  ")
        assert result == datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "malformed",
        ["not-a-date", "2026-13-40T99:99:99", "garbage"],
    )
    def test_malformed_input_returns_none(self, malformed: str) -> None:
        """Unparseable strings fall through the except clause to ``None``."""
        assert parse_iso(malformed) is None

    def test_non_string_input_returns_none(self) -> None:
        """A non-string value triggers ``AttributeError`` and returns ``None``."""
        # 123 has no ``.strip`` / ``.replace`` — the AttributeError branch.
        assert parse_iso(123) is None  # type: ignore[arg-type]


class TestDangerLevelKey:
    """Tests for the ``danger_level_key`` template filter."""

    @pytest.mark.parametrize(
        "level, expected",
        [
            (1, "low"),
            (2, "moderate"),
            (3, "considerable"),
            (4, "high"),
            (5, "very_high"),
        ],
    )
    def test_known_levels_return_key(self, level: int, expected: str) -> None:
        """Levels 1–5 map to the documented CSS data-level keys."""
        assert danger_level_key(level) == expected

    def test_none_returns_empty(self) -> None:
        """``None`` returns the empty string."""
        assert danger_level_key(None) == ""

    @pytest.mark.parametrize("level", [0, 6, -1, 99])
    def test_out_of_range_returns_empty(self, level: int) -> None:
        """An integer outside 1–5 returns the empty string."""
        assert danger_level_key(level) == ""

    def test_string_digit_coerced(self) -> None:
        """A numeric string is coerced via ``int()`` and looked up."""
        assert danger_level_key("3") == "considerable"  # type: ignore[arg-type]

    @pytest.mark.parametrize("level", ["nope", [1]])
    def test_non_int_returns_empty(self, level: Any) -> None:
        """Non-numeric inputs hit the ``except`` clause and return ""."""
        assert danger_level_key(level) == ""


class TestDangerLevelLabel:
    """Tests for the ``danger_level_label`` template filter."""

    @pytest.mark.parametrize(
        "level, expected",
        [
            (1, "Low"),
            (2, "Moderate"),
            (3, "Considerable"),
            (4, "High"),
            (5, "Very High"),
        ],
    )
    def test_known_levels_return_label(self, level: int, expected: str) -> None:
        """Levels 1–5 map to the documented human-readable labels."""
        assert danger_level_label(level) == expected

    def test_none_returns_empty(self) -> None:
        """``None`` returns the empty string."""
        assert danger_level_label(None) == ""

    @pytest.mark.parametrize("level", [0, 6, -1, 99])
    def test_out_of_range_returns_empty(self, level: int) -> None:
        """An integer outside 1–5 returns the empty string."""
        assert danger_level_label(level) == ""

    def test_string_digit_coerced(self) -> None:
        """A numeric string is coerced via ``int()`` and looked up."""
        assert danger_level_label("4") == "High"  # type: ignore[arg-type]

    @pytest.mark.parametrize("level", ["nope", [1]])
    def test_non_int_returns_empty(self, level: Any) -> None:
        """Non-numeric inputs hit the ``except`` clause and return ""."""
        assert danger_level_label(level) == ""
