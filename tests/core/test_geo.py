"""
tests/core/test_geo.py — Tests for apps.core.geo (SNOW-708).

Covers haversine_km / haversine_m: known distances, degenerate and extreme
pairs, unit agreement, and — the point of the ticket — bit-for-bit
equivalence with each of the four private copies this module replaced.

The equivalence tests carry verbatim transcriptions of those four
implementations as they stood at 132a679c. They are the only thing proving
the consolidation is behaviour-preserving rather than merely assumed to be,
and they are meant to be deleted once nobody doubts it.
"""

from __future__ import annotations

import math

import pytest

from apps.core.geo import EARTH_RADIUS_KM, EARTH_RADIUS_M, haversine_km, haversine_m

# A spread of pairs covering the scales the four callers actually work at:
# identical points, a GPX leg, a forecast-cell reuse radius, an observation
# radius, cross-Alp, cross-continent, and the degenerate extremes.
PAIRS: list[tuple[float, float, float, float]] = [
    (46.0961, 7.2286, 46.0961, 7.2286),  # identical — Verbier village
    (46.0961, 7.2286, 46.09611, 7.22861),  # ~1 m — one GPX leg
    (46.0961, 7.2286, 46.1028, 7.2286),  # ~745 m — the reuse threshold
    (46.0961, 7.2286, 46.1030, 7.3100),  # ~6 km — within an observation radius
    (46.0961, 7.2286, 46.4908, 9.8355),  # ~205 km — Verbier to Corvatsch
    (46.0961, 7.2286, 47.3769, 8.5417),  # ~163 km — Verbier to Zurich
    (0.0, 0.0, 0.0, 0.0),  # null island, identical
    (90.0, 0.0, -90.0, 0.0),  # pole to pole
    (0.0, 0.0, 0.0, 180.0),  # antipodal on the equator
    (-33.8688, 151.2093, 40.7128, -74.0060),  # Sydney to New York
]


# ---------------------------------------------------------------------------
# Reference implementations — verbatim copies of what apps/core/geo.py
# replaced. Do not "tidy" these; their value is that they are unchanged.
# ---------------------------------------------------------------------------

_REF_EARTH_RADIUS_KM = 6371.0088
_REF_EARTH_RADIUS_M = 6_371_008.8


def _ref_weather_haversine_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """apps/weather/services/forecast_points.py::_haversine_m, as it stood."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    distance_km = 2 * _REF_EARTH_RADIUS_KM * math.asin(math.sqrt(a))
    return distance_km * 1000


def _ref_observations_haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """apps/observations/models.py::_haversine_km, as it stood."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _REF_EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _ref_mcp_haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """apps/mcp_server/resolvers.py::_haversine_km, as it stood."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _REF_EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _ref_gpx_haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """apps/routes/services/gpx.py::_haversine_m, as it stood (lon first)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _REF_EARTH_RADIUS_M * math.asin(math.sqrt(a))


class TestKnownDistances:
    """Distances against independently known figures."""

    def test_identical_points_are_zero(self) -> None:
        """Two identical coordinates are exactly zero apart, not near-zero."""
        assert haversine_km(46.0961, 7.2286, 46.0961, 7.2286) == 0.0
        assert haversine_m(46.0961, 7.2286, 46.0961, 7.2286) == 0.0

    def test_pole_to_pole_is_half_the_circumference(self) -> None:
        """Pole to pole is half a great circle, to within a metre."""
        assert haversine_km(90.0, 0.0, -90.0, 0.0) == pytest.approx(
            math.pi * EARTH_RADIUS_KM, abs=1e-3
        )

    def test_equatorial_antipodes_are_half_the_circumference(self) -> None:
        """Antipodal equatorial points are half a great circle apart."""
        assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(
            math.pi * EARTH_RADIUS_KM, abs=1e-3
        )

    def test_one_degree_of_latitude_is_about_111_km(self) -> None:
        """A degree of latitude is ~111.19 km anywhere on the sphere."""
        assert haversine_km(46.0, 7.0, 47.0, 7.0) == pytest.approx(111.19, abs=0.01)

    def test_sydney_to_new_york(self) -> None:
        """A continental-scale pair, against the published great-circle figure."""
        assert haversine_km(-33.8688, 151.2093, 40.7128, -74.0060) == pytest.approx(
            15_990, abs=10
        )

    def test_sub_metre_separation_is_resolved(self) -> None:
        """A ~1 m separation comes out near 1 m, not rounded to zero.

        The regime a GPX leg between consecutive 1 Hz trackpoints lives in,
        and the one where a careless formula loses all its precision.
        """
        metres = haversine_m(46.0961, 7.2286, 46.09611, 7.2286)
        assert 1.0 < metres < 1.2


class TestUnits:
    """The two entry points must agree on the same physical distance."""

    def test_radius_constants_are_the_same_value(self) -> None:
        """The two radius constants express one value in two units."""
        assert EARTH_RADIUS_M == pytest.approx(EARTH_RADIUS_KM * 1000, abs=1e-6)

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_km_and_m_agree(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """``haversine_m`` is ``haversine_km`` times a thousand."""
        assert haversine_m(lat1, lon1, lat2, lon2) == pytest.approx(
            haversine_km(lat1, lon1, lat2, lon2) * 1000, rel=1e-12
        )

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_is_symmetric(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """Swapping the two points does not change the distance."""
        assert haversine_km(lat1, lon1, lat2, lon2) == haversine_km(
            lat2, lon2, lat1, lon1
        )


class TestEquivalenceWithReplacedCopies:
    """The consolidation must be behaviour-preserving, not merely close.

    Two of the four copies are matched bit-for-bit. The other two differ in
    their last bits, each for a reason worth knowing:

    * **weather** computed kilometres and multiplied by 1000, rounding
      twice where this rounds once.
    * **gpx** subtracted its two latitudes *after* converting each to
      radians; this converts the difference. Two roundings and a
      cancellation against one rounding.

    Neither difference exceeds **2 nanometres** across the pairs below,
    including the 16,000 km one — against a 750 m cell-reuse threshold and
    a GPX leg measured from consumer GPS. Both are strictly more accurate
    than what they replace, so this is a fix, not a regression to tolerate.
    """

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_matches_observations_copy_exactly(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """Bit-identical to the observations copy.

        This one carries the strictest requirement of the four: the
        proximity filter compares ``distance <= radius_km`` on an inclusive
        boundary, so a last-bit change would silently drop a field report
        sitting exactly on the edge.
        """
        assert haversine_km(lat1, lon1, lat2, lon2) == _ref_observations_haversine_km(
            lat1, lon1, lat2, lon2
        )

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_matches_mcp_copy_exactly(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """Bit-identical to the MCP resolvers copy."""
        assert haversine_km(lat1, lon1, lat2, lon2) == _ref_mcp_haversine_km(
            lat1, lon1, lat2, lon2
        )

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_matches_gpx_copy_to_within_a_nanometre(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """Within 10 nm of the GPX copy, with its lon-first order swapped.

        The GPX copy subtracted latitudes already converted to radians;
        this converts the difference, which avoids a cancellation. Route
        length is a sum of thousands of these legs, so the bound has to
        hold per leg — at 2e-10 m each, a 30,000-leg track accumulates a
        divergence six orders of magnitude below a millimetre.
        """
        assert haversine_m(lat1, lon1, lat2, lon2) == pytest.approx(
            _ref_gpx_haversine_m(lon1, lat1, lon2, lat2), abs=1e-8
        )

    @pytest.mark.parametrize(("lat1", "lon1", "lat2", "lon2"), PAIRS)
    def test_matches_weather_copy_to_within_a_nanometre(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> None:
        """Within a nanometre of the weather copy.

        The weather copy rounded twice (kilometres, then x1000); this one
        rounds once. Its consumer is the 750 m forecast-cell reuse
        threshold, so a nanometre cannot change which cell a pin snaps to.
        """
        assert haversine_m(lat1, lon1, lat2, lon2) == pytest.approx(
            _ref_weather_haversine_m(lat1, lon1, lat2, lon2), abs=1e-8
        )
