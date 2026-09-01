"""
tests/weather/test_admin.py — Tests for WeatherAdmin.

Two things about this admin are not boilerplate:

* a past row renders read-only, so the immutability guard never has to fire
  from a form. Raising is right for a shell write and wrong for a curator
  who would otherwise get a 500 for opening last week's row and pressing
  Save;
* the two JSON series render as formatted, read-only blocks on **every**
  row. In a ``Textarea`` they are one unbroken line thousands of characters
  long — unreadable, and editable only by hand-writing valid JSON into it.
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

        for field in (
            "location",
            "observed_on",
            "weather_code",
            "hourly_json",
            "forecast_json",
        ):
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


@pytest.mark.django_db
class TestWeatherAdminSeriesRendering:
    """The two JSON columns render readably, and are never editable."""

    def test_the_series_are_read_only_on_todays_row_too(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """A live row is editable, but its provider series still are not.

        Everything else on today's row is a plausible manual correction; a
        24-hour series typed into a single-line textarea is not.
        """
        weather = WeatherFactory.create(observed_on=timezone.localdate())

        readonly = admin_instance.get_readonly_fields(None, weather)  # type: ignore[arg-type]

        assert "hourly_json" in readonly
        assert "forecast_json" in readonly

    def test_hourly_renders_as_indented_json(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """The block is indented, so a reader can see one hour per stanza."""
        weather = WeatherFactory.create(
            observed_on=timezone.localdate(),
            hourly=[{"time": "2026-08-31T00:00", "temperature_2m": 8.2}],
        )

        rendered = admin_instance.hourly_json(weather)

        assert "<pre" in rendered
        # Quotes come back escaped — the point of the assertion is the
        # indentation, which is what a Textarea does not give you.
        assert "\n  {\n    &quot;time&quot;" in rendered

    def test_a_null_series_renders_as_a_dash(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """A backfilled row carries no forecast, and that must read as absence.

        Not ``None``, not ``[]`` — those look like a bug rather than the
        deliberate null the backfill writes.
        """
        weather = WeatherFactory.create(
            observed_on=timezone.localdate(), hourly=None, forecast=None
        )

        assert admin_instance.hourly_json(weather) == "—"
        assert admin_instance.forecast_json(weather) == "—"

    def test_provider_strings_cannot_inject_markup(
        self, admin_instance: WeatherAdmin
    ) -> None:
        """The payload is upstream data, so it is escaped rather than trusted."""
        weather = WeatherFactory.create(
            observed_on=timezone.localdate(),
            hourly=[{"time": "<script>alert(1)</script>"}],
        )

        rendered = admin_instance.hourly_json(weather)

        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
