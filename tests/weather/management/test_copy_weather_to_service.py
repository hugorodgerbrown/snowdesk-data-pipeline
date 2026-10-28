"""
tests/weather/management/test_copy_weather_to_service.py — copy_weather_to_service (SNOW-1275).

The one-time copy of production's past ``Weather`` into the weather
service: past days only, never one the service holds, never a user's
point, and nothing without ``--commit``.
"""

from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.ingest.public import SourceLocation, SourceWeather
from apps.locations.models import Location
from apps.weather.services.reference import mirror_reference_locations
from tests.factories import (
    LocationFactory,
    ResortLocationFactory,
    SourceWeatherFactory,
    WeatherFactory,
)

pytestmark = pytest.mark.django_db


def _station() -> Location:
    """Return a curated location, mirrored, so the service holds its station."""
    location = ResortLocationFactory.create(location__name="Attelas").location
    mirror_reference_locations()
    location.refresh_from_db()
    return location


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("copy_weather_to_service", *args, stdout=out)
    return out.getvalue()


def test_a_preview_counts_and_copies_nothing() -> None:
    """Read-only by default."""
    location = _station()
    WeatherFactory.create(
        location=location, observed_on=timezone.localdate() - datetime.timedelta(1)
    )
    out = _run()
    assert "Would copy 1 day(s)" in out
    assert not SourceWeather.objects.exists()


def test_commit_copies_past_days_the_service_lacks() -> None:
    """Today and a day already held are left; the columns travel whole."""
    location = _station()
    today = timezone.localdate()
    held_day = today - datetime.timedelta(days=2)
    past = WeatherFactory.create(
        location=location,
        observed_on=today - datetime.timedelta(days=1),
        weather_code=71,
        snowfall_sum=12.5,
    )
    WeatherFactory.create(location=location, observed_on=held_day)
    WeatherFactory.create(location=location, observed_on=today)
    station_row = SourceWeatherFactory.create(
        location=SourceLocation.objects.get(uuid=str(location.source_uuid)),
        observed_on=held_day,
    )

    _run("--commit")

    copied = SourceWeather.objects.exclude(pk=station_row.pk).get()
    assert copied.observed_on == past.observed_on
    assert (copied.weather_code, copied.snowfall_sum) == (71, 12.5)
    assert SourceWeather.objects.count() == 2
    assert _run("--commit").count("Copied 0 day(s)") == 1


def test_a_users_point_is_never_copied() -> None:
    """Only reference locations carry a source uuid."""
    point = LocationFactory.create()
    WeatherFactory.create(
        location=point, observed_on=timezone.localdate() - datetime.timedelta(1)
    )
    _run("--commit")
    assert not SourceWeather.objects.exists()
