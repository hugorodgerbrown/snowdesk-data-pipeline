"""
tests/weather/services/test_nearest.py — the curated forecast a place borrows.

Covers ``apps.weather.services.nearest`` (Hugo, 2026-10-28: only Snowdesk
decides where a forecast is fetched) and the one sentence every borrowing
surface prints about it, ``includes/_forecast_source.html``.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.weather.models import Weather
from apps.weather.services.nearest import (
    forecast_candidates,
    nearest_candidate,
    nearest_forecast,
)
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortLocationFactory,
    WeatherFactory,
)

DAY = datetime.date(2026, 10, 28)


@pytest.mark.django_db
class TestNearestForecast:
    """``nearest_forecast`` — the closest public point with a row for the day."""

    def test_the_closest_point_wins_at_any_distance(self) -> None:
        """No cap: a point 100 km away is still the answer when it is nearest."""
        far = LocationFactory.create(name="Far", latitude=46.9, longitude=7.4)
        ResortLocationFactory.create(location=far)
        WeatherFactory.create(location=far, observed_on=DAY)

        nearest = nearest_forecast(46.0, 7.4, DAY)

        assert nearest is not None
        assert nearest.location == far
        assert nearest.distance_km == pytest.approx(100.1, abs=0.1)
        assert str(nearest) == "Far (100.1 km)"

    def test_only_rows_for_the_day_count(self) -> None:
        """A closer point with no row that day loses to a farther one with one."""
        near = LocationFactory.create(latitude=46.01, longitude=7.4)
        ResortLocationFactory.create(location=near)
        WeatherFactory.create(location=near, observed_on=DAY - datetime.timedelta(1))
        centroid = LocationFactory.create(name="", latitude=46.2, longitude=7.4)
        MicroRegionFactory.create(name="Val de Bagnes", centroid_location=centroid)
        WeatherFactory.create(location=centroid, observed_on=DAY)

        nearest = nearest_forecast(46.0, 7.4, DAY)

        assert nearest is not None
        assert nearest.location == centroid
        assert nearest.label == "Val de Bagnes"

    def test_a_private_pin_is_never_borrowed(self) -> None:
        """A favourite's own row would name somebody's private place."""
        favourite = FavouriteFactory.create(
            location=LocationFactory.create(latitude=46.0, longitude=7.4)
        )
        WeatherFactory.create(location=favourite.location, observed_on=DAY)

        assert nearest_forecast(46.0, 7.4, DAY) is None

    def test_the_same_place_is_flagged(self) -> None:
        """A saved resort's own pin is, near enough, the forecast point."""
        point = LocationFactory.create(latitude=46.0, longitude=7.4, elevation_m=1499.6)
        ResortLocationFactory.create(location=point)
        WeatherFactory.create(location=point, observed_on=DAY)

        nearest = nearest_forecast(46.0, 7.4, DAY)

        assert nearest is not None
        assert nearest.is_same_place
        assert nearest.elevation_m == 1500


@pytest.mark.django_db
class TestForecastCandidateHelpers:
    """The scan and the choice ``nearest_forecast`` composes (SNOW-965)."""

    def test_the_helpers_agree_with_nearest_forecast(self) -> None:
        """One scan, then a pick per place, answers as the composition does."""
        near = LocationFactory.create(latitude=46.1, longitude=7.4)
        ResortLocationFactory.create(location=near)
        WeatherFactory.create(location=near, observed_on=DAY)
        far = LocationFactory.create(latitude=46.9, longitude=7.4)
        ResortLocationFactory.create(location=far)
        WeatherFactory.create(location=far, observed_on=DAY)

        candidates = forecast_candidates(DAY)
        for latitude in (46.0, 46.6, 47.0):
            picked = nearest_candidate(latitude, 7.4, candidates)
            composed = nearest_forecast(latitude, 7.4, DAY)
            assert picked is not None
            assert composed is not None
            assert picked[0].short_id == composed.location.short_id
            assert picked[1] == pytest.approx(composed.distance_km)

    def test_candidates_are_one_query(
        self, django_assert_num_queries: Callable[..., Any]
    ) -> None:
        """The scan reads bare coordinates in a single query."""
        for latitude in (46.0, 46.5):
            point = LocationFactory.create(latitude=latitude, longitude=7.4)
            ResortLocationFactory.create(location=point)
            WeatherFactory.create(location=point, observed_on=DAY)

        with django_assert_num_queries(1):
            candidates = forecast_candidates(DAY)

        assert {c.short_id for c in candidates} == {
            row.location.short_id for row in Weather.objects.all()
        }

    def test_no_candidates_picks_nothing(self) -> None:
        """An empty day borrows nothing."""
        assert forecast_candidates(DAY) == []
        assert nearest_candidate(46.0, 7.4, []) is None


def _source(**overrides: object) -> dict[str, object]:
    """A stand-in for a NearestForecast / TripWeather, as the partial reads it."""
    return {
        "label": "Mont Fort",
        "elevation_m": 3329,
        "distance_km": 2.13,
        "is_same_place": False,
        **overrides,
    }


def _render(source: dict[str, object], anchor: str) -> str:
    """Render the sentence, whitespace collapsed."""
    html = render_to_string(
        "includes/_forecast_source.html", {"source": source, "anchor": anchor}
    )
    return " ".join(html.split())


class TestForecastSourceSentence:
    """``includes/_forecast_source.html`` — one sentence, three anchors."""

    @pytest.mark.parametrize(
        ("anchor", "tail"),
        [
            ("pin", "from this pin."),
            ("meeting", "from the meeting point."),
            ("start", "from the route's start."),
        ],
    )
    def test_each_anchor_names_point_height_and_distance(
        self, anchor: str, tail: str
    ) -> None:
        assert (
            _render(_source(), anchor)
            == f"Forecast for Mont Fort (3329 m), 2.1 km {tail}"
        )

    def test_an_unknown_height_is_left_out(self) -> None:
        assert _render(_source(elevation_m=None), "pin") == (
            "Forecast for Mont Fort, 2.1 km from this pin."
        )

    def test_the_same_place_drops_the_distance(self) -> None:
        assert _render(_source(is_same_place=True), "pin") == (
            "Forecast for Mont Fort (3329 m)."
        )
        assert _render(_source(is_same_place=True, elevation_m=None), "start") == (
            "Forecast for Mont Fort."
        )
