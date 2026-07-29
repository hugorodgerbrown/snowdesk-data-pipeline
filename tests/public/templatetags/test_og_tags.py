"""
tests/public/templatetags/test_og_tags.py — Tests for the ``og_tags``
template filters.

Covers ``og_locale`` with the common Django language codes, edge cases
(empty string, ``None``, already-underscored input, single-segment code),
and the normalisation contract (territory portion always upper-case).
"""

from __future__ import annotations

import pytest

from apps.public.templatetags.og_tags import og_locale


class TestOgLocale:
    """Tests for the ``og_locale`` template filter."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            # Common two-segment codes.
            ("en-gb", "en_GB"),
            ("en-us", "en_US"),
            ("de-ch", "de_CH"),
            ("fr-ch", "fr_CH"),
            ("it-ch", "it_CH"),
            # Single-segment codes returned as-is.
            ("fr", "fr"),
            ("de", "de"),
            ("it", "it"),
            # Input already uses underscore separator (no double-transformation).
            ("en_GB", "en_GB"),
            ("de_CH", "de_CH"),
        ],
    )
    def test_conversion(self, value: str, expected: str) -> None:
        """Language codes are converted to OpenGraph locale format."""
        assert og_locale(value) == expected

    @pytest.mark.parametrize("falsy", [None, ""])
    def test_falsy_returns_empty_string(self, falsy: str | None) -> None:
        """``None`` and empty string both return ``""``."""
        assert og_locale(falsy) == ""

    def test_territory_is_upper_case(self) -> None:
        """Territory portion is always upper-cased regardless of input case."""
        assert og_locale("en-gb") == "en_GB"
        assert og_locale("en-GB") == "en_GB"
        assert og_locale("EN-gb") == "EN_GB"

    def test_preserves_single_segment(self) -> None:
        """Single-segment language tags (no territory) are returned unchanged."""
        assert og_locale("de") == "de"
        assert og_locale("fr") == "fr"
