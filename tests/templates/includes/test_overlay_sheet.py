"""
tests/templates/includes/test_overlay_sheet.py — Unit tests for the
_overlay_sheet.html partial's bounded-height/scroll contract (SNOW-645).

Uses ``django.template.loader.render_to_string`` directly — no test client,
no database, no browser. This is the layer for the assertion this file
makes: "does the rendered class attribute carry the right Tailwind
utilities" is reachable from a template render, so it belongs here rather
than in ``tests/js`` (no JS drives this partial's own markup — the scroll
behaviour is CSS layout, which jsdom does not compute at all) or
``tests/e2e`` (a real box-layout scroll assertion would need a live
browser, and the e2e suite is capped at ~15 journey-level tests — SNOW-649).

Covers:

  - The sheet always carries the bounded ``max-h-[calc(100dvh-...)]``
    utility referencing both edge-inset tokens, in ``dvh`` not ``vh``.
  - ``scroll_body`` (default False, favourites/report's shape): the sheet
    itself is ``overflow-y-auto`` and does NOT switch to a flex column.
  - ``scroll_body=True`` (the downloads sheet's shape): the sheet becomes
    ``flex flex-col overflow-hidden`` instead — no ``overflow-y-auto`` on
    the sheet itself, since the caller's own body carves out its own
    scroll region.
  - ``static=True`` ignores ``scroll_body`` entirely (component-library
    demo shape — no positioning classes of any kind).
"""

from __future__ import annotations

from django.template.loader import render_to_string

TEMPLATE = "includes/_overlay_sheet.html"


def render(context: dict) -> str:
    """Render _overlay_sheet.html with the given context dict."""
    return render_to_string(TEMPLATE, context)


class TestBoundedHeight:
    """Every non-static render carries the viewport-minus-insets max-height."""

    def test_default_carries_bounded_max_height(self) -> None:
        html = render({"id": "favourite-sheet", "aria_label": "Favourite"})
        assert (
            "max-h-[calc(100dvh-var(--map-edge-inset-top)-var(--map-edge-inset-bottom))]"
            in html
        )

    def test_scroll_body_also_carries_bounded_max_height(self) -> None:
        html = render(
            {
                "id": "map-downloads-sheet",
                "aria_label": "Downloads",
                "scroll_body": True,
            }
        )
        assert (
            "max-h-[calc(100dvh-var(--map-edge-inset-top)-var(--map-edge-inset-bottom))]"
            in html
        )

    def test_uses_dvh_not_vh(self) -> None:
        """dvh, never a bare vh — vh ignores mobile browser chrome."""
        html = render({"id": "favourite-sheet", "aria_label": "Favourite"})
        assert "100dvh" in html
        assert "100vh" not in html

    def test_static_omits_the_bound_entirely(self) -> None:
        """static=True renders inline (component library) — no positioning at all."""
        html = render({"id": "sheet-demo", "aria_label": "Demo", "static": True})
        assert "max-h-[calc(100dvh" not in html


class TestScrollBodyDefault:
    """scroll_body defaults to False: the sheet itself scrolls as one block."""

    def test_default_is_overflow_y_auto(self) -> None:
        html = render({"id": "favourite-sheet", "aria_label": "Favourite"})
        assert "overflow-y-auto" in html

    def test_default_does_not_become_a_flex_column(self) -> None:
        html = render({"id": "favourite-sheet", "aria_label": "Favourite"})
        assert "flex flex-col overflow-hidden" not in html

    def test_explicit_false_matches_the_default(self) -> None:
        html_default = render({"id": "favourite-sheet", "aria_label": "Favourite"})
        html_explicit = render(
            {"id": "favourite-sheet", "aria_label": "Favourite", "scroll_body": False}
        )
        assert html_default == html_explicit


class TestScrollBodyTrue:
    """scroll_body=True (the downloads sheet): a pinned-header/footer flex column."""

    def test_becomes_flex_column_with_hidden_overflow(self) -> None:
        html = render(
            {
                "id": "map-downloads-sheet",
                "aria_label": "Downloads",
                "scroll_body": True,
            }
        )
        assert "flex flex-col overflow-hidden" in html

    def test_does_not_carry_overflow_y_auto_on_the_sheet_itself(self) -> None:
        """The caller's own body carves out the scroll region — not this element."""
        html = render(
            {
                "id": "map-downloads-sheet",
                "aria_label": "Downloads",
                "scroll_body": True,
            }
        )
        assert "overflow-y-auto" not in html

    def test_ignored_when_static(self) -> None:
        """static=True renders inline regardless of scroll_body — component library demo."""
        html = render(
            {
                "id": "sheet-demo",
                "aria_label": "Demo",
                "static": True,
                "scroll_body": True,
            }
        )
        assert "flex flex-col overflow-hidden" not in html
        assert "relative" in html
