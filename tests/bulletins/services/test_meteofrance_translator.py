"""
tests/bulletins/services/test_meteofrance_translator.py — Tests for the DPBRA XML translator.

Covers:
  - parse_dpbra_xml: two-band and single-band danger ratings, avalanche
    problems, tendency, custom data, UTC conversion, bulletinID format.
  - MeteoFranceDelegatedRegionError raised for non-BULLETINS_NEIGE_AVALANCHE root.
  - MeteoFranceTranslationError for missing required elements and invalid values.
  - Internal helpers: _parse_local_to_utc, _parse_local_date, _parse_plus_one_day,
    _aspects_from_pente, _elevation_from_prose, _evolution_from_levels.

All tests are pure (no database, no network): XML bytes are either
constructed inline or loaded from the committed sample data directory.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405
from pathlib import Path

import pytest

from bulletins.services.meteofrance_translator import (
    MeteoFranceDelegatedRegionError,
    MeteoFranceTranslationError,
    _aspects_from_pente,
    _elevation_from_prose,
    _evolution_from_levels,
    _parse_local_date,
    _parse_local_to_utc,
    _parse_plus_one_day,
    parse_dpbra_xml,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "research"
    / "meteofrance"
    / "bulletins-2026-05-18"
)


def _sample(filename: str) -> bytes:
    """Load a sample DPBRA XML file from the research directory."""
    return (_SAMPLE_DIR / filename).read_bytes()


def _minimal_dpbra(
    massif_id: int = 1,
    massif_name: str = "TestMassif",
    risque1: int = 2,
    risque2: str = "",
    altitude: str = "",
    risque_maxi_j2: int = 2,
    date_bulletin: str = "2026-05-17T16:00:00",
    date_validite: str = "2026-05-18T18:00:00",
    date_diffusion: str = "2026-05-17T16:02:00",
) -> bytes:
    """
    Build a minimal valid DPBRA XML document for unit-testing edge cases.

    Only the sections that ``parse_dpbra_xml`` strictly requires are
    included. SAT codes default to 4 (wet_snow) for a single problem.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<BULLETINS_NEIGE_AVALANCHE ID="{massif_id}" MASSIF="{massif_name}"
    DATEBULLETIN="{date_bulletin}"
    DATEECHEANCE="{date_validite}"
    DATEVALIDITE="{date_validite}"
    DATEDIFFUSION="{date_diffusion}"
    AMENDEMENT="false">
  <CARTOUCHERISQUE>
    <RISQUE RISQUE1="{risque1}" ALTITUDE="{altitude}" RISQUE2="{risque2}"
            RISQUEMAXI="{risque1}" RISQUEMAXIJ2="{risque_maxi_j2}"
            DATE_RISQUE_J2="2026-05-19T00:00:00"/>
    <PENTE NE="false" E="false" SE="false" S="false"
           SW="false" W="false" NW="false" N="false"/>
    <ACCIDENTEL/>
    <NATUREL/>
  </CARTOUCHERISQUE>
  <STABILITE>
    <SitAvalTyp SAT1="4" SAT2=""/>
    <TITRE/>
    <TEXTESANSTITRE/>
    <TEXTE/>
  </STABILITE>
  <QUALITE><TEXTE/></QUALITE>
</BULLETINS_NEIGE_AVALANCHE>""".encode()


# ---------------------------------------------------------------------------
# _parse_local_to_utc
# ---------------------------------------------------------------------------


class TestParseLocalToUtc:
    """_parse_local_to_utc converts Paris local-time strings to UTC Z strings."""

    def test_standard_datetime_converts_to_utc(self) -> None:
        """A standard Paris datetime converts correctly to UTC."""
        # 2026-01-01T12:00:00 Paris = 2026-01-01T11:00:00Z (UTC+1 in winter)
        result = _parse_local_to_utc("2026-01-01T12:00:00")
        assert result == "2026-01-01T11:00:00Z"

    def test_summer_time_converts_to_utc(self) -> None:
        """A Paris summer datetime (UTC+2) converts correctly to UTC."""
        # 2026-05-18T16:00:00 Paris = 2026-05-18T14:00:00Z (UTC+2 in summer)
        result = _parse_local_to_utc("2026-05-18T16:00:00")
        assert result == "2026-05-18T14:00:00Z"

    def test_result_ends_with_z(self) -> None:
        """The output always ends with Z (not +00:00)."""
        result = _parse_local_to_utc("2026-05-17T16:00:00")
        assert result.endswith("Z")

    def test_raises_on_invalid_string(self) -> None:
        """A non-datetime string raises MeteoFranceTranslationError."""
        with pytest.raises(MeteoFranceTranslationError, match="Cannot parse datetime"):
            _parse_local_to_utc("not-a-date")


# ---------------------------------------------------------------------------
# _parse_local_date
# ---------------------------------------------------------------------------


class TestParseLocalDate:
    """_parse_local_date extracts the Paris-local calendar date."""

    def test_extracts_date(self) -> None:
        """A valid datetime string returns the local date component."""
        from datetime import date

        result = _parse_local_date("2026-05-18T00:00:00")
        assert result == date(2026, 5, 18)

    def test_raises_on_invalid_string(self) -> None:
        """An invalid string raises MeteoFranceTranslationError."""
        with pytest.raises(MeteoFranceTranslationError):
            _parse_local_date("bad")


# ---------------------------------------------------------------------------
# _parse_plus_one_day
# ---------------------------------------------------------------------------


class TestParsePlusOneDay:
    """_parse_plus_one_day advances a Paris datetime by one calendar day."""

    def test_advances_by_one_day(self) -> None:
        """Result is one calendar day later than the input, in UTC."""
        result = _parse_plus_one_day("2026-05-18T00:00:00")
        # 2026-05-19T00:00:00 Paris CEST (UTC+2) = 2026-05-18T22:00:00Z
        assert result == "2026-05-18T22:00:00Z"

    def test_raises_on_invalid_string(self) -> None:
        """An invalid string raises MeteoFranceTranslationError."""
        with pytest.raises(MeteoFranceTranslationError):
            _parse_plus_one_day("garbage")


# ---------------------------------------------------------------------------
# _aspects_from_pente
# ---------------------------------------------------------------------------


class TestAspectsFromPente:
    """_aspects_from_pente extracts aspects from a <PENTE> element."""

    def test_returns_true_aspects_only(self) -> None:
        """Only compass attributes with value 'true' are returned."""
        xml = '<PENTE NE="true" E="false" SE="true" S="false" SW="false" W="false" NW="false" N="true"/>'
        el = ET.fromstring(xml)  # noqa: S314
        result = _aspects_from_pente(el)
        assert sorted(result) == ["N", "NE", "SE"]

    def test_empty_when_all_false(self) -> None:
        """All-false rose returns an empty list."""
        xml = '<PENTE NE="false" E="false" SE="false" S="false" SW="false" W="false" NW="false" N="false"/>'
        el = ET.fromstring(xml)  # noqa: S314
        assert _aspects_from_pente(el) == []

    def test_all_aspects_when_all_true(self) -> None:
        """All-true rose returns all 8 CAAML compass tokens."""
        xml = '<PENTE NE="true" E="true" SE="true" S="true" SW="true" W="true" NW="true" N="true"/>'
        el = ET.fromstring(xml)  # noqa: S314
        assert len(_aspects_from_pente(el)) == 8


# ---------------------------------------------------------------------------
# _elevation_from_prose
# ---------------------------------------------------------------------------


class TestElevationFromProse:
    """_elevation_from_prose parses French altitude-range phrases."""

    def test_au_dessus_returns_lower_bound(self) -> None:
        """'Au-dessus de 2400 m' → lowerBound: '2400'."""
        result = _elevation_from_prose("Au-dessus de 2400 m", None)
        assert result == {"lowerBound": "2400"}

    def test_en_dessous_returns_upper_bound(self) -> None:
        """'En dessous de 1800 m' → upperBound: '1800'."""
        result = _elevation_from_prose("En dessous de 1800 m", None)
        assert result == {"upperBound": "1800"}

    def test_entre_returns_both_bounds(self) -> None:
        """'Entre 1800 et 2400 m' → lowerBound + upperBound."""
        result = _elevation_from_prose("Entre 1800 et 2400 m", None)
        assert result == {"lowerBound": "1800", "upperBound": "2400"}

    def test_falls_back_to_bulletin_split(self) -> None:
        """No match in prose → falls back to bulletin-wide split as lowerBound."""
        result = _elevation_from_prose("texte sans altitude", 2200)
        assert result == {"lowerBound": "2200"}

    def test_returns_none_when_no_data(self) -> None:
        """No prose match and no bulletin split → None."""
        assert _elevation_from_prose(None, None) is None

    def test_prose_beats_bulletin_split(self) -> None:
        """Prose match overrides the bulletin split fallback."""
        result = _elevation_from_prose(
            "Au-dessus de 1600 m dans les versants nord", 2400
        )
        assert result == {"lowerBound": "1600"}


# ---------------------------------------------------------------------------
# _evolution_from_levels
# ---------------------------------------------------------------------------


class TestEvolutionFromLevels:
    """_evolution_from_levels derives tendency from danger level comparison."""

    def test_higher_tomorrow_is_increasing(self) -> None:
        """Tomorrow > today → 'increasing'."""
        assert _evolution_from_levels(2, 3) == "increasing"

    def test_lower_tomorrow_is_decreasing(self) -> None:
        """Tomorrow < today → 'decreasing'."""
        assert _evolution_from_levels(3, 2) == "decreasing"

    def test_same_levels_is_steady(self) -> None:
        """Same level → 'steady'."""
        assert _evolution_from_levels(2, 2) == "steady"


# ---------------------------------------------------------------------------
# parse_dpbra_xml — delegated region
# ---------------------------------------------------------------------------


class TestParseDpbraXmlDelegated:
    """parse_dpbra_xml raises MeteoFranceDelegatedRegionError for delegation docs."""

    def test_real_andorre_sample(self) -> None:
        """The real massif-071 delegation XML triggers the delegated error."""
        xml_bytes = _sample("massif-071.xml")
        with pytest.raises(MeteoFranceDelegatedRegionError):
            parse_dpbra_xml(xml_bytes)

    def test_any_non_bna_root_tag(self) -> None:
        """Any root element other than BULLETINS_NEIGE_AVALANCHE is delegated."""
        xml_bytes = (
            b"<?xml version='1.0'?><delegation><text>foreign</text></delegation>"
        )
        with pytest.raises(MeteoFranceDelegatedRegionError):
            parse_dpbra_xml(xml_bytes)


# ---------------------------------------------------------------------------
# parse_dpbra_xml — parse errors
# ---------------------------------------------------------------------------


class TestParseDpbraXmlErrors:
    """parse_dpbra_xml raises MeteoFranceTranslationError for bad input."""

    def test_raises_on_invalid_xml(self) -> None:
        """Malformed XML raises MeteoFranceTranslationError."""
        with pytest.raises(MeteoFranceTranslationError, match="XML parse error"):
            parse_dpbra_xml(b"<not valid xml<<")

    def test_raises_on_missing_cartoucherisque(self) -> None:
        """Missing <CARTOUCHERISQUE> raises MeteoFranceTranslationError."""
        xml_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<BULLETINS_NEIGE_AVALANCHE ID="1" MASSIF="X"
    DATEBULLETIN="2026-05-17T16:00:00"
    DATEECHEANCE="2026-05-18T18:00:00"
    DATEVALIDITE="2026-05-18T18:00:00"
    DATEDIFFUSION="2026-05-17T16:02:00"
    AMENDEMENT="false"/>"""
        with pytest.raises(MeteoFranceTranslationError, match="CARTOUCHERISQUE"):
            parse_dpbra_xml(xml_bytes)

    def test_raises_on_invalid_risque_level(self) -> None:
        """An out-of-range RISQUE1 value raises MeteoFranceTranslationError."""
        xml_bytes = _minimal_dpbra(risque1=9)
        with pytest.raises(MeteoFranceTranslationError, match="out of range"):
            parse_dpbra_xml(xml_bytes)


# ---------------------------------------------------------------------------
# parse_dpbra_xml — two-band bulletin (massif-001, 2026-05-18)
# ---------------------------------------------------------------------------


class TestParseTwoBandBulletin:
    """parse_dpbra_xml correctly handles the two-band massif-001 sample."""

    @pytest.fixture()
    def result(self) -> dict:
        """Parse the massif-001 sample once and return the CAAML dict."""
        return parse_dpbra_xml(_sample("massif-001.xml"))

    def test_bulletin_id_format(self, result: dict) -> None:
        """bulletinID is 'FR-{NN}-{YYYY-MM-DD}' (Paris-local date)."""
        assert result["bulletinID"] == "FR-01-2026-05-18"

    def test_lang_is_fr(self, result: dict) -> None:
        """Language is always 'fr' for MeteoFrance bulletins."""
        assert result["lang"] == "fr"

    def test_valid_time_start_in_utc(self, result: dict) -> None:
        """validTime.startTime is a UTC Z-string."""
        start = result["validTime"]["startTime"]
        assert start.endswith("Z")
        # 2026-05-17T16:00:00 Paris CEST (UTC+2) → 14:00:00Z
        assert start == "2026-05-17T14:00:00Z"

    def test_valid_time_end_in_utc(self, result: dict) -> None:
        """validTime.endTime is a UTC Z-string."""
        end = result["validTime"]["endTime"]
        assert end.endswith("Z")

    def test_two_danger_ratings(self, result: dict) -> None:
        """Two dangerRatings are returned for a two-band bulletin."""
        ratings = result["dangerRatings"]
        assert len(ratings) == 2

    def test_lower_band_danger(self, result: dict) -> None:
        """The first rating (below split) uses RISQUE1 ('low') with upperBound."""
        lower = result["dangerRatings"][0]
        assert lower["mainValue"] == "low"
        assert lower["elevation"] == {"upperBound": "2400"}

    def test_upper_band_danger(self, result: dict) -> None:
        """The second rating (above split) uses RISQUE2 ('moderate') with lowerBound."""
        upper = result["dangerRatings"][1]
        assert upper["mainValue"] == "moderate"
        assert upper["elevation"] == {"lowerBound": "2400"}

    def test_regions_list(self, result: dict) -> None:
        """Regions list has exactly one entry with regionID 'FR-01'."""
        regions = result["regions"]
        assert len(regions) == 1
        assert regions[0]["regionID"] == "FR-01"
        assert regions[0]["name"] == "Chablais"

    def test_avalanche_problems_present(self, result: dict) -> None:
        """At least one avalanche problem is extracted."""
        assert len(result["avalancheProblems"]) >= 1

    def test_problem_has_problem_type(self, result: dict) -> None:
        """Each avalanche problem has a problemType field."""
        for problem in result["avalancheProblems"]:
            assert "problemType" in problem

    def test_tendency_present(self, result: dict) -> None:
        """Tendency list is present with at least one entry."""
        assert "tendency" in result
        tendency = result["tendency"]
        assert isinstance(tendency, list)
        assert len(tendency) >= 1
        assert "tendencyType" in tendency[0]
        assert "validTime" in tendency[0]

    def test_custom_data_mf_present(self, result: dict) -> None:
        """customData.MF is populated with the mfInternalId."""
        mf = result["customData"]["MF"]
        assert mf["mfInternalId"] == 1
        assert mf["amendment"] is False

    def test_custom_data_j2_outlook(self, result: dict) -> None:
        """customData.MF.j2Outlook is populated when RISQUEMAXIJ2 is present."""
        j2 = result["customData"]["MF"]["j2Outlook"]
        assert j2 is not None
        assert "maxDanger" in j2


# ---------------------------------------------------------------------------
# parse_dpbra_xml — single-band bulletin (massif-064, 2026-05-18)
# ---------------------------------------------------------------------------


class TestParseSingleBandBulletin:
    """parse_dpbra_xml correctly handles the single-band massif-064 sample."""

    @pytest.fixture()
    def result(self) -> dict:
        """Parse the massif-064 sample once and return the CAAML dict."""
        return parse_dpbra_xml(_sample("massif-064.xml"))

    def test_bulletin_id_format(self, result: dict) -> None:
        """bulletinID follows FR-{NN}-{YYYY-MM-DD} format."""
        assert result["bulletinID"].startswith("FR-64-")

    def test_single_danger_rating(self, result: dict) -> None:
        """One dangerRating returned for a single-band bulletin."""
        ratings = result["dangerRatings"]
        assert len(ratings) == 1

    def test_single_rating_has_no_elevation(self, result: dict) -> None:
        """Single-band rating has no elevation key."""
        rating = result["dangerRatings"][0]
        assert "elevation" not in rating

    def test_danger_level_low(self, result: dict) -> None:
        """Massif-064 on 2026-05-18 has danger level 'low' (RISQUE1=1)."""
        assert result["dangerRatings"][0]["mainValue"] == "low"


# ---------------------------------------------------------------------------
# parse_dpbra_xml — minimal synthetic XMLs
# ---------------------------------------------------------------------------


class TestParseSyntheticBulletin:
    """parse_dpbra_xml edge-cases tested with synthetically constructed XML."""

    def test_amendment_flag_reflected_as_unscheduled(self) -> None:
        """@AMENDEMENT="true" sets unscheduled=True in the output."""
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<BULLETINS_NEIGE_AVALANCHE ID="5" MASSIF="Belledonne"
    DATEBULLETIN="2026-05-17T16:00:00"
    DATEECHEANCE="2026-05-18T18:00:00"
    DATEVALIDITE="2026-05-18T18:00:00"
    DATEDIFFUSION="2026-05-17T16:02:00"
    AMENDEMENT="true">
  <CARTOUCHERISQUE>
    <RISQUE RISQUE1="2" ALTITUDE="" RISQUE2=""
            RISQUEMAXI="2" RISQUEMAXIJ2="2"
            DATE_RISQUE_J2="2026-05-19T00:00:00"/>
    <PENTE NE="false" E="false" SE="false" S="false"
           SW="false" W="false" NW="false" N="false"/>
  </CARTOUCHERISQUE>
</BULLETINS_NEIGE_AVALANCHE>"""
        result = parse_dpbra_xml(xml)
        assert result["unscheduled"] is True

    def test_non_amendment_bulletin_is_scheduled(self) -> None:
        """@AMENDEMENT="false" sets unscheduled=False."""
        result = parse_dpbra_xml(_minimal_dpbra())
        assert result["unscheduled"] is False

    def test_tendency_steady_when_same_level(self) -> None:
        """RISQUEMAXIJ2 == RISQUE1 → tendencyType 'steady' in tendency list."""
        result = parse_dpbra_xml(_minimal_dpbra(risque1=2, risque_maxi_j2=2))
        tendency = result["tendency"]
        assert isinstance(tendency, list)
        assert tendency[0]["tendencyType"] == "steady"

    def test_tendency_increasing_when_j2_higher(self) -> None:
        """RISQUEMAXIJ2 > RISQUE1 → tendencyType 'increasing'."""
        result = parse_dpbra_xml(_minimal_dpbra(risque1=2, risque_maxi_j2=3))
        assert result["tendency"][0]["tendencyType"] == "increasing"

    def test_tendency_decreasing_when_j2_lower(self) -> None:
        """RISQUEMAXIJ2 < RISQUE1 → tendencyType 'decreasing'."""
        result = parse_dpbra_xml(_minimal_dpbra(risque1=3, risque_maxi_j2=2))
        assert result["tendency"][0]["tendencyType"] == "decreasing"

    def test_bulletin_id_uses_paris_local_date(self) -> None:
        """bulletinID date component uses Europe/Paris calendar day from DATEVALIDITE.

        At 2026-01-01T01:00:00 CET (UTC+1) the Paris local date is 2026-01-01;
        the corresponding UTC instant is 2025-12-31T23:00:00Z — but the
        bulletinID must use the Paris date, not the UTC date.
        """
        result = parse_dpbra_xml(
            _minimal_dpbra(
                date_validite="2026-01-01T01:00:00",
                date_bulletin="2025-12-31T16:00:00",
            )
        )
        bid = result["bulletinID"]
        # Format: FR-01-YYYY-MM-DD — must use Paris local date 2026-01-01
        assert "-2026-01-01" in bid
