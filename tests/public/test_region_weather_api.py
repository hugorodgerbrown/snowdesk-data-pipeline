"""
tests/public/test_region_weather_api.py — Tests for GET /api/region-weather.geojson.

Covers (SNOW-698):

* Response shape: ``[lon, lat]`` ordering read out of ``MicroRegion.centre``
  (a flat ``{"lon", "lat"}`` dict, not a GeoJSON geometry), and the
  ``region_id``/``name``/``days`` properties.
* Membership rules: a region with a ``centre`` but no snapshot in the
  window is ABSENT rather than present with an empty ``days``; a region
  with ``centre=None`` is skipped entirely.
* The default window — ``REGION_WEATHER_DAYS_BACK`` days ending today —
  and its boundary.
* ``?d=`` narrowing and its malformed-input 400.
* Public reachability — an anonymous visitor with no flag active gets 200.
* ``Cache-Control``, the freshness headers (oldest-``fetched_at``-wins),
  and the absence of ``Vary: Cookie`` with analytics enabled.
* Query count — flat regardless of region count (no N+1).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.core.freshness import GENERATED_AT_HEADER, MAX_AGE_HEADER, UNSAFE_AFTER_HEADER
from apps.public.api import REGION_WEATHER_DAYS_BACK
from tests.factories import MicroRegionFactory, WeatherSnapshotFactory


@pytest.fixture(autouse=True)
def _clear_response_cache():  # type: ignore[no-untyped-def]
    """Clear the server-side response cache before each test.

    The view's ``cache.get_or_set`` key is stable
    (``region-weather:v1:<d|all>``), so a stale entry from a previous test
    would otherwise leak in.
    """
    cache.clear()
    yield


@pytest.mark.django_db
class TestRegionWeatherGeojsonShape:
    """Response shape and property contents."""

    # Frozen to midday, inside the 06:00–20:00 sunrise/sunset window set up
    # below. The icon's day/night suffix is a projection of the CURRENT
    # time-of-day onto the row's stored sunrise/sunset
    # (weather_display.is_day), not a property of the stored row — so left
    # to the wall clock this assertion passes by day and fails every
    # evening after 20:00 UTC, including in CI, which runs on UTC.
    @freeze_time("2026-08-07T12:00:00Z")
    def test_feature_shape(self) -> None:
        """[lon, lat] ordering and region_id/name/days properties."""
        region = MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny / Verbier",
            centre={"lon": 7.23, "lat": 46.09},
        )
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=dt.date(2026, 8, 7),
            weather_code=71,  # light snowfall
            sunrise=dt.datetime(2026, 8, 7, 6, 0, tzinfo=dt.UTC),
            sunset=dt.datetime(2026, 8, 7, 20, 0, tzinfo=dt.UTC),
            temperature_2m_max=4.0,
            temperature_2m_min=-3.0,
            snowfall_sum=2.0,
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        feature = body["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"] == {
            "type": "Point",
            "coordinates": [7.23, 46.09],
        }
        props = feature["properties"]
        assert props["region_id"] == "CH-4115"
        assert props["name"] == "Martigny / Verbier"
        assert props["days"]["2026-08-07"] == {
            "icon": "light_snow-day.svg",
            "label": "Light snow",
            "tmax": 4.0,
            "tmin": -3.0,
            "snow": 2.0,
        }

    def test_geometry_is_lon_lat_not_the_stored_dict_order(self) -> None:
        """``centre`` is {"lon", "lat"}; the geometry is the [lon, lat] pair."""
        region = MicroRegionFactory.create(centre={"lat": 46.09, "lon": 7.23})
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate()
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        coordinates = response.json()["features"][0]["geometry"]["coordinates"]
        assert coordinates == [7.23, 46.09]

    def test_days_is_keyed_by_iso_date(self) -> None:
        """Each feature's days dict is keyed by ISO date string."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        for offset in range(3):
            WeatherSnapshotFactory.create(
                region=region, valid_for_date=today - dt.timedelta(days=offset)
            )

        response = Client().get(reverse("api:region_weather_geojson"))

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {
            (today - dt.timedelta(days=offset)).isoformat() for offset in range(3)
        }

    def test_null_temperature_and_snowfall_pass_through_as_none(self) -> None:
        """Nullable extended fields surface as None, not omitted or coerced.

        ``WeatherSnapshotFactory`` leaves all three at ``None`` by default
        (unlike ``ForecastCellWeatherFactory``), so this is the factory's
        own shape rather than an override.
        """
        region = MicroRegionFactory.create()
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate()
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        days = response.json()["features"][0]["properties"]["days"]
        entry = days[timezone.localdate().isoformat()]
        assert entry["tmax"] is None
        assert entry["tmin"] is None
        assert entry["snow"] is None


@pytest.mark.django_db
class TestRegionWeatherGeojsonMembership:
    """Which regions earn a feature at all."""

    def test_region_without_a_snapshot_is_absent(self) -> None:
        """A region with a centre but no snapshot in window yields no feature.

        Deliberately absent, not present-with-an-empty-days: at 461
        regions a feature the client can never draw is payload spent on
        nothing.
        """
        MicroRegionFactory.create(centre={"lon": 7.5, "lat": 46.8})

        response = Client().get(reverse("api:region_weather_geojson"))

        assert response.status_code == 200
        assert response.json()["features"] == []

    def test_region_with_null_centre_is_skipped(self) -> None:
        """A region with no centre has nowhere to draw a symbol, so is skipped."""
        no_centre = MicroRegionFactory.create(centre=None)
        WeatherSnapshotFactory.create(
            region=no_centre, valid_for_date=timezone.localdate()
        )
        with_centre = MicroRegionFactory.create(centre={"lon": 7.5, "lat": 46.8})
        WeatherSnapshotFactory.create(
            region=with_centre, valid_for_date=timezone.localdate()
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        region_ids = {f["properties"]["region_id"] for f in response.json()["features"]}
        assert region_ids == {with_centre.region_id}

    def test_one_feature_per_region(self) -> None:
        """Each qualifying micro-region contributes exactly one feature."""
        today = timezone.localdate()
        for _ in range(3):
            region = MicroRegionFactory.create()
            WeatherSnapshotFactory.create(region=region, valid_for_date=today)

        response = Client().get(reverse("api:region_weather_geojson"))

        assert len(response.json()["features"]) == 3


@pytest.mark.django_db
class TestRegionWeatherGeojsonWindow:
    """The default window reaches REGION_WEATHER_DAYS_BACK days back, inclusive."""

    def test_oldest_in_window_date_is_included(self) -> None:
        """The day at the far edge of the window is still served."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        oldest = today - dt.timedelta(days=REGION_WEATHER_DAYS_BACK - 1)
        WeatherSnapshotFactory.create(region=region, valid_for_date=oldest)

        response = Client().get(reverse("api:region_weather_geojson"))

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {oldest.isoformat()}

    def test_date_one_day_past_the_window_is_excluded(self) -> None:
        """A snapshot older than the window drops out, taking its region with it."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        too_old = today - dt.timedelta(days=REGION_WEATHER_DAYS_BACK)
        WeatherSnapshotFactory.create(region=region, valid_for_date=too_old)

        response = Client().get(reverse("api:region_weather_geojson"))

        assert response.json()["features"] == []

    def test_future_date_is_excluded_from_the_default_window(self) -> None:
        """The region tier covers today and history, never the forecast."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        WeatherSnapshotFactory.create(region=region, valid_for_date=today)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=today + dt.timedelta(days=1)
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {today.isoformat()}


@pytest.mark.django_db
class TestRegionWeatherGeojsonDateParam:
    """?d=YYYY-MM-DD narrowing and its error handling."""

    def test_date_param_narrows_to_one_date(self) -> None:
        """?d= restricts every feature's days dict to the requested date."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        yesterday = today - dt.timedelta(days=1)
        WeatherSnapshotFactory.create(region=region, valid_for_date=today)
        WeatherSnapshotFactory.create(region=region, valid_for_date=yesterday)

        response = Client().get(
            reverse("api:region_weather_geojson") + f"?d={yesterday.isoformat()}"
        )

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {yesterday.isoformat()}

    @pytest.mark.parametrize(
        "bad_date",
        ["2026-13-01", "not-a-date", "20260807", "2026/08/07"],
    )
    def test_malformed_date_returns_400(self, bad_date: str) -> None:
        """A malformed ?d= value is rejected with 400, not a 500."""
        response = Client().get(
            reverse("api:region_weather_geojson") + f"?d={bad_date}"
        )
        assert response.status_code == 400
        assert response.json() == {"error": "malformed date"}


@pytest.mark.django_db
class TestRegionWeatherGeojsonIsPublic:
    """The endpoint is reachable without authentication or a flag."""

    def test_anonymous_request_returns_200(self) -> None:
        """An anonymous visitor with no flag active gets the payload."""
        response = Client().get(reverse("api:region_weather_geojson"))
        assert response.status_code == 200
        assert response.json()["type"] == "FeatureCollection"


@pytest.mark.django_db
class TestRegionWeatherGeojsonHeaders:
    """Cache-Control, Vary and the freshness headers."""

    def test_cache_control_public(self) -> None:
        """The response is publicly cacheable, mirroring its point-tier sibling."""
        response = Client().get(reverse("api:region_weather_geojson"))
        assert "public" in response["Cache-Control"]

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_vary_no_cookie_with_analytics_enabled(self) -> None:
        """Vary carries Accept-Encoding but never Cookie (SNOW-299 rule).

        With ``POSTHOG_API_KEY`` set, ``PosthogContextMiddleware`` would
        read ``request.user``, causing ``SessionMiddleware`` to append
        ``Vary: Cookie`` and defeat the public caching. The
        ``_POSTHOG_EXEMPT_PATHS`` entry in ``config/settings/base.py``
        prevents that access for this endpoint.
        """
        response = Client().get(reverse("api:region_weather_geojson"))
        assert response.status_code == 200
        vary = response.get("Vary", "")
        assert "Accept-Encoding" in vary
        assert "Cookie" not in vary, (
            f"Vary: Cookie must not be set even with analytics enabled; got: {vary!r}"
        )

    def test_generated_at_is_oldest_fetched_at(self) -> None:
        """generated_at is the OLDEST fetched_at across the payload's rows."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        older = timezone.now() - dt.timedelta(hours=2)
        newer = timezone.now() - dt.timedelta(minutes=5)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=today, fetched_at=older
        )
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=today - dt.timedelta(days=1),
            fetched_at=newer,
        )

        response = Client().get(reverse("api:region_weather_geojson"))

        generated_at = dt.datetime.fromisoformat(response[GENERATED_AT_HEADER])
        assert generated_at == older.replace(microsecond=0)

    def test_unsafe_after_header_absent(self) -> None:
        """Weather is non-safety-critical — X-Data-Unsafe-After is never set."""
        response = Client().get(reverse("api:region_weather_geojson"))
        assert MAX_AGE_HEADER in response
        assert UNSAFE_AFTER_HEADER not in response


@pytest.mark.django_db
class TestRegionWeatherGeojsonQueryCount:
    """No N+1: query count must not grow with the number of regions."""

    def _create_region_with_weather(self) -> None:
        """Create one micro-region with a centre and one snapshot for today."""
        region = MicroRegionFactory.create()
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate()
        )

    def test_query_count_is_flat_regardless_of_region_count(self) -> None:
        """The query count with 10 regions equals the count with 1."""
        self._create_region_with_weather()
        cache.clear()
        with CaptureQueriesContext(connection) as ctx_one:
            response_one = Client().get(reverse("api:region_weather_geojson"))
        assert response_one.status_code == 200

        for _ in range(9):
            self._create_region_with_weather()
        cache.clear()
        with CaptureQueriesContext(connection) as ctx_ten:
            response_ten = Client().get(reverse("api:region_weather_geojson"))
        assert response_ten.status_code == 200
        assert len(response_ten.json()["features"]) == 10

        # Two app-level queries (regions + bulk snapshot fetch) plus a
        # small, fixed number of framework queries — the important
        # assertion is that the count does not grow with the region count.
        assert len(ctx_ten.captured_queries) == len(ctx_one.captured_queries)
