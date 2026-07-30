"""
tests/bulletins/services/test_render_model_prose_detection.py — Tests for
detect_prose_spatial (SNOW-263).

Covers:
  - True positives: cardinal directions, compass abbreviations, elevation
    tokens (number+m), treeline vocabulary, altitude/elevation keywords,
    aspect vocabulary.
  - True negatives: generic no-scope templates such as
    "Moist snow slides expected as the day warms." must return False.
  - HTML-stripping: tokens inside <p>…</p> tags are detected correctly.
"""

from __future__ import annotations

from apps.bulletins.services.render_model import detect_prose_spatial


class TestDetectProseSpatialTruePositives:
    """detect_prose_spatial returns True for prose that describes spatial scope."""

    def test_north_facing_slopes(self) -> None:
        """Cardinal direction 'north' triggers a True result."""
        assert detect_prose_spatial("Wet-snow slides on north-facing slopes.") is True

    def test_south_facing(self) -> None:
        """Cardinal direction 'south' is detected."""
        assert detect_prose_spatial("Risk elevated on south facing aspects.") is True

    def test_east_west_combination(self) -> None:
        """Multiple cardinals in one phrase are all detected."""
        assert (
            detect_prose_spatial("Exposed east and west flanks are affected.") is True
        )

    def test_elevation_number_m(self) -> None:
        """Number+m pattern (e.g. '2400 m') triggers detection."""
        assert (
            detect_prose_spatial("Between 2000 and 2400 m conditions are critical.")
            is True
        )

    def test_elevation_metres_spelled_out(self) -> None:
        """'metres' (British spelling) after a number is detected."""
        assert detect_prose_spatial("Above 1800 metres expect instability.") is True

    def test_elevation_meters_american(self) -> None:
        """'meters' (American spelling) after a number is detected."""
        assert detect_prose_spatial("Below 2200 meters hazard decreases.") is True

    def test_treeline_one_word(self) -> None:
        """'treeline' is a spatial token."""
        assert detect_prose_spatial("Hazard elevated below the treeline.") is True

    def test_tree_line_two_words(self) -> None:
        """'tree line' (two words) is detected."""
        assert detect_prose_spatial("Avoid terrain near the tree line.") is True

    def test_timberline(self) -> None:
        """'timberline' is detected as a spatial token."""
        assert detect_prose_spatial("Above the timberline risk is high.") is True

    def test_altitude_keyword(self) -> None:
        """'altitude' triggers detection."""
        assert detect_prose_spatial("Particularly at high altitude today.") is True

    def test_elevation_keyword(self) -> None:
        """'elevation' triggers detection."""
        assert detect_prose_spatial("Risk increases with elevation.") is True

    def test_elevations_plural(self) -> None:
        """'elevations' (plural) triggers detection."""
        assert detect_prose_spatial("Hazard present at all elevations.") is True

    def test_aspect_singular(self) -> None:
        """'aspect' triggers detection."""
        assert detect_prose_spatial("Particular aspect — check description.") is True

    def test_aspects_plural(self) -> None:
        """'aspects' triggers detection."""
        assert detect_prose_spatial("N and NE aspects are most affected.") is True

    def test_compass_abbreviation_ne(self) -> None:
        """Compound compass abbreviation 'NE' is detected."""
        assert detect_prose_spatial("NE-facing slopes are loaded.") is True

    def test_compass_abbreviation_sw(self) -> None:
        """Compound compass abbreviation 'SW' is detected."""
        assert detect_prose_spatial("SW aspects have softened.") is True

    def test_single_letter_n(self) -> None:
        """Standalone compass letter 'N' is detected."""
        assert detect_prose_spatial("N slopes remain dangerous.") is True

    def test_south_west_written_out(self) -> None:
        """'south-west' written with hyphen is detected via 'south'/'west'."""
        assert detect_prose_spatial("Exposed on south-west facing ridges.") is True

    def test_above_number(self) -> None:
        """'above' adjacent to a number is detected."""
        assert detect_prose_spatial("Above 2500 the hazard is considerable.") is True

    def test_below_number(self) -> None:
        """'below' adjacent to a number is detected."""
        assert detect_prose_spatial("Below 1500 risk is low.") is True

    def test_real_style_north_facing_between(self) -> None:
        """Representative real-style prose with multiple tokens returns True."""
        assert (
            detect_prose_spatial(
                "Wet-snow slides likely on steep north-facing slopes "
                "between approximately 2000 and 2400 m."
            )
            is True
        )


class TestDetectProseSpatialTrueNegatives:
    """detect_prose_spatial returns False for generic, scope-free prose."""

    def test_generic_moist_snow_template(self) -> None:
        """The canonical no-scope template must return False."""
        assert (
            detect_prose_spatial("Moist snow slides expected as the day warms.")
            is False
        )

    def test_wet_loose_snow_possible(self) -> None:
        """Another generic template without spatial tokens returns False."""
        assert detect_prose_spatial("Wet loose-snow avalanches are possible.") is False

    def test_empty_string(self) -> None:
        """Empty string returns False without error."""
        assert detect_prose_spatial("") is False

    def test_generic_warming_with_no_scope(self) -> None:
        """Warming-driven statement with no spatial reference returns False."""
        assert (
            detect_prose_spatial(
                "Natural avalanche activity possible as temperatures rise."
            )
            is False
        )

    def test_no_scope_general_hazard(self) -> None:
        """A general hazard statement with no location tokens returns False."""
        assert detect_prose_spatial("General instability in the snowpack.") is False


class TestDetectProseSpatialHtmlStripping:
    """detect_prose_spatial strips HTML before analysing the text."""

    def test_tokens_inside_p_tags(self) -> None:
        """Spatial tokens wrapped in <p> tags are still detected."""
        assert (
            detect_prose_spatial(
                "<p>Wet-snow slides likely on steep north-facing slopes "
                "between approximately 2000 and 2400 m.</p>"
            )
            is True
        )

    def test_generic_template_wrapped_in_p_tags(self) -> None:
        """Generic template inside <p> tags returns False — HTML does not fool the detector."""
        assert (
            detect_prose_spatial("<p>Moist snow slides expected as the day warms.</p>")
            is False
        )

    def test_tokens_in_nested_html(self) -> None:
        """Spatial tokens inside nested HTML elements are detected."""
        assert (
            detect_prose_spatial(
                "<div><p>Risk on <strong>north</strong> facing slopes.</p></div>"
            )
            is True
        )
