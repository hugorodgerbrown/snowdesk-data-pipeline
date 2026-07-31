"""
tests/bulletins/test_forecast_history_model.py — Tests for ForecastPointWeatherHistory.

Covers:
  - Factory produces a valid instance via .create().
  - to_string() / __str__() format, including the signed lead-days marker.
  - ForecastPointWeatherHistoryQuerySet.convergence_for() filter/ordering —
    oldest issue first, which is the inverse of the model's own ordering.
  - unique_together constraint raises IntegrityError on a duplicate
    (forecast_point, valid_for_date, issued_date).
  - The same valid_for_date may be stored many times under different
    issue dates — the whole point of the model (SNOW-575).
  - Nullable payload fields tolerate None.
  - ordering: newest forecast day first, then issue date ascending.
  - Deleting the linked ForecastPoint cascades.
"""

import datetime

import pytest
from django.db import IntegrityError

from apps.bulletins.models import ForecastPointWeatherHistory
from tests.factories import (
    ForecastPointFactory,
    ForecastPointWeatherHistoryFactory,
)

VALID_DAY = datetime.date(2026, 5, 8)


@pytest.mark.django_db
class TestForecastPointWeatherHistoryFactory:
    """The factory produces valid, well-formed instances."""

    def test_create_returns_history_row(self) -> None:
        """ForecastPointWeatherHistoryFactory.create() returns a persisted row."""
        row = ForecastPointWeatherHistoryFactory.create()
        assert isinstance(row, ForecastPointWeatherHistory)
        assert row.pk is not None

    def test_defaults_are_sensible(self) -> None:
        """Default factory values satisfy model constraints."""
        row = ForecastPointWeatherHistoryFactory.create()
        assert row.weather_code == 0
        assert row.lead_days == 3
        assert row.valid_for_date - row.issued_date == datetime.timedelta(days=3)
        assert row.fetched_at is not None
        assert row.forecast_point is not None

    def test_nullable_payload_fields_accept_none(self) -> None:
        """Every field beyond weather_code tolerates None."""
        row = ForecastPointWeatherHistoryFactory.create(
            temperature_2m_max=None,
            temperature_2m_min=None,
            precipitation_sum=None,
            snowfall_sum=None,
            wind_speed_10m_max=None,
            freezing_level_height=None,
        )
        row.refresh_from_db()
        assert row.temperature_2m_max is None
        assert row.freezing_level_height is None
        assert row.weather_code == 0


@pytest.mark.django_db
class TestToString:
    """to_string() renders point, forecast day, issue day and signed lead."""

    def test_to_string_format(self) -> None:
        """A positive lead renders with an explicit + sign."""
        row = ForecastPointWeatherHistoryFactory.create(
            valid_for_date=VALID_DAY,
            issued_date=datetime.date(2026, 5, 5),
            lead_days=3,
            weather_code=2,
        )
        expected = (
            f"{row.forecast_point.to_string()} 2026-05-08 issued 2026-05-05 (+3d) wmo=2"
        )
        assert row.to_string() == expected

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        row = ForecastPointWeatherHistoryFactory.create()
        assert str(row) == row.to_string()

    def test_zero_lead_renders_signed(self) -> None:
        """A day-of row renders +0d rather than a bare 0."""
        row = ForecastPointWeatherHistoryFactory.create(
            valid_for_date=VALID_DAY,
            issued_date=VALID_DAY,
            lead_days=0,
        )
        assert "(+0d)" in row.to_string()


@pytest.mark.django_db
class TestConvergenceFor:
    """convergence_for() returns one day's series, oldest issue first."""

    def test_returns_series_in_issue_order(self) -> None:
        """Rows come back ascending by issued_date, not the model default."""
        point = ForecastPointFactory.create()
        for lead in (3, 0, 5, 1):
            ForecastPointWeatherHistoryFactory.create(
                forecast_point=point,
                valid_for_date=VALID_DAY,
                issued_date=VALID_DAY - datetime.timedelta(days=lead),
                lead_days=lead,
            )

        series = ForecastPointWeatherHistory.objects.convergence_for(point, VALID_DAY)

        assert [row.lead_days for row in series] == [5, 3, 1, 0]

    def test_excludes_other_days_and_points(self) -> None:
        """The filter is scoped to both the point and the forecast day."""
        point = ForecastPointFactory.create()
        # Distinct coordinates: ForecastPoint is unique on the quantised
        # (lat_cell, lon_cell, elevation_band) triple, and the factory's
        # defaults are fixed.
        other_point = ForecastPointFactory.create(latitude=46.5, longitude=7.9)
        wanted = ForecastPointWeatherHistoryFactory.create(
            forecast_point=point, valid_for_date=VALID_DAY
        )
        ForecastPointWeatherHistoryFactory.create(
            forecast_point=point,
            valid_for_date=VALID_DAY + datetime.timedelta(days=1),
        )
        ForecastPointWeatherHistoryFactory.create(
            forecast_point=other_point, valid_for_date=VALID_DAY
        )

        series = ForecastPointWeatherHistory.objects.convergence_for(point, VALID_DAY)

        assert list(series) == [wanted]

    def test_empty_for_unknown_day(self) -> None:
        """A day with no history returns an empty queryset, not an error."""
        point = ForecastPointFactory.create()
        assert not ForecastPointWeatherHistory.objects.convergence_for(
            point, VALID_DAY
        ).exists()


@pytest.mark.django_db
class TestUniqueness:
    """The key is (forecast_point, valid_for_date, issued_date)."""

    def test_duplicate_triple_raises(self) -> None:
        """Two rows for the same point, day, and issue date collide."""
        point = ForecastPointFactory.create()
        ForecastPointWeatherHistoryFactory.create(
            forecast_point=point,
            valid_for_date=VALID_DAY,
            issued_date=datetime.date(2026, 5, 5),
        )
        with pytest.raises(IntegrityError):
            ForecastPointWeatherHistoryFactory.create(
                forecast_point=point,
                valid_for_date=VALID_DAY,
                issued_date=datetime.date(2026, 5, 5),
            )

    def test_same_day_different_issue_dates_coexist(self) -> None:
        """One forecast day may be stored once per issue date.

        This is the behaviour the model exists for — ForecastPointWeather
        can hold only the last of these.
        """
        point = ForecastPointFactory.create()
        for lead in range(4):
            ForecastPointWeatherHistoryFactory.create(
                forecast_point=point,
                valid_for_date=VALID_DAY,
                issued_date=VALID_DAY - datetime.timedelta(days=lead),
                lead_days=lead,
            )

        assert (
            ForecastPointWeatherHistory.objects.filter(
                forecast_point=point, valid_for_date=VALID_DAY
            ).count()
            == 4
        )


@pytest.mark.django_db
class TestOrderingAndCascade:
    """Default ordering and FK cascade behaviour."""

    def test_default_ordering_newest_day_then_issue_ascending(self) -> None:
        """Ordering is -valid_for_date, then issued_date ascending."""
        point = ForecastPointFactory.create()
        older_day = ForecastPointWeatherHistoryFactory.create(
            forecast_point=point,
            valid_for_date=VALID_DAY - datetime.timedelta(days=1),
            issued_date=datetime.date(2026, 5, 4),
        )
        later_issue = ForecastPointWeatherHistoryFactory.create(
            forecast_point=point,
            valid_for_date=VALID_DAY,
            issued_date=datetime.date(2026, 5, 6),
        )
        earlier_issue = ForecastPointWeatherHistoryFactory.create(
            forecast_point=point,
            valid_for_date=VALID_DAY,
            issued_date=datetime.date(2026, 5, 4),
        )

        assert list(ForecastPointWeatherHistory.objects.all()) == [
            earlier_issue,
            later_issue,
            older_day,
        ]

    def test_deleting_point_cascades(self) -> None:
        """Deleting the ForecastPoint removes its history rows."""
        row = ForecastPointWeatherHistoryFactory.create()
        row.forecast_point.delete()
        assert not ForecastPointWeatherHistory.objects.filter(pk=row.pk).exists()
