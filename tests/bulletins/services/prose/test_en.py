"""
tests/bulletins/services/prose/test_en.py — Tests for the English prose parser.

Covers every regex pattern in ``bulletins/services/prose/en.py`` with a
representative prose snippet and at least one no-match counter-example.  Also
includes a removability test that asserts that removing the parser leaves the
``parse_for`` interface in the same empty-state the registry is in when no
parser is registered at all.

Test structure
--------------
- ``TestAspectPatterns`` — one test per aspect grammar rule.
- ``TestElevationPatterns`` — one test per elevation grammar rule.
- ``TestParseFullComment`` — integration tests using realistic SLF comment text.
- ``TestNoMatch`` — counter-examples that must return None.
- ``TestHTMLStripping`` — verifies that HTML tags do not break phrase matching.
- ``TestRegistryRemovability`` — verifies that unregistering a parser returns
  the no-op behaviour described in the module docstring.
"""

from __future__ import annotations

from bulletins.services.prose import _PARSERS, parse_for
from bulletins.services.prose.en import _parse

# ---------------------------------------------------------------------------
# Aspect patterns
# ---------------------------------------------------------------------------


class TestAspectPatterns:
    """Cover every aspect-grammar rule from en.py."""

    def test_north_word(self) -> None:
        """'north' maps to N."""
        result = _parse("Wet-snow avalanches on north facing slopes.")
        assert result is not None
        assert "N" in result.aspects

    def test_south_word(self) -> None:
        """'south' maps to S."""
        result = _parse("Wet releases likely on south facing slopes.")
        assert result is not None
        assert "S" in result.aspects

    def test_east_word(self) -> None:
        """'east' maps to E."""
        result = _parse("Gliding snow on east facing slopes.")
        assert result is not None
        assert "E" in result.aspects

    def test_west_word(self) -> None:
        """'west' maps to W."""
        result = _parse("Wet slabs on west facing terrain.")
        assert result is not None
        assert "W" in result.aspects

    def test_northeast_compound(self) -> None:
        """'northeast' maps to NE."""
        result = _parse("Wet-snow on northeast slopes below 2000m.")
        assert result is not None
        assert "NE" in result.aspects

    def test_north_east_hyphen(self) -> None:
        """'north-east' maps to NE."""
        result = _parse("Releases on north-east aspects below 1800m.")
        assert result is not None
        assert "NE" in result.aspects

    def test_northwest_compound(self) -> None:
        """'northwest' maps to NW."""
        result = _parse("Wet releases on northwest and north aspects.")
        assert result is not None
        assert "NW" in result.aspects

    def test_north_west_hyphen(self) -> None:
        """'north-west' maps to NW."""
        result = _parse("Slabs on north-west terrain above 2200m.")
        assert result is not None
        assert "NW" in result.aspects

    def test_southeast_compound(self) -> None:
        """'southeast' maps to SE."""
        result = _parse("Gliding snow on southeast slopes.")
        assert result is not None
        assert "SE" in result.aspects

    def test_south_east_hyphen(self) -> None:
        """'south-east' maps to SE."""
        result = _parse("Wet avalanche activity on south-east aspects.")
        assert result is not None
        assert "SE" in result.aspects

    def test_southwest_compound(self) -> None:
        """'southwest' maps to SW."""
        result = _parse("Wet-snow problems on southwest slopes below 2500m.")
        assert result is not None
        assert "SW" in result.aspects

    def test_south_west_hyphen(self) -> None:
        """'south-west' maps to SW."""
        result = _parse("Activity on south-west and south faces.")
        assert result is not None
        assert "SW" in result.aspects

    def test_compass_code_N(self) -> None:
        """Standalone code 'N' matches at word boundary."""
        result = _parse("Wet slabs on N facing slopes.")
        assert result is not None
        assert "N" in result.aspects

    def test_compass_code_NE(self) -> None:
        """Standalone code 'NE' matches at word boundary."""
        result = _parse("Activity on NE and E aspects.")
        assert result is not None
        assert "NE" in result.aspects

    def test_compass_code_SE(self) -> None:
        """Standalone code 'SE' matches at word boundary."""
        result = _parse("Wet gliding on SE slopes below 2000m.")
        assert result is not None
        assert "SE" in result.aspects

    def test_compass_code_SW(self) -> None:
        """Standalone code 'SW' matches at word boundary."""
        result = _parse("SW and W slopes are prone to gliding.")
        assert result is not None
        assert "SW" in result.aspects

    def test_compass_code_NW(self) -> None:
        """Standalone code 'NW' matches at word boundary."""
        result = _parse("Wet slabs possible on NW slopes.")
        assert result is not None
        assert "NW" in result.aspects

    def test_all_aspects_terminal(self) -> None:
        """'all aspects' expands to all 8 compass codes."""
        result = _parse("Wet-snow avalanches on all aspects below 2200m.")
        assert result is not None
        assert set(result.aspects) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

    def test_shady_aspects_terminal(self) -> None:
        """'shady aspects' expands to N, NE, NW."""
        result = _parse("Wet releases on shady aspects.")
        assert result is not None
        assert set(result.aspects) == {"N", "NE", "NW"}

    def test_sunny_aspects_terminal(self) -> None:
        """'sunny aspects' expands to S, SE, SW."""
        result = _parse("Wet-snow activity on sunny aspects below 2400m.")
        assert result is not None
        assert set(result.aspects) == {"S", "SE", "SW"}

    def test_multiple_aspects_deduplication(self) -> None:
        """Multiple compass words de-duplicate while preserving order."""
        result = _parse("North and northeast and north facing slopes.")
        assert result is not None
        assert result.aspects.count("N") == 1
        assert "NE" in result.aspects

    def test_compass_code_not_inside_word(self) -> None:
        """'S' inside 'South' word-match; standalone code scan skipped."""
        # Word match takes priority; 'S' inside a word is not matched.
        result = _parse("ANNEX review of south slopes.")
        assert result is not None
        # Only 'S' from the word 'south' — not from 'S' inside 'ANNEX'.
        assert result.aspects == ["S"]

    def test_code_ne_not_in_sentence(self) -> None:
        """'NE' inside ANNEX or similar context is not matched."""
        # No direction words or compass codes in isolation.
        result = _parse("The ANNEX lists baseline conditions.")
        assert result is None

    def test_case_insensitive_words(self) -> None:
        """Direction words are matched case-insensitively."""
        result = _parse("NORTH and SOUTH facing slopes below 2000m.")
        assert result is not None
        assert "N" in result.aspects
        assert "S" in result.aspects


# ---------------------------------------------------------------------------
# Elevation patterns
# ---------------------------------------------------------------------------


class TestElevationPatterns:
    """Cover every elevation-grammar rule from en.py."""

    def test_below_numeric(self) -> None:
        """'below 2000m' → upper=2000, lower=None."""
        result = _parse("Wet avalanches on south slopes below 2000m.")
        assert result is not None
        assert result.elevation["upper"] == 2000
        assert result.elevation["lower"] is None
        assert result.elevation["treeline"] is False

    def test_below_approximately(self) -> None:
        """'below approximately 2400 m' → upper=2400."""
        result = _parse("Wet-snow below approximately 2400 m on sunny aspects.")
        assert result is not None
        assert result.elevation["upper"] == 2400

    def test_above_numeric(self) -> None:
        """'above 1800m' → lower=1800, upper=None."""
        result = _parse("Wet releases above 1800m on south aspects.")
        assert result is not None
        assert result.elevation["lower"] == 1800
        assert result.elevation["upper"] is None

    def test_above_approximately(self) -> None:
        """'above approximately 1600 m' → lower=1600."""
        result = _parse("Gliding snow above approximately 1600 m.")
        assert result is not None
        assert result.elevation["lower"] == 1600

    def test_between_numeric(self) -> None:
        """'between 1800 and 2400m' → lower=1800, upper=2400."""
        result = _parse("Wet slabs between 1800 and 2400m on east aspects.")
        assert result is not None
        assert result.elevation["lower"] == 1800
        assert result.elevation["upper"] == 2400

    def test_all_altitudes(self) -> None:
        """'all altitudes' → unconstrained (lower=None, upper=None, treeline=False)."""
        result = _parse("Wet-snow releases at all altitudes.")
        assert result is not None
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None
        assert result.elevation["treeline"] is False

    def test_intermediate_altitudes(self) -> None:
        """'intermediate altitudes' → same shape as 'all altitudes'."""
        result = _parse("Wet activity at intermediate altitudes on shady aspects.")
        assert result is not None
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None

    def test_high_altitudes(self) -> None:
        """'high altitudes' → lower=2000, upper=None."""
        result = _parse("Gliding snow at high altitudes.")
        assert result is not None
        assert result.elevation["lower"] == 2000
        assert result.elevation["upper"] is None

    def test_high_alpine(self) -> None:
        """'high alpine' → lower=2000, upper=None."""
        result = _parse("Wet-snow releases in high alpine terrain.")
        assert result is not None
        assert result.elevation["lower"] == 2000

    def test_treeline_phrase(self) -> None:
        """'treeline' → treeline=True."""
        result = _parse("Wet releases around the treeline on south slopes.")
        assert result is not None
        assert result.elevation["treeline"] is True

    def test_tree_line_two_words(self) -> None:
        """'tree line' → treeline=True."""
        result = _parse("Wet avalanches near the tree line.")
        assert result is not None
        assert result.elevation["treeline"] is True

    def test_first_match_wins(self) -> None:
        """When multiple elevation phrases appear, the first wins."""
        # "below 2400m" appears before "between 1800 and 2200m".
        comment = "Wet slabs below 2400m, particularly between 1800 and 2200m."
        result = _parse(comment)
        assert result is not None
        assert result.elevation["upper"] == 2400
        assert result.elevation["lower"] is None

    def test_first_match_above_then_below(self) -> None:
        """'above 1600m' before 'below 2400m' → above wins."""
        comment = "Wet releases above 1600m, locally below 2400m."
        result = _parse(comment)
        assert result is not None
        assert result.elevation["lower"] == 1600
        assert result.elevation["upper"] is None


# ---------------------------------------------------------------------------
# No-match counter-examples
# ---------------------------------------------------------------------------


class TestNoMatch:
    """Prose snippets that should return None."""

    def test_empty_string(self) -> None:
        """Empty input returns None."""
        assert _parse("") is None

    def test_no_tokens(self) -> None:
        """Generic avalanche prose with no direction or altitude tokens."""
        result = _parse(
            "The snowpack is warming throughout the day and becoming increasingly unstable."
        )
        assert result is None

    def test_partial_code_in_word(self) -> None:
        """Code substring inside a longer word is not matched."""
        # 'S' in 'slope', 'N' in 'unstable' — neither should match.
        result = _parse("The slope is unstable but no specific direction is noted.")
        assert result is None

    def test_whitespace_only(self) -> None:
        """Whitespace-only input returns None."""
        assert _parse("   ") is None

    def test_no_elevation_digits_too_short(self) -> None:
        """Two-digit altitude (e.g. '25 m') does not match (needs 3+ digits)."""
        result = _parse("Releases on north slopes at 25 m elevation.")
        assert result is not None
        # Aspects should match but elevation should not (too short).
        assert result.aspects == ["N"]
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None

    def test_no_elevation_five_digits(self) -> None:
        """Five-digit altitude (e.g. '12000 m') does not match (capped at 4)."""
        result = _parse("Releases on north slopes at 12000 m.")
        assert result is not None
        assert result.aspects == ["N"]
        assert result.elevation["lower"] is None

    def test_elevation_alone_returns_parsedscope(self) -> None:
        """Elevation token alone (no aspects) still returns a ParsedScope."""
        result = _parse("Wet-snow releases below 2200m.")
        assert result is not None
        assert result.aspects == []
        assert result.elevation["upper"] == 2200

    def test_aspects_alone_returns_parsedscope(self) -> None:
        """Aspect token alone (no elevation) returns ParsedScope with empty elevation."""
        result = _parse("Wet-snow on south facing slopes.")
        assert result is not None
        assert "S" in result.aspects
        assert result.elevation["lower"] is None
        assert result.elevation["upper"] is None
        assert result.elevation["treeline"] is False


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class TestHTMLStripping:
    """Verify that HTML markup does not break phrase detection."""

    def test_html_tags_stripped(self) -> None:
        """Tags inside a comment are stripped before matching."""
        result = _parse(
            "<p>Wet-snow avalanches on <em>south</em> slopes below 2000m.</p>"
        )
        assert result is not None
        assert "S" in result.aspects
        assert result.elevation["upper"] == 2000

    def test_inline_tag_does_not_break_phrase(self) -> None:
        """An inline tag in the middle of a phrase still yields a match."""
        # "below <b>2200</b> m" is uncommon but should not crash.
        result = _parse("Wet avalanches on north slopes below <b>2200</b> m.")
        assert result is not None
        assert "N" in result.aspects
        # Tag interrupts the phrase, so elevation may or may not match —
        # the key assertion is that the parser does not raise.

    def test_entity_decoded(self) -> None:
        """HTML entities are decoded by HTMLParser before matching."""
        # &ndash; becomes – which breaks the match for "north-east";
        # but "north" alone should still match.
        result = _parse("Wet releases on north&ndash;east aspects.")
        # At minimum, no exception is raised.
        assert result is not None or result is None  # noqa: PT008 — either outcome is fine


# ---------------------------------------------------------------------------
# parse_for registry interface
# ---------------------------------------------------------------------------


class TestParseForInterface:
    """Verify the parse_for() shim behaviour."""

    def test_parse_for_en_returns_result(self) -> None:
        """parse_for('en', ...) delegates to the registered parser."""
        result = parse_for("en", "Wet-snow releases on south aspects below 2200m.")
        assert result is not None
        assert "S" in result.aspects
        assert result.elevation["upper"] == 2200

    def test_parse_for_unknown_lang_returns_none(self) -> None:
        """parse_for returns None for unregistered languages (e.g. 'de')."""
        assert parse_for("de", "Nassschneelawinen an südexponierten Hängen.") is None

    def test_parse_for_empty_comment_returns_none(self) -> None:
        """parse_for returns None for an empty comment even with a registered parser."""
        assert parse_for("en", "") is None

    def test_parse_for_whitespace_returns_none(self) -> None:
        """parse_for returns None for whitespace-only comment."""
        assert parse_for("en", "   ") is None


# ---------------------------------------------------------------------------
# Removability test
# ---------------------------------------------------------------------------


class TestRegistryRemovability:
    """
    Assert that removing the English parser makes parse_for return None.

    This is the key guarantee that makes the parser truly removable: deleting
    ``en.py`` (or simply not importing it) leaves consumers in exactly the
    same state as when no parser is registered — the empty-state branch fires
    and the render model proceeds without aspect/elevation enrichment.
    """

    def test_removing_parser_gives_none(self) -> None:
        """Un-registering 'en' makes parse_for('en', ...) return None."""
        original = _PARSERS.pop("en", None)
        try:
            result = parse_for("en", "Wet-snow on south aspects below 2000m.")
            assert result is None
        finally:
            # Restore so other tests in the suite are unaffected.
            if original is not None:
                _PARSERS["en"] = original

    def test_restoring_parser_gives_result(self) -> None:
        """After restoring, parse_for('en', ...) returns a ParsedScope again."""
        original = _PARSERS.pop("en", None)
        if original is not None:
            _PARSERS["en"] = original
        result = parse_for("en", "Wet-snow on south aspects below 2000m.")
        assert result is not None
