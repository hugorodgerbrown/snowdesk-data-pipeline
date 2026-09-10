"""
tests/templates/includes/test_overlay_banner_icons.py — the marks
_overlay_banner.html draws, and the fact that it no longer draws them
itself.

SNOW-869 wrote down the icon rule
(``docs/decisions/ui-icons-are-house-stroke-partials.md``) because the
banner's refresh glyph had been corrupt for a year with no second copy to
compare it against. Its two remaining inline glyphs are the same rule from
the other direction:

  * **calendar** — the mark the off-season strip carried, which ALSO
    existed inline in ``includes/nav.html``'s season trigger at a
    different size. Two copies that had to be edited in step, and would
    not have been. That strip has since been dropped, so the nav is the
    only caller left; the strip's own render is exercised here directly.
  * **location-off** — the off-map nudge's struck-through pin, five
    subpaths that only read correctly at 18px when all five agree.

Both are ``_icon_*.html`` partials now, so what is pinned here is that the
banner *includes* them: the mark reaches the page, the caller's size and
class overrides survive the include, and the same partial serves the nav's
copy. A test asserting only "a calendar appears" would pass just as well
against a re-inlined path, which is the regression this file exists to
catch.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.template.loader import render_to_string

TEMPLATE = "includes/_overlay_banner.html"

# One subpath from each mark, sufficient to identify it on the page.
CALENDAR_GRID = '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"'
CALENDAR_RINGS = ('x1="16" y1="2" x2="16" y2="6"', 'x1="8" y1="2" x2="8" y2="6"')
PIN_ARCS = (
    'd="M5.43 5.43A8.06 8.06 0 0 0 4 10c0 6 8 12 8 12a29.94 29.94 0 0 0 5-5"',
    'd="M19.18 13.52A8.66 8.66 0 0 0 20 10a8 8 0 0 0-8-8 7.88 7.88 0 0 0-3.52.82"',
    'd="M9.13 9.13A2.78 2.78 0 0 0 9 10a3 3 0 0 0 3 3 2.78 2.78 0 0 0 .87-.13"',
    'd="M14.9 9.25a3 3 0 0 0-2.15-2.16"',
)
PIN_STRIKE = 'x1="2" x2="22" y1="2" y2="22"'


def render_strip(**context: object) -> str:
    """Render the "strip" variant.

    Args:
        **context: Extra template context merged over the required keys.

    Returns:
        The rendered HTML.

    """
    return render_to_string(
        TEMPLATE,
        {"variant": "strip", "body": "Archive bulletins.", **context},
    )


def render_floating(**context: object) -> str:
    """Render the "floating" variant.

    Args:
        **context: Extra template context merged over the required keys.

    Returns:
        The rendered HTML.

    """
    return render_to_string(
        TEMPLATE,
        {"variant": "floating", "body": "Off the map.", **context},
    )


class TestCalendarIcon:
    """The mark the strip variant draws when a caller asks for it."""

    def test_the_whole_mark_reaches_the_page(self) -> None:
        """All four subpaths, not just the grid.

        The rings and the header rule are what make it read as a calendar
        rather than as a plain rectangle at 16px.
        """
        html = render_strip(icon="calendar")

        assert CALENDAR_GRID in html
        for ring in CALENDAR_RINGS:
            assert ring in html
        assert 'x1="3" y1="10" x2="21" y2="10"' in html

    def test_the_caller_keeps_its_shrink_guard(self) -> None:
        """``shrink-0`` survives the include as ``svg_class``.

        The strip is a flex row whose copy can run long; without this the
        glyph is squeezed to a sliver rather than the text wrapping.
        """
        assert 'class="shrink-0"' in render_strip(icon="calendar")

    def test_no_icon_draws_no_mark(self) -> None:
        """The parameter is optional, and omitting it is not an empty box."""
        assert CALENDAR_GRID not in render_strip()


class TestLocationOffIcon:
    """The off-map nudge's struck-through pin."""

    def test_all_five_subpaths_reach_the_page(self) -> None:
        """Four arcs plus the strike.

        Asserted in full because the pin is drawn as separate arcs so the
        strike passes between them rather than across a stroke. Losing one
        arc leaves a mark that still draws and no longer reads — which is
        exactly how the refresh glyph failed.
        """
        html = render_floating(icon="location-off")

        for arc in PIN_ARCS:
            assert arc in html
        assert PIN_STRIKE in html

    def test_it_sits_inside_the_roundel(self) -> None:
        """``data-overlay-icon`` is the hook an owning module animates."""
        assert "data-overlay-icon" in render_floating(icon="location-off")


class TestOneSourcePerMark:
    """The rule the ADR states, asserted rather than assumed."""

    def test_the_banner_draws_no_svg_of_its_own(self) -> None:
        """No ``<svg`` literal survives in the template source.

        This is the assertion that actually holds the line. Every check
        above passes just as well against a re-inlined copy of the path —
        only reading the source catches a mark that came back inline.
        """
        from pathlib import Path  # noqa: PLC0415 - test-local, not a hot path

        from django.conf import settings

        source = (
            Path(settings.BASE_DIR) / "templates" / "includes" / TEMPLATE.split("/")[-1]
        ).read_text(encoding="utf-8")

        assert "<svg" not in source

    def test_the_nav_shares_the_calendar_partial(self) -> None:
        """``nav.html`` includes the same partial rather than its own copy.

        The second copy is the reason this mark is a partial at all, so a
        test that only covered the banner would miss the half of the
        problem that motivated the extraction.
        """
        from pathlib import Path  # noqa: PLC0415 - test-local, not a hot path

        from django.conf import settings

        source = (
            Path(settings.BASE_DIR) / "templates" / "includes" / "nav.html"
        ).read_text(encoding="utf-8")

        assert 'include "includes/_icon_calendar.html"' in source
        assert CALENDAR_GRID not in source


class TestRenderedInTheNav:
    """The nav's season trigger still draws its mark after the extraction.

    The include passes ``size=14``, and a silently-failing include renders
    nothing at all rather than raising — so the mark is checked against a
    render rather than only in the template source.

    Rendered from ``nav.html`` with the trigger's own context. An earlier
    cut fetched ``/`` instead, which never carried a season trigger at all
    (``season_trigger`` is a bulletin-page parameter): the calendar it
    found there came from the off-season strip above the map, so the check
    went green while asserting nothing about the nav, and it broke when
    that strip was dropped.
    """

    def test_the_season_trigger_keeps_its_glyph(self) -> None:
        """The nav draws the calendar at its own 14px size."""
        html = render_to_string(
            "includes/nav.html",
            {"season_trigger": SimpleNamespace(season_label="25/26")},
        )

        assert CALENDAR_GRID in html
        assert 'width="14"' in html
