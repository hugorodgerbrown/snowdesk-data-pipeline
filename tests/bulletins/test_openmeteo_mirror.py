"""
tests/bulletins/test_openmeteo_mirror.py — Tests for the dev-only Open-Meteo mirror view.

The mirror replays ``sample_data/openmeteo_archive.ndjson`` in an Open-Meteo-
compatible response shape, resolved by lat/lon to a Region and filtered by
date range. These tests exercise it via the Django test client (DEBUG is True
under config.settings.development, so the URLs are mounted).

Separate from ``test_dev_mirror.py`` so the two distinct mirrors' tests don't
crowd a single file.
"""

from pathlib import Path
from typing import Any

import pytest
from django.test import Client, override_settings

from bulletins.services.openmeteo_archive import write_archive
from tests.factories import RegionFactory


def _om_record(
    region_id: str,
    date: str,
    weather_code: int = 3,
    captured_at: str = "2026-05-09T12:00:00Z",
) -> dict[str, Any]:
    """Build a minimal Open-Meteo archive record."""
    return {
        "region_id": region_id,
        "date": date,
        "weather_code": weather_code,
        "sunrise": f"{date}T05:32+02:00",
        "sunset": f"{date}T20:14+02:00",
        "captured_at": captured_at,
    }


def _make_archive(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """Write the given records to an archive file at path."""
    write_archive(path, records)


@pytest.mark.django_db
class TestOpenMeteoMirrorForecastEndpoint:
    """Tests for the /dev/openmeteo-mirror/v1/forecast endpoint."""

    def test_matching_region_and_date_returns_payload(self, tmp_path: Path) -> None:
        """A request with a lat/lon that matches a region and a covered date returns 200."""
        region = RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})
        archive_path = tmp_path / "om_archive.ndjson"
        _make_archive(
            archive_path,
            [_om_record(region.region_id, "2026-05-01", weather_code=1)],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-01",
                    "daily": "weather_code,sunrise,sunset",
                    "timezone": "auto",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert "daily" in body
        daily = body["daily"]
        assert daily["time"] == ["2026-05-01"]
        assert daily["weather_code"] == [1]
        assert daily["sunrise"] == ["2026-05-01T05:32+02:00"]
        assert daily["sunset"] == ["2026-05-01T20:14+02:00"]

    def test_multi_day_range_returns_all_days(self, tmp_path: Path) -> None:
        """A multi-day request returns all days in order."""
        region = RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})
        archive_path = tmp_path / "om_archive.ndjson"
        _make_archive(
            archive_path,
            [
                _om_record(region.region_id, "2026-05-01", weather_code=0),
                _om_record(region.region_id, "2026-05-02", weather_code=1),
                _om_record(region.region_id, "2026-05-03", weather_code=2),
            ],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-03",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["daily"]["time"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
        assert body["daily"]["weather_code"] == [0, 1, 2]


@pytest.mark.django_db
class TestOpenMeteoMirrorArchiveEndpoint:
    """Tests for the /dev/openmeteo-mirror/v1/archive endpoint."""

    def test_archive_endpoint_returns_same_payload_as_forecast(
        self, tmp_path: Path
    ) -> None:
        """Both endpoint kinds serve the same archive data."""
        region = RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})
        archive_path = tmp_path / "om_archive.ndjson"
        _make_archive(
            archive_path,
            [_om_record(region.region_id, "2026-04-01", weather_code=7)],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/archive",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-01",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["daily"]["weather_code"] == [7]


@pytest.mark.django_db
class TestOpenMeteoMirrorErrors:
    """Tests for error cases in the Open-Meteo mirror view."""

    def test_unknown_lat_lon_returns_404(self, tmp_path: Path) -> None:
        """A lat/lon that doesn't match any Region returns 404."""
        archive_path = tmp_path / "om_archive.ndjson"
        archive_path.write_text("", encoding="utf-8")
        RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "99.99",
                    "longitude": "99.99",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-01",
                },
            )

        assert response.status_code == 404
        assert "error" in response.json()

    def test_partial_date_coverage_returns_404(self, tmp_path: Path) -> None:
        """If any date in the range is missing from the archive, return 404."""
        region = RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})
        archive_path = tmp_path / "om_archive.ndjson"
        # Only 2026-05-01 present; request asks for 2026-05-01 to 2026-05-02.
        _make_archive(
            archive_path,
            [_om_record(region.region_id, "2026-05-01", weather_code=0)],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-02",
                },
            )

        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "2026-05-02" in body["error"]

    def test_no_records_for_region_returns_404(self, tmp_path: Path) -> None:
        """If the archive has no records for the matched region, return 404."""
        matched_region = RegionFactory.create(centre={"lat": 46.21, "lon": 7.36})
        other_region_id = "CH-OTHER"
        archive_path = tmp_path / "om_archive.ndjson"
        # Archive only has records for a different region (not matched_region).
        assert matched_region.region_id != other_region_id
        _make_archive(
            archive_path,
            [_om_record(other_region_id, "2026-05-01", weather_code=0)],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-01",
                },
            )

        assert response.status_code == 404
        assert "error" in response.json()

    def test_region_without_centre_is_not_matched(self, tmp_path: Path) -> None:
        """A region with centre=None cannot be matched and returns 404."""
        region = RegionFactory.create(centre=None)
        archive_path = tmp_path / "om_archive.ndjson"
        _make_archive(
            archive_path,
            [_om_record(region.region_id, "2026-05-01", weather_code=0)],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/openmeteo-mirror/v1/forecast",
                {
                    "latitude": "46.21",
                    "longitude": "7.36",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-01",
                },
            )

        assert response.status_code == 404


@pytest.mark.django_db
class TestOpenMeteoMirrorLatLonRoundTrip:
    """Regression: lat/lon string round-trip is bit-exact for all Regions."""

    def test_all_regions_round_trip_lat_lon_via_str(self, tmp_path: Path) -> None:
        """
        Every Region in the test DB can round-trip through the mirror.

        Loads multiple regions, creates an archive record for each, then
        posts each region's centre lat/lon (as str()) to the mirror and
        asserts a 200. This guards against silent precision drift if the
        regions fixture is regenerated.
        """
        # Create three regions with varied lat/lon values.
        regions = [
            RegionFactory.create(centre={"lat": 46.21, "lon": 7.36}),
            RegionFactory.create(centre={"lat": 47.2, "lon": 8.1}),
            RegionFactory.create(centre={"lat": 46.5, "lon": 9.75}),
        ]
        archive_path = tmp_path / "om_archive.ndjson"
        _make_archive(
            archive_path,
            [_om_record(r.region_id, "2026-05-01") for r in regions],
        )

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            for region in regions:
                response = Client().get(
                    "/dev/openmeteo-mirror/v1/forecast",
                    {
                        "latitude": str(region.centre["lat"]),  # type: ignore[index]
                        "longitude": str(region.centre["lon"]),  # type: ignore[index]
                        "start_date": "2026-05-01",
                        "end_date": "2026-05-01",
                    },
                )
                assert response.status_code == 200, (
                    f"Region {region.region_id} lat={region.centre['lat']} "  # type: ignore[index]
                    f"lon={region.centre['lon']} failed round-trip. "  # type: ignore[index]
                    f"Response: {response.json()}"
                )
