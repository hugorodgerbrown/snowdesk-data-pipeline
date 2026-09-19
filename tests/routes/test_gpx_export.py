"""
tests/routes/test_gpx_export.py — the stored-route GPX export (SNOW-988).

Covers ``build_gpx`` and ``gpx_filename``: what the document says about
itself, the three ways it differs from the upload, and that what comes out
can be read straight back in by the project's own parser.
"""

from __future__ import annotations

import datetime

import pytest

from apps.routes.services.gpx import parse_gpx
from apps.routes.services.gpx_export import build_gpx, gpx_filename
from tests.factories import RouteFactory

pytestmark = pytest.mark.django_db


class TestBuildGpx:
    """Rendering one stored route as a GPX document."""

    def test_round_trips_through_the_projects_own_parser(self) -> None:
        """What we emit, ``parse_gpx`` reads back with the same geometry."""
        route = RouteFactory.create()

        parsed = parse_gpx(build_gpx(route).encode("utf-8"))

        assert parsed.point_count == route.point_count
        for original, returned in zip(route.points, parsed.points, strict=True):
            assert returned[0] == pytest.approx(original[0], abs=1e-6)
            assert returned[1] == pytest.approx(original[1], abs=1e-6)
            assert returned[2] == pytest.approx(original[2], abs=0.1)

    def test_carries_no_per_point_timestamp(self) -> None:
        """Per-point times were never stored, so none is invented."""
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 41, 38, tzinfo=datetime.UTC),
            finished_at=datetime.datetime(2026, 3, 13, 14, 41, 35, tzinfo=datetime.UTC),
        )

        document = build_gpx(route)

        assert "<trkpt" in document
        assert "<time>" in document.split("</metadata>")[0]
        assert "<time>" not in document.split("</metadata>")[1]

    def test_states_that_it_is_not_the_upload(self) -> None:
        """The description says so, so a travelling copy cannot mislead."""
        route = RouteFactory.create()

        document = build_gpx(route)

        assert "parsed and discarded at ingest; this is not it" in document

    def test_reports_a_simplified_track_as_simplified(self) -> None:
        """A thinned route says what it was thinned from."""
        route = RouteFactory.create(point_count=3, source_point_count=30_000)

        assert "simplified from 30000 in the source file" in build_gpx(route)

    def test_reports_an_unsimplified_track_as_the_sources_own_count(self) -> None:
        """Equal counts mean the file was stored whole, and it says that."""
        route = RouteFactory.create(point_count=3, source_point_count=3)

        assert "the source file's own count — not simplified" in build_gpx(route)

    def test_reports_an_unrecorded_source_count_as_unrecorded(self) -> None:
        """A pre-SNOW-988 row claims neither zero nor equality."""
        route = RouteFactory.create(point_count=3, source_point_count=None)

        document = build_gpx(route)

        assert "source count was not recorded" in document
        assert "not simplified" not in document

    def test_omits_the_element_for_a_point_with_no_elevation(self) -> None:
        """A null ``ele`` is left out rather than defaulted to zero."""
        route = RouteFactory.create(
            points=[[7.4, 46.1, None], [7.41, 46.11, 2000.0]],
            point_count=2,
            source_point_count=2,
            ascent_m=None,
            descent_m=None,
        )

        document = build_gpx(route)

        assert '<trkpt lat="46.100000" lon="7.400000"></trkpt>' in document
        assert "<ele>2000.0</ele>" in document
        assert "<ele>0" not in document

    def test_writes_the_stored_bounds(self) -> None:
        """Bounds ride in the metadata, in GPX's lat/lon attribute order."""
        route = RouteFactory.create(bounds=[7.4, 46.1, 7.42, 46.12])

        assert (
            '<bounds minlat="46.1" minlon="7.4" maxlat="46.12" maxlon="7.42"/>'
            in build_gpx(route)
        )


class TestGpxFilename:
    """Naming the download."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Col de la Chaux", "Col-de-la-Chaux.gpx"),
            ("Mont Fort // north face", "Mont-Fort-north-face.gpx"),
            ("  spaced  out  ", "spaced-out.gpx"),
        ],
    )
    def test_reduces_a_label_to_a_safe_name(self, name: str, expected: str) -> None:
        """Punctuation collapses to single hyphens and the ends are trimmed."""
        route = RouteFactory.create(name=name)

        assert gpx_filename(route) == expected

    def test_falls_back_to_the_uuid_when_a_label_reduces_to_nothing(self) -> None:
        """An unnameable route still downloads under a stable name."""
        route = RouteFactory.create(name="🏔️🏔️", source_filename="")

        assert gpx_filename(route) == f"route-{route.uuid}.gpx"
