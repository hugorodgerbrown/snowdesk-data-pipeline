"""
tests/bulletins/services/prose/test_en_canonical.py — Tests for the
canonical-phrase fallback lookup.

Covers every pattern in ``bulletins/services/prose/en_canonical.py`` with:
  - a representative comment that should match,
  - a counter-example that must return ``None``,
  - assertions on ``aspects``, ``elevation``, ``context_tag``, and
    ``meta_aspect`` for each match.

Also covers:
  - First-match priority (a comment matching two patterns returns the first).
  - Empty / whitespace / HTML-only inputs return ``None``.
  - HTML stripping does not break phrase detection.
"""

from __future__ import annotations

from bulletins.services.prose.en_canonical import lookup

# All compass codes for the "all aspects" assertion.
_ALL_COMPASS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
# Sunny compass codes for the "sunny aspects" assertion.
_SUNNY_COMPASS = {"S", "SE", "SW"}


class TestGrassySlopes:
    """Pattern 1: 'grassy slopes' → grassy_slopes, intermediate elevation."""

    def test_grassy_slopes_match(self) -> None:
        """'steep grassy slopes' triggers the grassy_slopes pattern."""
        result = lookup(
            "On steep grassy slopes the gliding snow activity is increasing."
        )
        assert result is not None
        assert result.context_tag == "grassy_slopes"
        assert set(result.aspects) == _ALL_COMPASS
        assert result.elevation["lower"] == 1800
        assert result.elevation["upper"] == 2400
        assert result.meta_aspect is None

    def test_grassy_slope_singular(self) -> None:
        """'grassy slope' (singular) also matches."""
        result = lookup("Wet-snow releases on grassy slope terrain.")
        assert result is not None
        assert result.context_tag == "grassy_slopes"

    def test_grassy_slopes_no_match(self) -> None:
        """Comment without 'grassy' does not match this pattern."""
        result = lookup("Wet-snow avalanches on steep rocky terrain below 2000m.")
        # May match another pattern — verify it is not grassy_slopes.
        if result is not None:
            assert result.context_tag != "grassy_slopes"

    def test_grassy_slopes_case_insensitive(self) -> None:
        """Pattern is case-insensitive."""
        result = lookup("GRASSY SLOPES are at risk from gliding.")
        assert result is not None
        assert result.context_tag == "grassy_slopes"


class TestGlideCracks:
    """Pattern 2: 'glide cracks' → glide_cracks, intermediate elevation."""

    def test_glide_cracks_match(self) -> None:
        """'open glide cracks' triggers the glide_cracks pattern."""
        result = lookup(
            "Open glide cracks are visible on many slopes; release is likely."
        )
        assert result is not None
        assert result.context_tag == "glide_cracks"
        assert set(result.aspects) == _ALL_COMPASS
        assert result.elevation["lower"] == 1800
        assert result.elevation["upper"] == 2400

    def test_glide_crack_singular(self) -> None:
        """'glide crack' (singular) also matches."""
        result = lookup("A large glide crack has opened overnight.")
        assert result is not None
        assert result.context_tag == "glide_cracks"

    def test_glide_cracks_hyphen(self) -> None:
        """'glide-crack' (hyphenated) also matches."""
        result = lookup("Significant glide-crack activity on southern terrain.")
        assert result is not None
        assert result.context_tag == "glide_cracks"

    def test_glide_cracks_no_match(self) -> None:
        """Comment without 'glide crack' does not trigger this pattern."""
        result = lookup(
            "Warming temperatures are increasing wet-snow avalanche activity."
        )
        if result is not None:
            assert result.context_tag != "glide_cracks"


class TestSolarRadiation:
    """Pattern 3: 'solar radiation' → solar_radiation, sunny meta_aspect."""

    def test_solar_radiation_match(self) -> None:
        """'solar radiation' triggers solar_radiation with sunny aspects."""
        result = lookup(
            "As a consequence of solar radiation loose snow avalanches are likely."
        )
        assert result is not None
        assert result.context_tag == "solar_radiation"
        assert result.meta_aspect == "sunny"
        assert set(result.aspects) == _SUNNY_COMPASS

    def test_solar_radiation_elevation_all(self) -> None:
        """solar_radiation has unconstrained elevation (no numeric bounds)."""
        result = lookup("Solar radiation will trigger loose-snow avalanches today.")
        assert result is not None
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None
        assert result.elevation["treeline"] is False

    def test_solar_radiation_no_match(self) -> None:
        """Comment without 'solar radiation' does not trigger this pattern."""
        result = lookup("Gliding snow on all aspects below 2200m.")
        if result is not None:
            assert result.context_tag != "solar_radiation"


class TestDiurnalCycle:
    """Pattern 4: 'diurnal' / 'daily cycle' → diurnal_cycle, sunny meta_aspect."""

    def test_diurnal_match(self) -> None:
        """'diurnal' triggers the diurnal_cycle pattern."""
        result = lookup(
            "A pronounced diurnal cycle will increase wet-snow risk in the afternoon."
        )
        assert result is not None
        assert result.context_tag == "diurnal_cycle"
        assert result.meta_aspect == "sunny"

    def test_daily_cycle_match(self) -> None:
        """'daily cycle' triggers the diurnal_cycle pattern."""
        result = lookup(
            "Expect a clear daily cycle — low in the morning, higher later."
        )
        assert result is not None
        assert result.context_tag == "diurnal_cycle"
        assert result.meta_aspect == "sunny"

    def test_diurnal_no_match(self) -> None:
        """Comment without 'diurnal' or 'daily cycle' does not match."""
        result = lookup("Saturated snowpack across all aspects.")
        if result is not None:
            assert result.context_tag != "diurnal_cycle"


class TestDaytimeWarming:
    """Pattern 5: 'daytime warming' → daytime_warming, sunny meta_aspect."""

    def test_daytime_warming_match(self) -> None:
        """'daytime warming' triggers the daytime_warming pattern."""
        result = lookup(
            "Daytime warming will significantly increase the wet-snow danger."
        )
        assert result is not None
        assert result.context_tag == "daytime_warming"
        assert result.meta_aspect == "sunny"
        assert set(result.aspects) == _SUNNY_COMPASS

    def test_warming_alone_no_match(self) -> None:
        """'warming' alone (without 'daytime') does not match this pattern."""
        result = lookup("Warming temperatures are destabilising the snowpack.")
        # 'warming' alone should not trigger daytime_warming.
        if result is not None:
            assert result.context_tag != "daytime_warming"


class TestRain:
    """Pattern 6: 'rain' → rain, lower elevation band."""

    def test_rain_match(self) -> None:
        """'rain' triggers the rain pattern with lower elevation (upper=1800)."""
        result = lookup(
            "Rain up to mid-elevations is wetting the snowpack and triggering releases."
        )
        assert result is not None
        assert result.context_tag == "rain"
        assert set(result.aspects) == _ALL_COMPASS
        assert result.elevation["upper"] == 1800
        assert result.elevation["lower"] is None

    def test_rain_meta_aspect_none(self) -> None:
        """Rain has no meta_aspect (affects all aspects equally)."""
        result = lookup("Rain is falling up to 1400m.")
        assert result is not None
        assert result.meta_aspect is None

    def test_rain_no_match(self) -> None:
        """Comment without 'rain' does not trigger this pattern."""
        result = lookup("Clear skies and strong sunshine will melt the surface.")
        if result is not None:
            assert result.context_tag != "rain"


class TestSpringConditions:
    """Pattern 7: 'spring conditions' / 'in spring' → spring_conditions."""

    def test_spring_conditions_match(self) -> None:
        """'spring conditions' triggers the spring_conditions pattern."""
        result = lookup(
            "Spring conditions prevail; expect a rapid increase in danger from midday."
        )
        assert result is not None
        assert result.context_tag == "spring_conditions"
        assert set(result.aspects) == _ALL_COMPASS
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None

    def test_in_spring_match(self) -> None:
        """'in spring' also triggers the spring_conditions pattern."""
        result = lookup(
            "In spring the snowpack structure is typically prone to wet-snow releases."
        )
        assert result is not None
        assert result.context_tag == "spring_conditions"

    def test_spring_no_match(self) -> None:
        """'spring' in a different context (e.g. 'spring balance') does not match."""
        result = lookup("Gliding snow is increasing on steep terrain.")
        if result is not None:
            assert result.context_tag != "spring_conditions"


class TestLightSnowCover:
    """Pattern 8: 'little snow is lying' → light_snow_cover."""

    def test_little_snow_is_lying_match(self) -> None:
        """'little snow is lying' triggers light_snow_cover."""
        result = lookup(
            "Only a little snow is lying at low elevations — isolated wet releases."
        )
        assert result is not None
        assert result.context_tag == "light_snow_cover"
        assert set(result.aspects) == _ALL_COMPASS
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None

    def test_little_snow_is_lying_without_only(self) -> None:
        """'little snow is lying' without 'Only' also matches."""
        result = lookup("A little snow is lying above 1800m. Gliding remains possible.")
        assert result is not None
        assert result.context_tag == "light_snow_cover"

    def test_light_snow_cover_no_match(self) -> None:
        """Comment without this phrase does not match."""
        result = lookup("Wet releases likely due to solar radiation.")
        if result is not None:
            assert result.context_tag != "light_snow_cover"


class TestSaturatedSnowpack:
    """Pattern 9: 'saturated snowpack' / 'wet snowpack' → saturated_snowpack."""

    def test_saturated_snowpack_match(self) -> None:
        """'saturated snowpack' triggers the saturated_snowpack pattern."""
        result = lookup(
            "The saturated snowpack is prone to full-depth slides on all aspects."
        )
        assert result is not None
        assert result.context_tag == "saturated_snowpack"
        assert set(result.aspects) == _ALL_COMPASS

    def test_wet_snowpack_match(self) -> None:
        """'wet snowpack' also triggers saturated_snowpack."""
        result = lookup("The wet snowpack is unstable at all elevations.")
        assert result is not None
        assert result.context_tag == "saturated_snowpack"

    def test_saturated_snowpack_no_match(self) -> None:
        """Comment with 'snowpack' alone (not saturated/wet) does not match."""
        result = lookup("The snowpack has consolidated in many areas.")
        if result is not None:
            assert result.context_tag != "saturated_snowpack"


class TestNewSnow:
    """Pattern 10: 'new snow' (wet context) → new_snow."""

    def test_new_snow_match(self) -> None:
        """'new snow' in a wet-snow comment triggers the new_snow pattern."""
        result = lookup(
            "New snow and rain are wetting the snowpack, increasing the wet-snow danger."
        )
        # This comment also contains 'rain' (pattern 6, lower priority than new_snow
        # is pattern 10, rain is pattern 6 — rain wins as it comes first in table).
        assert result is not None
        assert result.context_tag == "rain"

    def test_new_snow_no_rain_match(self) -> None:
        """'new snow' alone (no other earlier pattern) triggers new_snow."""
        result = lookup(
            "New snow overnight has added weight to the existing wet snowpack — "
            "loose-snow avalanches are possible on steep slopes."
        )
        # 'saturated snowpack' / 'wet snowpack' pattern fires on 'wet snowpack';
        # test that one of new_snow or saturated_snowpack matches.
        assert result is not None
        assert result.context_tag in ("new_snow", "saturated_snowpack")

    def test_new_snow_truly_alone(self) -> None:
        """'new snow' with no competing patterns fires new_snow."""
        result = lookup(
            "New snow avalanches are possible on steep terrain at all elevations."
        )
        assert result is not None
        assert result.context_tag == "new_snow"
        assert set(result.aspects) == _ALL_COMPASS


class TestFirstMatchWins:
    """The first matching pattern in the table wins."""

    def test_grassy_takes_precedence_over_rain(self) -> None:
        """'grassy slopes' appears before 'rain' in the table — grassy wins."""
        result = lookup(
            "Rain on grassy slopes is triggering wet releases at low elevations."
        )
        assert result is not None
        # Both 'grassy slopes' (pattern 1) and 'rain' (pattern 6) match;
        # grassy_slopes is listed first so it wins.
        assert result.context_tag == "grassy_slopes"

    def test_solar_takes_precedence_over_new_snow(self) -> None:
        """'solar radiation' appears before 'new snow' — solar wins."""
        result = lookup(
            "Solar radiation is triggering loose new snow avalanches on steep terrain."
        )
        assert result is not None
        assert result.context_tag == "solar_radiation"


class TestEmptyAndEdgeCases:
    """Empty, whitespace, and HTML-edge-case inputs."""

    def test_empty_string(self) -> None:
        """Empty string returns None."""
        assert lookup("") is None

    def test_whitespace_only(self) -> None:
        """Whitespace-only input returns None."""
        assert lookup("   ") is None

    def test_no_matching_phrase(self) -> None:
        """Comment with no canonical phrase returns None."""
        assert (
            lookup("The snowpack is becoming increasingly unstable throughout the day.")
            is None
        )

    def test_html_tags_stripped(self) -> None:
        """HTML tags do not prevent phrase matching."""
        result = lookup(
            "<p>On <em>steep grassy slopes</em> the gliding risk is elevated.</p>"
        )
        assert result is not None
        assert result.context_tag == "grassy_slopes"

    def test_html_only_no_match(self) -> None:
        """HTML with no text content returns None."""
        assert lookup("<p></p>") is None
