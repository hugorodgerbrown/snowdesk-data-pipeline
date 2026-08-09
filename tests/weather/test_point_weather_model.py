"""
tests/weather/test_point_weather_model.py — Tests for the ForecastPointWeather model.

Covers:
  - Factory produces a valid instance via .create().
  - to_string() / __str__() format.
  - ForecastPointWeatherQuerySet.for_date() filter.
  - ForecastPointWeatherQuerySet.forecast_for_point() filter/ordering
    (SNOW-417).
  - freezing_level_height / hourly_series persistence (SNOW-417).
  - unique_together constraint raises IntegrityError on duplicate
    (forecast_point, date).
  - ordering: newest date first, then forecast_point__id ascending.
  - Deleting the linked ForecastPoint cascades to ForecastPointWeather.
"""

import datetime

import pytest
from django.db import IntegrityError

from apps.weather.models import ForecastPointWeather
from tests.factories import ForecastPointFactory, ForecastPointWeatherFactory


@pytest.mark.django_db
class TestForecastPointWeatherFactory:
    """The factory produces valid, well-formed ForecastPointWeather instances."""

    def test_create_returns_forecast_point_weather(self) -> None:
        """ForecastPointWeatherFactory.create() returns a persisted row."""
        weather = ForecastPointWeatherFactory.create()
        assert isinstance(weather, ForecastPointWeather)
        assert weather.pk is not None

    def test_defaults_are_sensible(self) -> None:
        """Default factory values satisfy model constraints."""
        weather = ForecastPointWeatherFactory.create()
        assert weather.weather_code == 0
        assert weather.sunrise.tzinfo is not None
        assert weather.sunset.tzinfo is not None
        assert weather.fetched_at is not None
        assert weather.forecast_point is not None

    def test_extended_fields_default_non_null(self) -> None:
        """The factory's extended fields default to non-null values."""
        weather = ForecastPointWeatherFactory.create()
        assert weather.temperature_2m_max is not None
        assert weather.temperature_2m_min is not None
        assert weather.snowfall_sum is not None
        assert weather.wind_speed_10m_max is not None
        assert weather.wind_direction_10m_dominant is not None
        assert weather.uv_index_max is not None

    def test_override_weather_code(self) -> None:
        """weather_code can be overridden at factory call time."""
        weather = ForecastPointWeatherFactory.create(weather_code=61)
        assert weather.weather_code == 61

    def test_freezing_level_height_and_hourly_series_persist(self) -> None:
        """freezing_level_height and hourly_series round-trip through the DB."""
        weather = ForecastPointWeatherFactory.create()
        weather.refresh_from_db()
        assert weather.freezing_level_height == 1800.0
        assert weather.hourly_series is not None
        assert len(weather.hourly_series) == 2
        assert weather.hourly_series[0]["temperature_2m"] == -2.0

    def test_freezing_level_height_and_hourly_series_nullable(self) -> None:
        """Both new fields accept None (Open-Meteo may omit the hourly block)."""
        weather = ForecastPointWeatherFactory.create(
            freezing_level_height=None, hourly_series=None
        )
        weather.refresh_from_db()
        assert weather.freezing_level_height is None
        assert weather.hourly_series is None


@pytest.mark.django_db
class TestForecastPointWeatherStr:
    """__str__ / to_string() format."""

    def test_to_string_format(self) -> None:
        """to_string() returns '<point.to_string()> <date> wmo=<code>'."""
        point = ForecastPointFactory.create(
            latitude=46.8, longitude=7.5, elevation=1500.0
        )
        weather = ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=datetime.date(2026, 5, 1),
            weather_code=3,
        )
        assert weather.to_string() == "46.80000,7.50000 @1500m 2026-05-01 wmo=3"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        weather = ForecastPointWeatherFactory.create()
        assert str(weather) == weather.to_string()


@pytest.mark.django_db
class TestForecastPointWeatherQuerySet:
    """ForecastPointWeatherQuerySet.for_date() filters correctly."""

    def test_for_date_returns_matching_rows(self) -> None:
        """for_date() returns only rows with matching valid_for_date."""
        target = datetime.date(2026, 5, 1)
        other = datetime.date(2026, 4, 30)
        weather_target = ForecastPointWeatherFactory.create(valid_for_date=target)
        ForecastPointWeatherFactory.create(
            forecast_point=ForecastPointFactory.create(latitude=47.0, longitude=8.0),
            valid_for_date=other,
        )

        result = ForecastPointWeather.objects.for_date(target)
        assert list(result) == [weather_target]

    def test_for_date_empty_when_no_match(self) -> None:
        """for_date() returns an empty queryset when no rows match."""
        ForecastPointWeatherFactory.create(valid_for_date=datetime.date(2026, 4, 30))
        result = ForecastPointWeather.objects.for_date(datetime.date(2026, 5, 1))
        assert result.count() == 0


@pytest.mark.django_db
class TestForecastPointWeatherForecastForPoint:
    """ForecastPointWeatherQuerySet.forecast_for_point() filters and orders correctly."""

    def test_filters_to_one_point_from_start_date(self) -> None:
        """Only rows for the given point, from start_date onwards, are returned."""
        point = ForecastPointFactory.create()
        other_point = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        start = datetime.date(2026, 5, 1)
        before = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start - datetime.timedelta(days=1)
        )
        on_start = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start
        )
        after = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start + datetime.timedelta(days=1)
        )
        ForecastPointWeatherFactory.create(
            forecast_point=other_point, valid_for_date=start
        )

        result = list(ForecastPointWeather.objects.forecast_for_point(point, start))

        assert before not in result
        assert result == [on_start, after]

    def test_orders_ascending_by_date(self) -> None:
        """Rows are returned oldest-first, opposite of the model's default ordering."""
        point = ForecastPointFactory.create()
        start = datetime.date(2026, 5, 1)
        day2 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start + datetime.timedelta(days=2)
        )
        day0 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start
        )
        day1 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start + datetime.timedelta(days=1)
        )

        result = list(ForecastPointWeather.objects.forecast_for_point(point, start))

        assert result == [day0, day1, day2]

    def test_empty_when_no_rows_at_or_after_start(self) -> None:
        """Returns an empty queryset when every row predates start_date."""
        point = ForecastPointFactory.create()
        start = datetime.date(2026, 5, 1)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=start - datetime.timedelta(days=1)
        )

        result = ForecastPointWeather.objects.forecast_for_point(point, start)

        assert result.count() == 0


@pytest.mark.django_db
class TestForecastPointWeatherConstraints:
    """Model-level integrity constraints."""

    def test_unique_together_point_date(self) -> None:
        """Inserting two rows for the same (forecast_point, date) raises IntegrityError."""
        point = ForecastPointFactory.create()
        target = datetime.date(2026, 5, 1)
        ForecastPointWeatherFactory.create(forecast_point=point, valid_for_date=target)

        with pytest.raises(IntegrityError):
            ForecastPointWeatherFactory.create(
                forecast_point=point, valid_for_date=target
            )

    def test_different_points_same_date_allowed(self) -> None:
        """Two rows with different points but the same date are allowed."""
        point_a = ForecastPointFactory.create()
        point_b = ForecastPointFactory.create(latitude=47.0, longitude=8.0)
        target = datetime.date(2026, 5, 1)
        ForecastPointWeatherFactory.create(
            forecast_point=point_a, valid_for_date=target
        )
        weather_b = ForecastPointWeatherFactory.create(
            forecast_point=point_b, valid_for_date=target
        )
        assert weather_b.pk is not None

    def test_same_point_different_dates_allowed(self) -> None:
        """Two rows for the same point on different dates are allowed."""
        point = ForecastPointFactory.create()
        weather1 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 5, 1)
        )
        weather2 = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 5, 2)
        )
        assert weather1.pk != weather2.pk

    def test_cascade_delete_from_forecast_point(self) -> None:
        """Deleting the linked ForecastPoint also deletes the ForecastPointWeather."""
        point = ForecastPointFactory.create()
        weather = ForecastPointWeatherFactory.create(forecast_point=point)
        weather_pk = weather.pk
        point.delete()
        assert not ForecastPointWeather.objects.filter(pk=weather_pk).exists()


@pytest.mark.django_db
class TestForecastPointWeatherOrdering:
    """Default ordering: newest date first, then forecast_point__id ascending."""

    def test_ordering_newest_date_first(self) -> None:
        """Queryset returns most recent valid_for_date first."""
        point = ForecastPointFactory.create()
        weather_old = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 4, 30)
        )
        weather_new = ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=datetime.date(2026, 5, 1)
        )
        qs = list(ForecastPointWeather.objects.all())
        assert qs[0] == weather_new
        assert qs[1] == weather_old


@pytest.mark.django_db
class TestForecastPointWeatherTzAware:
    """Datetimes are stored and returned tz-aware."""

    def test_sunrise_is_tz_aware(self) -> None:
        """Sunrise returned from the DB carries timezone info."""
        weather = ForecastPointWeatherFactory.create()
        weather.refresh_from_db()
        assert weather.sunrise.tzinfo is not None

    def test_sunset_is_tz_aware(self) -> None:
        """Sunset returned from the DB carries timezone info."""
        weather = ForecastPointWeatherFactory.create()
        weather.refresh_from_db()
        assert weather.sunset.tzinfo is not None

    def test_fetched_at_is_tz_aware(self) -> None:
        """fetched_at returned from the DB carries timezone info."""
        weather = ForecastPointWeatherFactory.create()
        weather.refresh_from_db()
        assert weather.fetched_at.tzinfo is not None
