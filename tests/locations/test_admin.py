"""
tests/locations/test_admin.py — Tests for the locations admin.

Admin is the only surface Location has until SNOW-702, and the
``ResortLocation`` inline on the resort change form is the surface SNOW-701's
whole data-curation effort runs through — so it is worth a test that it
actually renders, not just that it is registered.

SNOW-731 added the estate's first admin action to this surface, plus the
coverage column and filter that make the gaps it fills visible. Those get
tests of their own here, because a "Backfill missing weather" action that
silently processed only part of a selection, or a coverage column counting
the wrong window, would both read as working.

Covers:
  - Location and ResortLocation changelists render.
  - The inline renders on the resort change form, and on the location one.
  - A location's changelist row shows its resolved-out-of-band columns.
  - The weather coverage column, the gaps filter, and the backfill action —
    including that the per-run cap says what it skipped.
"""

import datetime
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from apps.weather.models import Weather
from apps.weather.services import backfill
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
)


def _payload(days: list[datetime.date]) -> dict[str, Any]:
    """Return a minimal complete historical payload covering ``days``."""
    count = len(days)
    return {
        "daily": {
            "time": [d.isoformat() for d in days],
            "weather_code": [3] * count,
            "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
            "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
            "temperature_2m_max": [4.5] * count,
            "temperature_2m_min": [-2.0] * count,
        }
    }


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _one_day_window(settings: Any) -> datetime.date:
    """Narrow the backfill window to yesterday alone, and return that day.

    Every coverage assertion below is easier to read against an expected
    total of 1 than against a whole season, and the arithmetic under test is
    the same either way.
    """
    yesterday = backfill.backfill_until()
    settings.WEATHER_BACKFILL_FLOOR = yesterday
    return yesterday


@pytest.fixture()
def staff_client() -> Client:
    """Return a Django test client logged in as a superuser."""
    client = Client()
    client.force_login(UserFactory.create(is_superuser=True))
    return client


@pytest.mark.django_db
class TestLocationAdmin:
    """LocationAdmin — the curation surface for the estate."""

    def test_changelist_renders(self, staff_client: Client) -> None:
        """The Location changelist renders with a curated row on it."""
        LocationFactory.create(name="Mont Fort")
        response = staff_client.get(reverse("admin:locations_location_changelist"))
        assert response.status_code == 200
        assert "Mont Fort" in response.content.decode()

    def test_change_form_renders_the_resort_inline(self, staff_client: Client) -> None:
        """The location change form shows which resorts reference it.

        The sharing is the point of the model, so a curator must be able to
        see that four resorts point at Mont Fort before trying to delete it.
        """
        link = ResortLocationFactory.create(
            location=LocationFactory.create(name="Mont Fort")
        )
        response = staff_client.get(
            reverse("admin:locations_location_change", args=[link.location.pk])
        )
        assert response.status_code == 200
        assert "resort_locations" in response.content.decode()

    def test_changelist_shows_weather_coverage(
        self, staff_client: Client, settings: Any
    ) -> None:
        """The coverage column says how complete a location's history is."""
        yesterday = _one_day_window(settings)
        location = LocationFactory.create(name="Mont Fort")
        WeatherFactory.create(location=location, observed_on=yesterday)

        response = staff_client.get(reverse("admin:locations_location_changelist"))
        assert "1 / 1" in response.content.decode()

    def test_the_coverage_count_ignores_rows_outside_the_window(
        self, staff_client: Client, settings: Any
    ) -> None:
        """Today's row is not coverage — the column counts the backfill window.

        Counting the whole relation would let a location the scheduled fetch
        touched once read as complete.
        """
        yesterday = _one_day_window(settings)
        location = LocationFactory.create(name="Mont Fort")
        WeatherFactory.create(
            location=location, observed_on=yesterday + datetime.timedelta(days=1)
        )

        response = staff_client.get(reverse("admin:locations_location_changelist"))
        assert "0 / 1" in response.content.decode()

    def test_the_gaps_filter_selects_the_incomplete_rows(
        self, staff_client: Client, settings: Any
    ) -> None:
        """The has-gaps option narrows the changelist to what the action would fill.

        The two locations are named for their state rather than "Complete" /
        "Incomplete": the filter's own sidebar links carry those words, so
        the assertion would pass on the chrome rather than the rows.
        """
        yesterday = _one_day_window(settings)
        filled = LocationFactory.create(name="Zermatt")
        WeatherFactory.create(location=filled, observed_on=yesterday)
        LocationFactory.create(name="Corvatsch")

        response = staff_client.get(
            reverse("admin:locations_location_changelist"), {"weather_gaps": "gaps"}
        )
        body = response.content.decode()
        assert "Corvatsch" in body
        assert "Zermatt" not in body

    def test_the_complete_filter_is_the_complement(
        self, staff_client: Client, settings: Any
    ) -> None:
        """The complete option selects exactly the rows has-gaps does not."""
        yesterday = _one_day_window(settings)
        filled = LocationFactory.create(name="Zermatt")
        WeatherFactory.create(location=filled, observed_on=yesterday)
        LocationFactory.create(name="Corvatsch")

        response = staff_client.get(
            reverse("admin:locations_location_changelist"), {"weather_gaps": "complete"}
        )
        body = response.content.decode()
        assert "Zermatt" in body
        assert "Corvatsch" not in body

    def test_changelist_is_searchable_by_name(self, staff_client: Client) -> None:
        """Search by name filters the changelist.

        Also what makes ``autocomplete_fields = ["location"]`` work on the
        resort inline — an autocomplete against a model with no
        search_fields raises at check time.
        """
        LocationFactory.create(name="Mont Fort")
        LocationFactory.create(name="Corvatsch")
        response = staff_client.get(
            reverse("admin:locations_location_changelist"), {"q": "Mont"}
        )
        body = response.content.decode()
        assert "Mont Fort" in body
        assert "Corvatsch" not in body


@pytest.mark.django_db
class TestBackfillMissingWeatherAction:
    """The estate's first admin action — it fills gaps, and says what it skipped."""

    def _run(
        self, client: Client, locations: list[Any], response: mock.Mock | None = None
    ) -> Any:
        """POST the action against ``locations`` and return the response."""
        return client.post(
            reverse("admin:locations_location_changelist"),
            {
                "action": "backfill_missing_weather",
                "_selected_action": [str(location.pk) for location in locations],
            },
            follow=True,
        )

    def test_the_action_fills_a_gap(self, staff_client: Client, settings: Any) -> None:
        """Selecting an incomplete location and running the action writes its day."""
        yesterday = _one_day_window(settings)
        location = LocationFactory.create(name="Mont Fort")

        with mock.patch("requests.get", return_value=_response(_payload([yesterday]))):
            response = self._run(staff_client, [location])

        assert response.status_code == 200
        assert Weather.objects.filter(location=location, observed_on=yesterday).exists()

    def test_a_backfilled_row_carries_no_forecast(
        self, staff_client: Client, settings: Any
    ) -> None:
        """The action writes the same shape the service does — no invented outlook."""
        yesterday = _one_day_window(settings)
        location = LocationFactory.create(name="Mont Fort")

        with mock.patch("requests.get", return_value=_response(_payload([yesterday]))):
            self._run(staff_client, [location])

        assert Weather.objects.get(location=location).forecast is None

    def test_the_cap_truncates_the_run_and_says_so(
        self, staff_client: Client, settings: Any
    ) -> None:
        """Over the cap, the extra locations are left — and reported, not dropped silently.

        The action runs inline in the request; a selection large enough to
        time it out must come back as a message telling the operator to run
        the rest, not as a half-finished job that looks finished.
        """
        yesterday = _one_day_window(settings)
        over = backfill.ADMIN_MAX_LOCATIONS + 2
        locations = [LocationFactory.create(name=f"L{i:02d}") for i in range(over)]

        with (
            mock.patch("requests.get", return_value=_response(_payload([yesterday]))),
            # The throttle is real behaviour and tested in the service suite;
            # paying it here would add four seconds to this assertion.
            mock.patch.object(backfill, "INTER_LOCATION_DELAY", 0),
        ):
            response = self._run(staff_client, locations)

        assert Weather.objects.count() == backfill.ADMIN_MAX_LOCATIONS
        assert "2 location(s) not processed" in response.content.decode()

    def test_a_failure_is_reported_rather_than_raised(
        self, staff_client: Client, settings: Any
    ) -> None:
        """One location Open-Meteo dislikes must not 500 the changelist."""
        import requests

        _one_day_window(settings)
        location = LocationFactory.create(name="Mont Fort")

        with mock.patch("requests.get", side_effect=requests.HTTPError("boom")):
            response = self._run(staff_client, [location])

        assert response.status_code == 200
        assert "1 location(s) failed" in response.content.decode()


@pytest.mark.django_db
class TestResortLocationAdmin:
    """ResortLocationAdmin, and the inline on the resort change form."""

    def test_changelist_renders(self, staff_client: Client) -> None:
        """The ResortLocation changelist renders."""
        ResortLocationFactory.create()
        response = staff_client.get(
            reverse("admin:locations_resortlocation_changelist")
        )
        assert response.status_code == 200

    def test_inline_renders_on_the_resort_change_form(
        self, staff_client: Client
    ) -> None:
        """A curator opens a resort and can add its locations there.

        SNOW-701 curates the estate through this form. If the inline stops
        rendering, that ticket has no surface.
        """
        resort = ResortFactory.create(name="Verbier")
        response = staff_client.get(
            reverse("admin:regions_resort_change", args=[resort.pk])
        )
        assert response.status_code == 200
        assert "resort_locations" in response.content.decode()
