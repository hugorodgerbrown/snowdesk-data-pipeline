"""
tests/public/test_forecast_weather_api.py — Tests for GET /api/forecast-weather.geojson.

Covers (SNOW-573):

* Privacy — the load-bearing test: a ``ForecastPoint`` reachable only from
  a ``Favourite`` must never appear in this public endpoint.
* Response shape: ``[lon, lat]`` ordering, ``resort_id``/``name``/
  ``region_id``/``days`` properties.
* ``?d=`` narrowing and its malformed-input 400.
* The ``weather_layer`` flag gate (404 while inactive).
* ``Cache-Control`` and the freshness headers (oldest-``fetched_at``-wins).
* Query count — two queries regardless of resort count (no N+1).
* Short-window tolerance (fewer than ``POINT_FORECAST_DAYS`` stored rows)
  and null extended fields.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from waffle.testutils import override_flag

from apps.core.freshness import GENERATED_AT_HEADER, MAX_AGE_HEADER, UNSAFE_AFTER_HEADER
from tests.factories import (
    FavouriteFactory,
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    ResortFactory,
)


@pytest.fixture(autouse=True)
def _enable_weather_layer_flag():  # type: ignore[no-untyped-def]
    """Force ``weather_layer=on`` for every test in this module.

    The flag gate itself gets its own dedicated test below with the flag
    forced off; every other test needs the endpoint reachable. Also clears
    the server-side response cache before each test — the view's
    ``cache.get_or_set`` key is stable (``forecast-weather:v1:<d|all>``),
    so a stale entry from a previous test would otherwise leak in.
    """
    cache.clear()
    with override_flag("weather_layer", active=True):
        yield


@pytest.mark.django_db
class TestForecastWeatherGeojsonPrivacy:
    """The privacy contract is this ticket's most important test."""

    def test_favourite_only_point_never_appears(self) -> None:
        """A ForecastPoint reachable only from a Favourite is never in the public payload.

        This is the assertion the whole endpoint exists to uphold: favourite
        pins are private, and the public map layer must only ever surface
        resort-anchored weather. Deleting the ``forecast_point__isnull=False``
        + resort-anchoring filter in the view would make this test fail.
        """
        favourite_point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        favourite = FavouriteFactory.create(
            forecast_point=favourite_point,
            latitude=47.0,
            longitude=8.0,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=favourite_point,
            valid_for_date=timezone.localdate(),
        )
        # A resort-anchored point, so the response isn't trivially empty.
        resort_point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        ResortFactory.create(geocoded=True, forecast_point=resort_point)
        ForecastPointWeatherFactory.create(
            forecast_point=resort_point,
            valid_for_date=timezone.localdate(),
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        assert response.status_code == 200
        body = response.json()
        point_ids = {
            (f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1])
            for f in body["features"]
        }
        # The favourite's coordinates never appear.
        assert (favourite.longitude, favourite.latitude) not in point_ids
        # Sanity: the resort-anchored point does appear.
        assert (resort_point.longitude, resort_point.latitude) in point_ids
        # Belt and braces: no feature anywhere references the favourite's
        # own forecast_point via its resort_id (there is none — favourites
        # don't have resort ids), and the favourite count stays exactly one
        # feature short of "both points included".
        assert len(body["features"]) == 1

    def test_resort_without_forecast_point_is_skipped(self) -> None:
        """An unlinked (geocoded but forecast_point=None) resort contributes no feature."""
        ResortFactory.create(geocoded=True, forecast_point=None)

        response = Client().get(reverse("api:forecast_weather_geojson"))

        assert response.status_code == 200
        assert response.json()["features"] == []

    def test_ungeocoded_resort_with_forecast_point_is_skipped(self) -> None:
        """A resort missing coordinates is excluded even if linked to a point."""
        point = ForecastPointFactory.create()
        ResortFactory.create(latitude=None, longitude=None, forecast_point=point)

        response = Client().get(reverse("api:forecast_weather_geojson"))

        assert response.status_code == 200
        assert response.json()["features"] == []


@pytest.mark.django_db
class TestForecastWeatherGeojsonShape:
    """Response shape and property contents."""

    # Frozen to midday, inside the 06:00–20:00 sunrise/sunset window set up
    # below. The icon's day/night suffix is a projection of the CURRENT
    # time-of-day onto the row's stored sunrise/sunset
    # (weather_display.is_day), not a property of the stored row — so left
    # to the wall clock this assertion passes by day and fails every
    # evening after 20:00 UTC, including in CI, which runs on UTC.
    @freeze_time("2026-08-07T12:00:00Z")
    def test_feature_shape(self) -> None:
        """[lon, lat] ordering and resort_id/name/region_id/days properties."""
        point = ForecastPointFactory.create()
        resort = ResortFactory.create(
            name="Verbier", geocoded=True, forecast_point=point
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=dt.date(2026, 8, 7),
            weather_code=71,  # light snowfall
            sunrise=dt.datetime(2026, 8, 7, 6, 0, tzinfo=dt.UTC),
            sunset=dt.datetime(2026, 8, 7, 20, 0, tzinfo=dt.UTC),
            temperature_2m_max=4.0,
            temperature_2m_min=-3.0,
            snowfall_sum=2.0,
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        feature = body["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"] == {
            "type": "Point",
            "coordinates": [resort.longitude, resort.latitude],
        }
        props = feature["properties"]
        assert props["resort_id"] == resort.pk
        assert props["name"] == "Verbier"
        assert props["region_id"] == resort.region.region_id
        assert props["days"]["2026-08-07"] == {
            "icon": "light_snow-day.svg",
            "label": "Light snow",
            "tmax": 4.0,
            "tmin": -3.0,
            "snow": 2.0,
        }

    def test_geometry_is_resort_coordinates_not_point_coordinates(self) -> None:
        """Geometry is the resort's own coordinates, not the quantised point's."""
        point = ForecastPointFactory.create(latitude=46.5, longitude=7.9)
        resort = ResortFactory.create(
            geocoded=True, latitude=46.1, longitude=7.4, forecast_point=point
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        feature = response.json()["features"][0]
        assert feature["geometry"]["coordinates"] == [resort.longitude, resort.latitude]
        assert feature["geometry"]["coordinates"] != [point.longitude, point.latitude]

    def test_resort_with_no_weather_rows_yields_empty_days(self) -> None:
        """A newly-linked resort with no fetched weather yet still gets a feature."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)

        response = Client().get(reverse("api:forecast_weather_geojson"))

        body = response.json()
        assert len(body["features"]) == 1
        assert body["features"][0]["properties"]["days"] == {}


@pytest.mark.django_db
class TestForecastWeatherGeojsonDateParam:
    """?d=YYYY-MM-DD narrowing and its error handling."""

    def test_date_param_narrows_to_one_date(self) -> None:
        """?d= restricts every feature's days dict to the requested date."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=dt.date(2026, 8, 7)
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=dt.date(2026, 8, 8)
        )

        response = Client().get(
            reverse("api:forecast_weather_geojson") + "?d=2026-08-07"
        )

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {"2026-08-07"}

    def test_no_date_param_returns_whole_window(self) -> None:
        """Without ?d=, days carries every stored date (mirrors /api/ratings/)."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=dt.date(2026, 8, 7)
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=dt.date(2026, 8, 8)
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        days = response.json()["features"][0]["properties"]["days"]
        assert set(days.keys()) == {"2026-08-07", "2026-08-08"}

    @pytest.mark.parametrize(
        "bad_date",
        ["2026-13-01", "not-a-date", "20260807", "2026/08/07"],
    )
    def test_malformed_date_returns_400(self, bad_date: str) -> None:
        """A malformed ?d= value is rejected with 400, not a 500."""
        response = Client().get(
            reverse("api:forecast_weather_geojson") + f"?d={bad_date}"
        )
        assert response.status_code == 400
        assert response.json() == {"error": "malformed date"}


@pytest.mark.django_db
class TestForecastWeatherGeojsonFlagGate:
    """The weather_layer waffle flag gate."""

    def test_flag_inactive_returns_404(self) -> None:
        """With the flag forced off, the endpoint 404s."""
        with override_flag("weather_layer", active=False):
            response = Client().get(reverse("api:forecast_weather_geojson"))
        assert response.status_code == 404


@pytest.mark.django_db
class TestForecastWeatherGeojsonHeaders:
    """Cache-Control and freshness headers."""

    def test_cache_control_public(self) -> None:
        """The response is publicly cacheable, mirroring resorts_geojson."""
        response = Client().get(reverse("api:forecast_weather_geojson"))
        assert "public" in response["Cache-Control"]

    def test_generated_at_is_oldest_fetched_at(self) -> None:
        """generated_at is the OLDEST fetched_at across the payload's rows."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        older = timezone.now() - dt.timedelta(hours=2)
        newer = timezone.now() - dt.timedelta(minutes=5)
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=dt.date(2026, 8, 7),
            fetched_at=older,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=dt.date(2026, 8, 8),
            fetched_at=newer,
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        generated_at = dt.datetime.fromisoformat(response[GENERATED_AT_HEADER])
        assert generated_at == older.replace(microsecond=0)

    def test_unsafe_after_header_absent(self) -> None:
        """Weather is non-safety-critical — X-Data-Unsafe-After is never set."""
        response = Client().get(reverse("api:forecast_weather_geojson"))
        assert MAX_AGE_HEADER in response
        assert UNSAFE_AFTER_HEADER not in response


@pytest.mark.django_db
class TestForecastWeatherGeojsonQueryCount:
    """No N+1: query count must not grow with the number of resorts."""

    def _create_resort_with_weather(self, index: int) -> None:
        """Create one geocoded resort with a linked point and one weather row.

        ``index`` varies the point's coordinates so repeated calls land in
        distinct (lat_cell, lon_cell, elevation_band) cells rather than
        tripping ``ForecastPoint``'s unique_together constraint.
        """
        point = ForecastPointFactory.create(
            latitude=46.1 + index * 0.5, longitude=7.4 + index * 0.5
        )
        ResortFactory.create(
            geocoded=True,
            latitude=point.latitude,
            longitude=point.longitude,
            forecast_point=point,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=timezone.localdate()
        )

    def test_query_count_is_flat_regardless_of_resort_count(self) -> None:
        """The query count with 10 resorts equals the count with 1."""
        self._create_resort_with_weather(0)
        cache.clear()
        with CaptureQueriesContext(connection) as ctx_one:
            response_one = Client().get(reverse("api:forecast_weather_geojson"))
        assert response_one.status_code == 200

        for index in range(1, 10):
            self._create_resort_with_weather(index)
        cache.clear()
        with CaptureQueriesContext(connection) as ctx_ten:
            response_ten = Client().get(reverse("api:forecast_weather_geojson"))
        assert response_ten.status_code == 200

        # Two app-level queries (resorts + bulk weather fetch) plus a small,
        # fixed number of framework queries (waffle flag lookup, session) —
        # the important assertion is that the count does not grow with the
        # number of resorts.
        assert len(ctx_ten.captured_queries) == len(ctx_one.captured_queries)


@pytest.mark.django_db
class TestForecastWeatherGeojsonShortWindowTolerance:
    """A short forecast window and null extended fields are normal, not errors."""

    def test_short_window_of_five_rows(self) -> None:
        """A point with only 5 stored rows (a model that ran short)."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        start = dt.date(2026, 8, 7)
        for offset in range(5):
            ForecastPointWeatherFactory.create(
                forecast_point=point,
                valid_for_date=start + dt.timedelta(days=offset),
            )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        assert response.status_code == 200
        days = response.json()["features"][0]["properties"]["days"]
        assert len(days) == 5

    def test_null_temperature_and_snowfall_pass_through_as_none(self) -> None:
        """Nullable extended fields surface as None, not omitted or coerced."""
        point = ForecastPointFactory.create()
        ResortFactory.create(geocoded=True, forecast_point=point)
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=dt.date(2026, 8, 7),
            temperature_2m_max=None,
            temperature_2m_min=None,
            snowfall_sum=None,
        )

        response = Client().get(reverse("api:forecast_weather_geojson"))

        entry = response.json()["features"][0]["properties"]["days"]["2026-08-07"]
        assert entry["tmax"] is None
        assert entry["tmin"] is None
        assert entry["snow"] is None
