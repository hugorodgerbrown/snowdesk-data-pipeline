"""
tests/weather/services/test_upsert.py — Tests for upsert_weather.

The three outcomes the function exists to distinguish — create, refine,
refuse — plus the property every read path depends on: that today's row is
updated in place rather than appended, so a ``.first()`` on the unique
constraint stays correct.
"""

from __future__ import annotations

import datetime
from unittest import mock

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.weather.exceptions import ImmutableWeatherRowError
from apps.weather.models import Weather
from apps.weather.services.upsert import upsert_weather
from tests.factories import LocationFactory, WeatherFactory


def _fields(**overrides: object) -> dict[str, object]:
    """Return a minimal valid field set for a Weather write."""
    return {
        "weather_code": 3,
        "sunrise": timezone.now(),
        "sunset": timezone.now(),
        **overrides,
    }


@pytest.mark.django_db
class TestUpsertWeather:
    """Tests for the create / refine / refuse decision."""

    def test_creates_when_the_row_is_absent(self) -> None:
        """No row for the day yet — one is created and flagged created."""
        location = LocationFactory.create()
        today = timezone.localdate()

        weather, created = upsert_weather(location, today, **_fields())

        assert created is True
        assert weather.location == location
        assert weather.observed_on == today

    def test_creates_a_past_dated_row_when_absent(self) -> None:
        """A day never recorded can be recorded, whatever its date.

        Recording is not rewriting. This is the entry point a historical
        backfill (SNOW-731) uses, so it must not need an exception to the
        immutability rule.
        """
        location = LocationFactory.create()
        long_ago = timezone.localdate() - datetime.timedelta(days=90)

        weather, created = upsert_weather(location, long_ago, **_fields())

        assert created is True
        assert weather.observed_on == long_ago

    def test_refines_todays_row_in_place(self) -> None:
        """The four daily runs update one row rather than appending rows.

        This is the property every read path rests on: because there is
        only ever one row per (location, day), a read stays ``.first()``
        on the unique constraint and never has to sort by fetched_at.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        first, _ = upsert_weather(location, today, **_fields(weather_code=3))

        second, created = upsert_weather(location, today, **_fields(weather_code=71))

        assert created is False
        assert second.pk == first.pk
        assert Weather.objects.for_location(location).count() == 1
        second.refresh_from_db()
        assert second.weather_code == 71

    def test_refines_a_future_row_in_place(self) -> None:
        """Tomorrow is still writable — the boundary is today, inclusive."""
        location = LocationFactory.create()
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        upsert_weather(location, tomorrow, **_fields(weather_code=3))

        _, created = upsert_weather(location, tomorrow, **_fields(weather_code=71))

        assert created is False

    def test_refuses_to_rewrite_a_past_row(self) -> None:
        """An existing past row raises rather than being silently skipped.

        Raising is the point. The bug this model replaces (SNOW-628) wrote
        zero rows for months because the write path degraded quietly; a
        caller trying to rewrite history has a bug and should hear about it.
        """
        location = LocationFactory.create()
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        WeatherFactory.create(location=location, observed_on=yesterday, weather_code=3)

        with pytest.raises(ImmutableWeatherRowError):
            upsert_weather(location, yesterday, **_fields(weather_code=71))

    def test_a_refused_write_changes_nothing(self) -> None:
        """The refusal happens before any column is touched."""
        location = LocationFactory.create()
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        WeatherFactory.create(
            location=location,
            observed_on=yesterday,
            weather_code=3,
            temperature_2m_max=4.5,
        )

        with pytest.raises(ImmutableWeatherRowError):
            upsert_weather(
                location,
                yesterday,
                **_fields(weather_code=71, temperature_2m_max=-99.0),
            )

        stored = Weather.objects.get(location=location, observed_on=yesterday)
        assert stored.weather_code == 3
        assert stored.temperature_2m_max == 4.5

    def test_fetched_at_defaults_to_now(self) -> None:
        """fetched_at means 'when this row was last written' — so, now."""
        location = LocationFactory.create()
        before = timezone.now()

        weather, _ = upsert_weather(location, timezone.localdate(), **_fields())

        assert weather.fetched_at >= before

    def test_fetched_at_advances_on_a_refinement(self) -> None:
        """A second run stamps the row again, so staleness stays readable."""
        location = LocationFactory.create()
        today = timezone.localdate()
        first, _ = upsert_weather(location, today, **_fields())
        original = first.fetched_at

        second, _ = upsert_weather(location, today, **_fields())

        assert second.fetched_at > original

    def test_a_midnight_straddle_between_the_check_and_the_save_fails_safe(
        self,
    ) -> None:
        """A write spanning local midnight is still refused, not silently written.

        upsert_weather checks ``observed_on < timezone.localdate()`` once,
        then calls ``existing.save()``. ``Weather.save()`` re-checks the
        same rule independently against the database (its backstop for
        writes that never come through this service). If the day rolls
        over in the gap between those two checks, they can disagree; this
        forces that straddle and asserts the backstop still catches it
        rather than the row silently getting rewritten past its day.
        """
        location = LocationFactory.create()
        with freeze_time("2026-01-01 23:59:59.900") as frozen:
            today = timezone.localdate()
            WeatherFactory.create(location=location, observed_on=today, weather_code=3)
            original_save = Weather.save

            def save_after_midnight(
                self: Weather, *args: object, **kwargs: object
            ) -> None:
                frozen.move_to("2026-01-02 00:00:00.100")
                original_save(self, *args, **kwargs)

            with (
                mock.patch.object(Weather, "save", save_after_midnight),
                pytest.raises(ImmutableWeatherRowError),
            ):
                upsert_weather(location, today, **_fields(weather_code=71))
