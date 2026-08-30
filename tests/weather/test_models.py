"""
tests/weather/test_models.py — Tests for the Weather model.

Covers the model kit (``to_string``, ordering, the custom queryset, the
unique constraint) and the ``save()`` immutability backstop. The service
that callers actually go through is tested in
``tests/weather/services/test_upsert.py``; these tests are about the model
holding the line on its own, for the writes that never reach the service.
"""

from __future__ import annotations

import datetime

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.weather.exceptions import ImmutableWeatherRowError
from apps.weather.models import Weather
from tests.factories import LocationFactory, WeatherFactory


@pytest.mark.django_db
class TestWeatherModel:
    """Tests for the Weather model's own behaviour."""

    def test_to_string_carries_location_date_and_code(self) -> None:
        """to_string() identifies the row by its location, day and code."""
        location = LocationFactory.create(name="Mont Fort")
        weather = WeatherFactory.create(
            location=location,
            observed_on=datetime.date(2026, 8, 30),
            weather_code=3,
        )

        assert "Mont Fort" in weather.to_string()
        assert "2026-08-30" in weather.to_string()
        assert "code=3" in weather.to_string()
        assert str(weather) == weather.to_string()

    def test_ordering_is_newest_day_first(self) -> None:
        """Meta.ordering puts the most recent observed_on first."""
        location = LocationFactory.create()
        older = WeatherFactory.create(
            location=location, observed_on=datetime.date(2026, 8, 1)
        )
        newer = WeatherFactory.create(
            location=location, observed_on=datetime.date(2026, 8, 20)
        )

        assert list(Weather.objects.all()) == [newer, older]

    def test_one_row_per_location_and_day(self) -> None:
        """The unique constraint is enforced at the database level.

        Not merely by the service: a second row for the same day is what
        every read path's ``.first()`` assumes cannot exist.
        """
        location = LocationFactory.create()
        day = datetime.date(2026, 8, 30)
        WeatherFactory.create(location=location, observed_on=day)

        with pytest.raises(IntegrityError):
            Weather.objects.create(
                location=location,
                observed_on=day,
                fetched_at=timezone.now(),
                weather_code=1,
                sunrise=timezone.now(),
                sunset=timezone.now(),
            )

    def test_two_locations_may_share_a_day(self) -> None:
        """The constraint is on the pair, not on the date alone."""
        day = datetime.date(2026, 8, 30)
        WeatherFactory.create(location=LocationFactory.create(), observed_on=day)
        WeatherFactory.create(location=LocationFactory.create(), observed_on=day)

        assert Weather.objects.on_date(day).count() == 2

    def test_table_is_not_pinned_to_the_legacy_name(self) -> None:
        """The new table takes weather_weather, not a bulletins_* legacy name.

        SNOW-654 pinned the old models' db_table to keep their bulletins_*
        names across an app move. This is a genuinely new table and must
        carry none of that.
        """
        assert Weather._meta.db_table == "weather_weather"


@pytest.mark.django_db
class TestWeatherQuerySet:
    """Tests for WeatherQuerySet."""

    def test_past_and_current_partition_on_today(self) -> None:
        """past() and current() split the table at today, with today current."""
        location = LocationFactory.create()
        today = timezone.localdate()
        yesterday = WeatherFactory.create(
            location=location, observed_on=today - datetime.timedelta(days=1)
        )
        now = WeatherFactory.create(location=location, observed_on=today)
        tomorrow = WeatherFactory.create(
            location=location, observed_on=today + datetime.timedelta(days=1)
        )

        assert list(Weather.objects.past()) == [yesterday]
        assert set(Weather.objects.current()) == {now, tomorrow}

    def test_for_location_filters_to_one_place(self) -> None:
        """for_location() returns only that location's rows."""
        wanted = LocationFactory.create()
        WeatherFactory.create(location=wanted)
        WeatherFactory.create(location=LocationFactory.create())

        assert [w.location for w in Weather.objects.for_location(wanted)] == [wanted]


@pytest.mark.django_db
class TestWeatherImmutabilityBackstop:
    """Tests for the save() guard — the path that bypasses the service."""

    def test_direct_save_on_a_past_row_raises(self) -> None:
        """A shell or admin write to a past row is refused by the model itself.

        The service is the sanctioned path, but it is not the only one, so
        the rule has to hold at the model or an admin edit walks straight
        through it.
        """
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        weather = WeatherFactory.create(observed_on=yesterday, weather_code=3)

        weather.weather_code = 71
        with pytest.raises(ImmutableWeatherRowError):
            weather.save()

    def test_a_refused_save_leaves_the_stored_row_untouched(self) -> None:
        """The guard rejects before writing — it does not half-apply."""
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        weather = WeatherFactory.create(
            observed_on=yesterday, weather_code=3, temperature_2m_max=4.5
        )

        weather.weather_code = 71
        weather.temperature_2m_max = -99.0
        with pytest.raises(ImmutableWeatherRowError):
            weather.save()

        weather.refresh_from_db()
        assert weather.weather_code == 3
        assert weather.temperature_2m_max == 4.5

    def test_creating_a_past_dated_row_is_allowed(self) -> None:
        """Recording a day never recorded is not a rewrite.

        This is what lets a historical backfill (SNOW-731) use the same
        entry point rather than needing an exception carved out of the rule.
        """
        yesterday = timezone.localdate() - datetime.timedelta(days=1)

        weather = WeatherFactory.create(observed_on=yesterday)

        assert weather.pk is not None
        assert weather.is_immutable is True

    def test_todays_row_saves_normally(self) -> None:
        """A live row is rewritten four times a day and must stay writable."""
        weather = WeatherFactory.create(
            observed_on=timezone.localdate(), weather_code=3
        )

        weather.weather_code = 71
        weather.save()

        weather.refresh_from_db()
        assert weather.weather_code == 71
        assert weather.is_immutable is False
