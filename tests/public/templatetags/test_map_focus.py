"""
tests/public/templatetags/test_map_focus.py — Tests for ``focus_target``.

The tag that formats WGS-84 ordinates into a UGC panel row's
``data-row-focus`` attribute, so pressing the row's name frames that place
on the map.

Two things carry the tag's whole reason for existing, and both are covered
here.  The first is that the output is LOCALE-INDEPENDENT: interpolating a
float through a template renders it through the active locale, so a German
UI would emit ``7,53`` — unparseable to ``Number()``, and not even
splittable back apart on the comma that separates the ordinates.  The
second is that a malformed call yields the empty string rather than a
partial coordinate, because ``{% if focus_target %}`` in the row is what
decides whether the name renders as a button at all: an empty value means
"no focus control", and a half-formed one would mean "a button that flies
the map somewhere arbitrary".
"""

from __future__ import annotations

from typing import Any

import pytest
from django.template import Context, Template
from django.utils import translation

from apps.public.templatetags.map_focus import focus_target


class TestFocusTargetShape:
    """Two ordinates for a point, four for a bbox, nothing else."""

    def test_a_point_is_two_ordinates(self) -> None:
        """Longitude first, matching GeoJSON axis order."""
        assert focus_target(7.5, 46.1) == "7.500000,46.100000"

    def test_a_bbox_is_four(self) -> None:
        """West, south, east, north — a GeoJSON bbox, in its own order."""
        assert focus_target(7.1, 46.0, 7.3, 46.2) == (
            "7.100000,46.000000,7.300000,46.200000"
        )

    def test_integers_are_accepted(self) -> None:
        """A whole-degree ordinate still formats to six decimals."""
        assert focus_target(7, 46) == "7.000000,46.000000"

    @pytest.mark.parametrize("count", [0, 1, 3, 5])
    def test_any_other_count_yields_nothing(self, count: int) -> None:
        """Three ordinates frame nothing, so they render no control."""
        assert focus_target(*([1.0] * count)) == ""


class TestFocusTargetGuards:
    """A missing or unreadable ordinate suppresses the whole attribute."""

    @pytest.mark.parametrize("missing", [None, ""])
    def test_a_missing_ordinate_yields_nothing(self, missing: Any) -> None:
        """A route's bbox is JSON, so an index past its end is empty string.

        The template engine resolves a missing index to the empty string
        rather than raising, so this is the shape a wrong index actually
        arrives in — and ``float("")`` would be a ValueError, not a
        coordinate.
        """
        assert focus_target(7.5, missing) == ""

    def test_a_non_numeric_ordinate_yields_nothing(self) -> None:
        """No exception escapes into the render of an unrelated row."""
        assert focus_target("north", 46.1) == ""


class TestFocusTargetLocale:
    """The output does not follow the active locale. This is the point."""

    def test_a_comma_decimal_locale_still_gets_a_full_stop(self) -> None:
        """Rendered through a template under de-DE, with l10n active.

        Exercised through ``Template`` rather than by calling the function,
        because the failure this guards against is a rendering one: the
        same value interpolated as ``{{ favourite.longitude }}`` under this
        locale is what produced ``7,5`` and would split into three
        ordinates instead of two.
        """
        template = Template("{% load map_focus %}{% focus_target lon lat %}")

        with translation.override("de"):
            rendered = template.render(Context({"lon": 7.5, "lat": 46.1}))

        assert rendered == "7.500000,46.100000"

    def test_the_naive_interpolation_it_replaces_would_have_drifted(self) -> None:
        """The counter-example, pinned so the reason cannot be forgotten.

        If this ever renders "7.5" the localisation setting has changed and
        the tag is no longer load-bearing — at which point deleting it is a
        decision, not an accident.
        """
        template = Template("{{ lon }}")

        with translation.override("de"):
            rendered = template.render(Context({"lon": 7.5}))

        assert rendered == "7,5"
