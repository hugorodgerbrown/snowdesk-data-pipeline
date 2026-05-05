"""
tests/public/templatetags/test_card_tags.py — Tests for the card_tags
template filters not already covered by ``test_aspect_rose.py``.

Focuses on ``elevation_icon``: the falsy-input guards and the three
``bound_type`` branches (``LOWER``, ``UPPER``, ``BOTH``). The filter
duck-types its argument via ``getattr(elevation, "bound_type", None)``,
so a ``SimpleNamespace`` stub is used in lieu of the production
``ElevationBounds`` dataclass — keeping the tests self-contained and
free of cross-app imports.
"""

from __future__ import annotations

from types import SimpleNamespace

from public.templatetags.card_tags import elevation_icon


class TestElevationIconFalsyGuards:
    """The falsy-input guards short-circuit to an empty string."""

    def test_none_elevation_returns_empty(self) -> None:
        """``None`` elevation hits the leading guard."""
        assert elevation_icon(None) == ""  # type: ignore[arg-type]

    def test_falsy_elevation_returns_empty(self) -> None:
        """A falsy stand-in (e.g. an object with ``__bool__`` False) returns ""."""

        class FalsyElevation:
            def __bool__(self) -> bool:
                return False

        assert elevation_icon(FalsyElevation()) == ""  # type: ignore[arg-type]

    def test_missing_bound_type_returns_empty(self) -> None:
        """Truthy elevation without a ``bound_type`` attribute returns ""."""
        elevation = SimpleNamespace()  # no bound_type attribute
        assert elevation_icon(elevation) == ""  # type: ignore[arg-type]

    def test_none_bound_type_returns_empty(self) -> None:
        """Truthy elevation with ``bound_type=None`` returns ""."""
        elevation = SimpleNamespace(bound_type=None)
        assert elevation_icon(elevation) == ""  # type: ignore[arg-type]

    def test_empty_string_bound_type_returns_empty(self) -> None:
        """Truthy elevation with ``bound_type=""`` returns "" (the empty-bounds case)."""
        elevation = SimpleNamespace(bound_type="")
        assert elevation_icon(elevation) == ""  # type: ignore[arg-type]


class TestElevationIconLowerBranch:
    """The ``LOWER`` branch — shaded zone above the dashed line."""

    def test_returns_svg_with_above_lower_bound_aria(self) -> None:
        """``LOWER`` gives the ``"above lower bound"`` aria label."""
        result = elevation_icon(SimpleNamespace(bound_type="LOWER"))  # type: ignore[arg-type]
        assert "<svg " in result
        assert "</svg>" in result
        assert 'aria-label="Elevation: above lower bound"' in result

    def test_lower_has_single_dashed_line(self) -> None:
        """``LOWER`` draws exactly one dashed line."""
        result = elevation_icon(SimpleNamespace(bound_type="LOWER"))  # type: ignore[arg-type]
        assert result.count("<line ") == 1
        assert 'stroke-dasharray="2,2"' in result

    def test_lower_has_shading_rect(self) -> None:
        """``LOWER`` draws a shaded rect clipped to the mountain triangle."""
        result = elevation_icon(SimpleNamespace(bound_type="LOWER"))  # type: ignore[arg-type]
        assert "<rect " in result
        assert 'opacity="0.18"' in result
        assert "clip-path=" in result

    def test_lower_respects_custom_size(self) -> None:
        """A custom ``size`` argument is reflected in the SVG dimensions."""
        result = elevation_icon(SimpleNamespace(bound_type="LOWER"), size=48)  # type: ignore[arg-type]
        assert 'width="48"' in result
        assert 'height="48"' in result
        assert 'viewBox="0 0 48 48"' in result


class TestElevationIconUpperBranch:
    """The ``UPPER`` branch — shaded zone below the dashed line."""

    def test_returns_svg_with_below_upper_bound_aria(self) -> None:
        """``UPPER`` gives the ``"below upper bound"`` aria label."""
        result = elevation_icon(SimpleNamespace(bound_type="UPPER"))  # type: ignore[arg-type]
        assert "<svg " in result
        assert 'aria-label="Elevation: below upper bound"' in result

    def test_upper_has_single_dashed_line(self) -> None:
        """``UPPER`` draws exactly one dashed line."""
        result = elevation_icon(SimpleNamespace(bound_type="UPPER"))  # type: ignore[arg-type]
        assert result.count("<line ") == 1
        assert 'stroke-dasharray="2,2"' in result

    def test_upper_has_shading_rect(self) -> None:
        """``UPPER`` draws a shaded rect clipped to the mountain triangle."""
        result = elevation_icon(SimpleNamespace(bound_type="UPPER"))  # type: ignore[arg-type]
        assert "<rect " in result
        assert 'opacity="0.18"' in result
        assert "clip-path=" in result


class TestElevationIconBothBranch:
    """The ``BOTH`` branch — shaded band between two dashed lines."""

    def test_returns_svg_with_between_bounds_aria(self) -> None:
        """``BOTH`` gives the ``"between bounds"`` aria label."""
        result = elevation_icon(SimpleNamespace(bound_type="BOTH"))  # type: ignore[arg-type]
        assert "<svg " in result
        assert 'aria-label="Elevation: between bounds"' in result

    def test_both_has_two_dashed_lines(self) -> None:
        """``BOTH`` draws two dashed lines (band edges)."""
        result = elevation_icon(SimpleNamespace(bound_type="BOTH"))  # type: ignore[arg-type]
        assert result.count("<line ") == 2
        # Both dashed lines share the dasharray attribute.
        assert result.count('stroke-dasharray="2,2"') == 2

    def test_both_has_shading_rect(self) -> None:
        """``BOTH`` draws a shaded rect (the band) clipped to the triangle."""
        result = elevation_icon(SimpleNamespace(bound_type="BOTH"))  # type: ignore[arg-type]
        assert "<rect " in result
        assert 'opacity="0.18"' in result
        assert "clip-path=" in result
