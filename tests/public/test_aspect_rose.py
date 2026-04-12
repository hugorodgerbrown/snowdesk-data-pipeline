"""
tests/public/test_aspect_rose.py — Tests for the aspect_rose template filter.

Covers the ``aspect_rose`` filter in ``public.templatetags.card_tags``
and its helper ``_wedge``.
"""

from __future__ import annotations

from public.templatetags.card_tags import _wedge, aspect_rose


class TestWedge:
    """Tests for the ``_wedge`` SVG path helper."""

    def test_returns_path_element(self) -> None:
        """Output is an SVG <path> element."""
        result = _wedge(18, 18, 16, -90, active=True)
        assert result.startswith("<path ")
        assert result.endswith("/>")

    def test_active_fill_colour(self) -> None:
        """Active wedges use the amber highlight colour."""
        result = _wedge(18, 18, 16, 0, active=True)
        assert 'fill="#BA7517"' in result

    def test_inactive_fill_colour(self) -> None:
        """Inactive wedges use the hardcoded warm grey."""
        result = _wedge(18, 18, 16, 0, active=False)
        assert 'fill="#E8E6E0"' in result


class TestAspectRose:
    """Tests for the ``aspect_rose`` template filter."""

    def test_returns_svg_element(self) -> None:
        """Output is a complete SVG element."""
        result = aspect_rose(["N", "E"])
        assert "<svg " in result
        assert "</svg>" in result

    def test_default_size_is_36(self) -> None:
        """Without a size argument the SVG is 36x36."""
        result = aspect_rose(["N"])
        assert 'width="36"' in result
        assert 'height="36"' in result

    def test_custom_size(self) -> None:
        """The size argument controls width and height."""
        result = aspect_rose(["N"], size=24)
        assert 'width="24"' in result
        assert 'height="24"' in result

    def test_active_aspects_highlighted(self) -> None:
        """Active aspects get the amber fill; inactive get warm grey."""
        result = aspect_rose(["N", "S"])
        assert result.count('fill="#BA7517"') == 2
        assert result.count('fill="#E8E6E0"') == 6

    def test_all_aspects_active(self) -> None:
        """All eight aspects active → eight amber wedges."""
        all_aspects = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        result = aspect_rose(all_aspects)
        assert result.count('fill="#BA7517"') == 8
        assert 'fill="#E8E6E0"' not in result

    def test_no_aspects_active(self) -> None:
        """Empty list → all eight wedges inactive."""
        result = aspect_rose([])
        assert result.count('fill="#E8E6E0"') == 8
        assert 'fill="#BA7517"' not in result

    def test_none_aspects_treated_as_empty(self) -> None:
        """None is treated as no active aspects."""
        result = aspect_rose(None)
        assert result.count('fill="#E8E6E0"') == 8

    def test_aria_label_lists_sorted_aspects(self) -> None:
        """The aria-label lists active aspects alphabetically."""
        result = aspect_rose(["SW", "N", "E"])
        assert 'aria-label="Aspects: E, N, SW"' in result

    def test_aria_label_empty_aspects(self) -> None:
        """No active aspects → aria-label says "none"."""
        result = aspect_rose([])
        assert 'aria-label="Aspects: none"' in result

    def test_centre_dot_is_white(self) -> None:
        """The centre circle uses hardcoded white fill."""
        result = aspect_rose(["N"])
        assert "<circle " in result
        assert 'fill="#FFFFFF"' in result

    def test_no_north_label(self) -> None:
        """The rose does not include a text label for north."""
        result = aspect_rose(["N"])
        assert "<text" not in result

    def test_result_is_marked_safe(self) -> None:
        """The returned string is marked as safe for Django templates."""
        result = aspect_rose(["N"])
        assert hasattr(result, "__html__")
