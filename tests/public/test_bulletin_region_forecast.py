"""
tests/public/test_bulletin_region_forecast.py — SNOW-696 bulletin weather.

The bulletin page is the most-visited page on the site and carried the
thinnest weather anywhere in the product: a condition icon, a hi/lo and a
snowfall total, for one day, because it read ``WeatherSnapshot``. SNOW-696
anchors each micro-region to a ``Location`` so it can render the same
multi-day panel — with wind and freezing level — the resort page already has.

Covers:
  - The panel renders on the live view once the region is anchored.
  - It is omitted when the region has no centroid location, when the cell
    has no rows, and — the one that matters — on a historical date.
  - The masthead's WeatherSnapshot is untouched either way.
"""

from __future__ import annotations

import datetime
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.regions.models import MicroRegion
from tests.factories import (
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    LocationFactory,
    MicroRegionFactory,
)

PANEL = 'data-testid="bulletin-forecast-section"'
MASTHEAD = 'data-testid="bulletin-header"'


def _url(region: MicroRegion, day: datetime.date) -> str:
    """Build the dated bulletin URL for a region.

    Args:
        region: The MicroRegion.
        day: The calendar date to render.

    Returns:
        The URL path.

    """
    return reverse(
        "public:bulletin_date",
        kwargs={
            "region_id": region.region_id,
            "slug": region.slug,
            "date_str": day.isoformat(),
        },
    )


def _anchored_region(with_weather: bool = True) -> MicroRegion:
    """Return a MicroRegion anchored to a cell, optionally carrying weather.

    Args:
        with_weather: Whether to write a forward window of
            ``ForecastPointWeather`` rows for the cell.

    Returns:
        The MicroRegion.

    """
    cell = ForecastPointFactory.create()
    region = MicroRegionFactory.create(
        centre={"lat": 46.1, "lon": 7.4},
        centroid_location=LocationFactory.create(
            anonymous=True, latitude=46.1, longitude=7.4, forecast_cell=cell
        ),
    )
    if with_weather:
        today = timezone.localdate()
        for offset in range(3):
            ForecastPointWeatherFactory.create(
                forecast_point=cell, valid_for_date=today + timedelta(days=offset)
            )
    return region


@pytest.mark.django_db
class TestBulletinRegionForecast:
    """The forecast panel on the live bulletin view."""

    def test_panel_renders_for_an_anchored_region(self, client: Client) -> None:
        """An anchored region with weather shows the multi-day panel."""
        region = _anchored_region()

        response = client.get(_url(region, timezone.localdate()), follow=True)

        assert response.status_code == 200
        assert PANEL in response.content.decode()

    def test_panel_is_omitted_without_a_centroid_location(self, client: Client) -> None:
        """A region the backfill has not reached renders without the panel.

        No empty state: a section promising weather it cannot show is worse
        than no section.
        """
        region = MicroRegionFactory.create(centre={"lat": 46.1, "lon": 7.4})

        response = client.get(_url(region, timezone.localdate()), follow=True)

        assert response.status_code == 200
        assert PANEL not in response.content.decode()

    def test_panel_is_omitted_when_the_cell_has_no_rows(self, client: Client) -> None:
        """An anchored region whose cell has not been fetched yet."""
        region = _anchored_region(with_weather=False)

        response = client.get(_url(region, timezone.localdate()), follow=True)

        assert response.status_code == 200
        assert PANEL not in response.content.decode()

    def test_panel_is_omitted_on_a_historical_date(self, client: Client) -> None:
        """The forward window is a *forecast*.

        Rendering it beside a bulletin from last week would put next week's
        weather under last week's danger rating — which is not a cosmetic
        problem on a safety page.
        """
        region = _anchored_region()
        past = timezone.localdate() - timedelta(days=7)

        response = client.get(_url(region, past), follow=True)

        assert response.status_code == 200
        assert PANEL not in response.content.decode()

    def test_the_masthead_is_untouched(self, client: Client) -> None:
        """WeatherSnapshot keeps its job as the day/night visual.

        The panel is the numbers; the masthead is the decoration. Adding
        one must not displace the other.
        """
        region = _anchored_region()

        response = client.get(_url(region, timezone.localdate()), follow=True)

        body = response.content.decode()
        assert MASTHEAD in body
        assert PANEL in body
