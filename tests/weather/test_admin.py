"""
tests/weather/test_admin.py — Tests for WeatherAdmin.

The one thing about this admin that is not boilerplate: a past row renders
read-only, so the immutability guard never has to fire from a form. Raising
is right for a shell write and wrong for a curator who would otherwise get
a 500 for opening last week's row and pressing Save.
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.admin.sites import AdminSite
from django.utils import timezone

from apps.weather.admin import WeatherAdmin
from apps.weather.models import Weather
from tests.factories import WeatherFactory


@pytest.fixture
def admin_instance() -> WeatherAdmin:
    """Return a WeatherAdmin bound to a bare AdminSite."""
    return WeatherAdmin(Weather, AdminSite())


@pytest.mark.django_db
class TestWeatherAdminImmutability:
    """Tests for the read-only-when-past behaviour."""

    def test_a_past_row_has_every_field_read_only(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """Nothing on a past row can be edited through the form."""
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        weather = WeatherFactory.create(observed_on=yesterday)

        readonly = admin_instance.get_readonly_fields(None, weather)  # type: ignore[arg-type]

        for field in ("location", "observed_on", "weather_code", "hourly", "forecast"):
            assert field in readonly

    def test_todays_row_stays_editable(self, admin_instance: WeatherAdmin) -> None:
        """A live row is rewritten four times a day; correcting one is normal."""
        weather = WeatherFactory.create(observed_on=timezone.localdate())

        readonly = admin_instance.get_readonly_fields(None, weather)  # type: ignore[arg-type]

        assert "weather_code" not in readonly
        assert "observed_on" not in readonly

    def test_the_add_form_is_editable(self, admin_instance: WeatherAdmin) -> None:
        """With no object there is no date to judge, so nothing is locked."""
        readonly = admin_instance.get_readonly_fields(None, None)  # type: ignore[arg-type]

        assert "weather_code" not in readonly

    def test_identity_and_timestamps_are_always_read_only(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """id/uuid/created_at/updated_at are never editable, on any row."""
        weather = WeatherFactory.create(observed_on=timezone.localdate())

        readonly = admin_instance.get_readonly_fields(None, weather)  # type: ignore[arg-type]

        assert {"id", "uuid", "created_at", "updated_at"} <= set(readonly)

    def test_deletion_is_refused(self, admin_instance: WeatherAdmin) -> None:
        """A row is the record that we said something; removing it is not a fix."""
        assert admin_instance.has_delete_permission(None) is False  # type: ignore[arg-type]
