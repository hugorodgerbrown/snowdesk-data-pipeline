"""
tests/public/templatetags/test_og_tags.py — Tests for the ``og_tags``
template filters.

Covers ``og_locale`` with the common Django language codes, edge cases
(empty string, ``None``, already-underscored input, single-segment code),
and the normalisation contract (territory portion always upper-case).

Covers ``squish``'s escaping contract: an already-escaped value is not
escaped a second time, and a raw value is still escaped once.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.utils.safestring import mark_safe

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


class TestSquishEscaping:
    """``squish`` escapes an apostrophe exactly once, safe input or not."""

    def _render(self, value: str) -> str:
        """Render ``value`` through ``|squish`` with autoescape on."""
        template = Template("{% load og_tags %}{{ value|squish }}")
        return template.render(Context({"value": value}))

    def test_safe_input_is_not_escaped_again(self) -> None:
        """A blocktrans asvar result (already escaped, marked safe) stays as-is.

        Regression: "Val d'Entremont" reached the browser tab as
        "Val d&amp;#x27;Entremont" because squish dropped the safe mark.
        """
        value = mark_safe("Val d&#x27;Entremont-Val Ferret · Today")
        assert self._render(value) == "Val d&#x27;Entremont-Val Ferret · Today"

    def test_raw_input_is_still_escaped(self) -> None:
        """An unescaped string is escaped once by autoescape, as before."""
        assert self._render("Val d'Entremont  <b>") == "Val d&#x27;Entremont &lt;b&gt;"
