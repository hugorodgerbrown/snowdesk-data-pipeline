"""
tests/public/test_weather_geojson_api.py — Tests for /api/weather.geojson.

The map's Weather overlay feed (SNOW-761). One Location-anchored payload
replacing the resort-anchored and region-anchored feeds SNOW-762 removed.

The load-bearing assertion here is the **privacy** one: the feed is filtered
by ``Location.objects.public()``, so a location reachable only from a
``Favourite`` must not appear. Getting that wrong puts a stranger's private
pin and its coordinates on a public map, which is why it is a test rather
than a comment in the view.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortLocationFactory,
    WeatherFactory,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear the per-day payload cache between tests.

    The view memoises its payload under one key per day, so without this a
    payload built by one test would be served to the next.
    """
    cache.clear()


def _get() -> dict[str, Any]:
    """Fetch the feed and return its parsed payload.

    Returns:
        The decoded FeatureCollection.

    """
    response = Client().get(reverse("api:weather_geojson"))
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


def _properties_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a payload's feature properties by location name.

    Args:
        payload: The decoded FeatureCollection.

    Returns:
        Mapping of location name to its properties dict.

    """
    return {f["properties"]["name"]: f["properties"] for f in payload["features"]}


@pytest.mark.django_db
class TestPrivacy:
    """A favourite must never reach a public feed."""

    def test_a_favourite_only_location_is_absent(self) -> None:
        """``public()``, not ``active()`` — the difference is a leak.

        ``active()`` is the billable set (what we pay Open-Meteo for) and
        includes every location a Favourite reaches. Serving it here would
        publish a stranger's saved place and its coordinates.
        """
        favourite = FavouriteFactory.create()
        WeatherFactory.create(
            location=favourite.location, observed_on=timezone.localdate()
        )

        payload = _get()

        assert payload["features"] == []

    def test_a_curated_location_is_present(self) -> None:
        """A location a resort or a region centroid reaches is public."""
        resort_location = LocationFactory.create(name="Mont Fort")
        ResortLocationFactory.create(location=resort_location)
        centroid = LocationFactory.create(name="CH-1000 centroid")
        MicroRegionFactory.create(centroid_location=centroid)

        payload = _get()

        assert sorted(_properties_by_name(payload)) == [
            "CH-1000 centroid",
            "Mont Fort",
        ]


@pytest.mark.django_db
class TestPayloadShape:
    """Four properties per feature, and no more."""

    def test_a_feature_carries_exactly_the_four_declared_properties(self) -> None:
        """No ``kind``, no ``resort_id``, no ``region_id``.

        Nothing on the map reads them, and a field every visitor downloads
        and no visitor sees is payload for nothing.
        """
        location = LocationFactory.create(name="Mont Fort", elevation_m=3328.0)
        ResortLocationFactory.create(location=location)
        WeatherFactory.create(location=location, observed_on=timezone.localdate())

        feature = _get()["features"][0]

        assert set(feature["properties"]) == {
            "location_id",
            "name",
            "elevation_m",
            "days",
        }
        assert feature["properties"]["elevation_m"] == 3328.0

    def test_geometry_is_lon_lat(self) -> None:
        """GeoJSON ordering is [longitude, latitude] per RFC 7946."""
        location = LocationFactory.create(latitude=46.1, longitude=7.4)
        ResortLocationFactory.create(location=location)

        feature = _get()["features"][0]

        assert feature["geometry"]["coordinates"] == [7.4, 46.1]

    def test_days_carries_today_and_the_forward_window(self) -> None:
        """One row per location supplies the whole week via ``forecast``."""
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
        WeatherFactory.create(
            location=location,
            observed_on=today,
            weather_code=3,
            temperature_2m_max=4.5,
            forecast=[
                {
                    "date": tomorrow.isoformat(),
                    "weather_code": 71,
                    "sunrise": f"{tomorrow}T06:30:00+00:00",
                    "sunset": f"{tomorrow}T20:15:00+00:00",
                    "temperature_2m_max": 1.0,
                }
            ],
        )

        days = _get()["features"][0]["properties"]["days"]

        assert days == {
            today.isoformat(): {"code": 3, "tmax": 4.5},
            tomorrow.isoformat(): {"code": 71, "tmax": 1.0},
        }

    def test_a_location_with_no_row_gets_a_feature_with_no_days(self) -> None:
        """Absence would be indistinguishable from "we don't know this place".

        The map filters an empty ``days`` out at draw time, so the feature
        costs nothing and the payload stays a complete answer to "which
        locations are public".
        """
        location = LocationFactory.create(name="Unfetched")
        ResortLocationFactory.create(location=location)

        assert _get()["features"][0]["properties"]["days"] == {}


@pytest.mark.django_db
class TestCaching:
    """The feed is shared-cached, so it must not vary on the session."""

    def test_response_is_publicly_cacheable_and_does_not_vary_on_cookie(
        self,
    ) -> None:
        """``Vary: Cookie`` would defeat the CDN cache entirely.

        The ``@vary_on_headers`` decorator is necessary but not sufficient —
        the path also has to be in ``_POSTHOG_EXEMPT_PATHS`` or the
        middleware's ``request.user`` read makes SessionMiddleware add the
        header back.
        """
        response = Client().get(reverse("api:weather_geojson"))

        assert "public" in response["Cache-Control"]
        assert "Cookie" not in response.get("Vary", "")

    def test_freshness_headers_report_the_oldest_row(self) -> None:
        """A payload is only as fresh as its stalest constituent."""
        today = timezone.localdate()
        older = timezone.now() - datetime.timedelta(hours=6)
        for offset, fetched_at in ((0.0, timezone.now()), (0.5, older)):
            location = LocationFactory.create(latitude=46.1 + offset)
            ResortLocationFactory.create(location=location)
            WeatherFactory.create(
                location=location, observed_on=today, fetched_at=fetched_at
            )

        response = Client().get(reverse("api:weather_geojson"))

        generated_at = datetime.datetime.fromisoformat(response["X-Data-Generated-At"])
        assert abs((generated_at - older).total_seconds()) < 1
