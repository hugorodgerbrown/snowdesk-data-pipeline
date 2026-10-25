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
from django.forms.models import model_to_dict
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


# SNOW-1226: every admin path that places or links a location enqueues its
# height-then-forecast fill. The admin module's name is patched, so these
# assert the call rather than the upstream round trips (test_prime.py has
# those).
_PRIME = "apps.locations.admin.enqueue_location_prime"


def _inline_management(prefix: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return an inline formset's POST data for ``rows``, all of them new."""
    data: dict[str, Any] = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return data


@pytest.mark.django_db
class TestPrimeOnAdminSave:
    """The admin enqueues the fill on every path that places or links."""

    def _change_location(self, client: Client, location: Any, **fields: Any) -> Any:
        """POST the location change form with ``fields`` over its values."""
        data: dict[str, Any] = {
            "name": location.name,
            "kind": location.kind,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "elevation_m": location.elevation_m or "",
            **fields,
            **_inline_management("resort_locations", []),
        }
        return client.post(
            reverse("admin:locations_location_change", args=[location.pk]), data
        )

    def test_a_moved_location_loses_its_height_and_is_refetched(
        self, staff_client: Client
    ) -> None:
        """A height from the old coordinate would make a bad pin look right."""
        location = LocationFactory.create(resolved=True)
        with mock.patch(_PRIME) as prime:
            response = self._change_location(staff_client, location, latitude=46.3)

        assert response.status_code == 302
        location.refresh_from_db()
        assert location.elevation_m is None
        prime.assert_called_once()
        assert prime.call_args.kwargs == {"moved": True}

    def test_a_rename_keeps_the_height(self, staff_client: Client) -> None:
        """Only a move invalidates the height."""
        location = LocationFactory.create(resolved=True)
        with mock.patch(_PRIME) as prime:
            self._change_location(staff_client, location, name="Schilthorn")

        location.refresh_from_db()
        assert location.elevation_m == 1500.0
        assert prime.call_args.kwargs == {"moved": False}

    def test_a_height_set_by_hand_with_the_move_is_kept(
        self, staff_client: Client
    ) -> None:
        """The curator's own figure is an override, not a stale value."""
        location = LocationFactory.create(resolved=True)
        with mock.patch(_PRIME):
            self._change_location(
                staff_client, location, latitude=46.3, elevation_m=2970
            )

        location.refresh_from_db()
        assert location.elevation_m == 2970.0

    def test_a_link_added_inline_on_the_resort_enqueues_its_location(
        self, staff_client: Client
    ) -> None:
        """The Mürren case: a summit linked from the resort's own page."""
        resort = ResortFactory.create()
        location = LocationFactory.create()
        data: dict[str, Any] = {
            key: "" if value is None else value
            for key, value in model_to_dict(resort).items()
            if key != "passes"
        }
        data.update(
            **_inline_management(
                "resort_locations",
                [{"location": location.pk, "role": "TOP"}],
            )
        )
        with mock.patch(_PRIME) as prime:
            response = staff_client.post(
                reverse("admin:regions_resort_change", args=[resort.pk]), data
            )

        assert response.status_code == 302, response.content.decode()[:2000]
        prime.assert_called_once_with(location)

    def test_a_link_added_on_its_own_page_enqueues_its_location(
        self, staff_client: Client
    ) -> None:
        """The standalone ResortLocation form follows the same rule."""
        resort = ResortFactory.create()
        location = LocationFactory.create()
        with mock.patch(_PRIME) as prime:
            response = staff_client.post(
                reverse("admin:locations_resortlocation_add"),
                {"resort": resort.pk, "location": location.pk, "role": "MID"},
            )

        assert response.status_code == 302
        prime.assert_called_once_with(location)

    def test_a_link_repointed_inline_enqueues_its_new_location(
        self, staff_client: Client
    ) -> None:
        """An edited link only counts when it now reaches somewhere else."""
        link = ResortLocationFactory.create()
        moved_to = LocationFactory.create()
        resort = link.resort
        data: dict[str, Any] = {
            key: "" if value is None else value
            for key, value in model_to_dict(resort).items()
            if key != "passes"
        }
        data.update(
            {
                "resort_locations-TOTAL_FORMS": "1",
                "resort_locations-INITIAL_FORMS": "1",
                "resort_locations-MIN_NUM_FORMS": "0",
                "resort_locations-MAX_NUM_FORMS": "1000",
                "resort_locations-0-id": link.pk,
                "resort_locations-0-resort": resort.pk,
                "resort_locations-0-location": moved_to.pk,
                "resort_locations-0-role": link.role,
            }
        )
        with mock.patch(_PRIME) as prime:
            response = staff_client.post(
                reverse("admin:regions_resort_change", args=[resort.pk]), data
            )

        assert response.status_code == 302
        prime.assert_called_once_with(moved_to)
