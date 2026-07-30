"""
tests/core/test_locale.py — Tests for apps.core.locale.primary_language.

Covers the common Accept-Language patterns, q-value stripping, the
primary-subtag extraction (e.g. "en-GB" → "en"), and graceful degradation
on empty / invalid input.
"""

from __future__ import annotations

import pytest

from apps.core.locale import primary_language


class TestPrimaryLanguage:
    """Tests for primary_language(accept_language)."""

    def test_simple_tag_returned_as_is(self) -> None:
        """A plain two-letter tag is returned lowercase."""
        assert primary_language("en") == "en"

    def test_multi_subtag_returns_primary_only(self) -> None:
        """Language+region tags return only the primary subtag."""
        assert primary_language("en-GB") == "en"
        assert primary_language("de-CH") == "de"
        assert primary_language("fr-CH") == "fr"

    def test_q_value_stripped(self) -> None:
        """Q-value qualifiers are removed before returning the tag."""
        assert primary_language("en;q=0.9") == "en"

    def test_first_tag_selected_from_list(self) -> None:
        """When multiple tags are listed, the first (highest-priority) is returned."""
        assert primary_language("en-GB,en;q=0.9,fr;q=0.8") == "en"
        assert primary_language("de-AT,de;q=0.9,en;q=0.8") == "de"

    def test_uppercase_tag_normalised_to_lowercase(self) -> None:
        """Language tags are normalised to lowercase."""
        assert primary_language("FR-CH") == "fr"
        assert primary_language("DE") == "de"

    def test_underscore_separator_treated_as_hyphen(self) -> None:
        """Underscore-separated tags (e.g. zh_TW) are treated as hyphen-separated."""
        assert primary_language("zh_TW") == "zh"

    def test_empty_string_returns_empty(self) -> None:
        """An empty header value returns an empty string."""
        assert primary_language("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        """A whitespace-only header value returns an empty string."""
        assert primary_language("   ") == ""

    def test_invalid_tag_returns_empty(self) -> None:
        """Tags that are not valid primary subtags return an empty string."""
        assert primary_language("@@invalid@@") == ""
        assert primary_language("1") == ""  # single digit — too short / non-alpha
        assert primary_language("123456789") == ""  # digits only — non-alpha

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("en-GB,en;q=0.9", "en"),
            ("fr-CH", "fr"),
            ("de", "de"),
            ("it;q=0.8,de-AT", "it"),
            ("", ""),
        ],
    )
    def test_parametrised_cases(self, header: str, expected: str) -> None:
        """Parametrised spot-checks for common header values."""
        assert primary_language(header) == expected
