"""
tests/locations/management/commands/test_link_resort_locations.py

Covers ``link_resort_locations``, the SNOW-1144 backfill of ``Resort.location``.

Every resort here is built with ``location=None`` and a coordinate: the
shape of a resort pinned before the column existed, which is the only shape
the backfill has to deal with — ``set_resort_pin`` keeps every later pin in
step on its own.

``TestAPinnedResortGetsWeather`` is the one that matters: it drives the page
and asserts weather is on it, rather than asserting rows exist.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from apps.locations.models import Location
from apps.regions.models import Resort
from apps.regions.services.resort_pin import set_resort_pin
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

COMMAND = "link_resort_locations"


def _legacy_resort(**kwargs: object) -> Resort:
    """Return a resort with a coordinate and no pin Location, as before SNOW-1144."""
    kwargs.setdefault("latitude", 46.1)
    kwargs.setdefault("longitude", 7.4)
    return ResortFactory.create(location=None, **kwargs)


@pytest.mark.django_db
class TestLinkResortLocations:
    """--commit gives every pinned resort its Location."""

    def test_a_resort_gets_a_location_at_its_own_pin(self) -> None:
        """The coordinate is the resort's, and the height comes from the sheet."""
        resort = _legacy_resort(base_elevation_m=1450)

        call_command(COMMAND, "--commit", stdout=StringIO())

        resort.refresh_from_db()
        assert resort.location is not None
        assert (resort.location.latitude, resort.location.longitude) == (46.1, 7.4)
        assert resort.location.elevation_m == 1450
        assert resort.location.name == ""

    def test_a_resort_without_coordinates_is_not_a_candidate(self) -> None:
        """No pin, nothing to anchor to."""
        ResortFactory.create()

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert not Location.objects.exists()

    def test_the_legacy_primary_link_location_is_adopted(self) -> None:
        """The row the old command minted becomes the pin, weather and all."""
        resort = _legacy_resort()
        minted = LocationFactory.create(anonymous=True, latitude=46.1, longitude=7.4)
        ResortLocationFactory.create(
            resort=resort, location=minted, role="", is_primary=True
        )
        WeatherFactory.create(location=minted)

        call_command(COMMAND, "--commit", stdout=StringIO())

        resort.refresh_from_db()
        assert resort.location_id == minted.pk
        assert Location.objects.count() == 1

    def test_it_reuses_an_anonymous_location_at_the_same_point(self) -> None:
        """Never mint a second row at a coordinate one already occupies."""
        resort = _legacy_resort()
        existing = LocationFactory.create(anonymous=True, latitude=46.1, longitude=7.4)

        call_command(COMMAND, "--commit", stdout=StringIO())

        resort.refresh_from_db()
        assert resort.location_id == existing.pk
        assert Location.objects.count() == 1

    def test_a_second_run_changes_nothing(self) -> None:
        """Idempotent — a rerun reports every resort unchanged and writes nothing."""
        resort = _legacy_resort()
        call_command(COMMAND, "--commit", stdout=StringIO())
        resort.refresh_from_db()
        before = (Location.objects.count(), resort.location_id, resort.updated_at)

        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        resort.refresh_from_db()
        assert (Location.objects.count(), resort.location_id, resort.updated_at) == (
            before
        )
        assert "0 resort(s) linked, 1 unchanged" in out.getvalue()

    def test_dry_run_writes_nothing(self) -> None:
        """Read-only by default, per the command design rules."""
        resort = _legacy_resort()

        out = StringIO()
        call_command(COMMAND, stdout=out)

        resort.refresh_from_db()
        assert resort.location is None
        assert not Location.objects.exists()
        assert "1 resort(s) would be linked" in out.getvalue()

    def test_countdown_names_each_resort(self) -> None:
        """Each processed row prints its id and the resort's name."""
        resort = _legacy_resort(name="Ovronnaz")

        out = StringIO()
        call_command(COMMAND, stdout=out)

        assert f"{resort.pk} Ovronnaz" in out.getvalue()

    def test_a_failed_resort_exits_non_zero_and_others_still_link(self) -> None:
        """One unwritable resort is counted, and the run fails loudly."""
        bad = _legacy_resort(name="Bad")
        good = _legacy_resort(name="Good", latitude=46.2)

        def flaky(resort: Resort, latitude: float, longitude: float) -> object:
            """Fail for the bad resort only."""
            if resort.pk == bad.pk:
                raise RuntimeError("boom")
            return set_resort_pin(resort, latitude, longitude)

        with (
            mock.patch(
                "apps.locations.management.commands.link_resort_locations."
                "set_resort_pin",
                side_effect=flaky,
            ),
            pytest.raises(CommandError, match="1 of 2"),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

        good.refresh_from_db()
        bad.refresh_from_db()
        assert good.location is not None
        assert bad.location is None


@pytest.mark.django_db
class TestAPinnedResortGetsWeather:
    """The requirement, asserted through the page rather than the rows."""

    def test_the_resort_page_links_to_the_pins_weather(self, client: Client) -> None:
        """After the backfill the resort page carries its pin's forecast link."""
        resort = _legacy_resort(name="Verbier", base_elevation_m=1494)
        call_command(COMMAND, "--commit", stdout=StringIO())
        resort.refresh_from_db()
        assert resort.location is not None
        WeatherFactory.create(
            location=resort.location, observed_on=timezone.localdate()
        )

        response = client.get(resort.get_absolute_url())
        body = response.content.decode()

        assert response.status_code == 200
        assert 'data-testid="resort-locations"' in body
        assert f'href="{resort.location.get_absolute_url()}"' in body

    def test_the_link_is_labelled_with_the_resort_name(self, client: Client) -> None:
        """An anonymous location must not put raw coordinates on the page."""
        resort = _legacy_resort(name="Verbier", base_elevation_m=1494)
        call_command(COMMAND, "--commit", stdout=StringIO())

        body = client.get(resort.get_absolute_url()).content.decode()

        link_markup = body.split('data-testid="resort-location-0-link"')[1][:200]
        assert ">Verbier</a>" in link_markup
        assert "46.1" not in link_markup


@pytest.mark.django_db
class TestCheckMode:
    """``--check`` is the SNOW-885 detector: report, write nothing, fail loud."""

    def test_passes_when_every_pinned_resort_has_a_location(self) -> None:
        """The state after the backfill, and the one the scheduled run expects."""
        ResortFactory.create(latitude=46.1, longitude=7.2)
        out = StringIO()
        call_command(COMMAND, "--check", stdout=out)
        assert "No unlinked geocoded resorts." in out.getvalue()

    def test_fails_and_names_an_unlinked_resort(self) -> None:
        """The defect: a coordinate, no Location, no weather."""
        _legacy_resort(name="Ovronnaz")
        out = StringIO()
        with pytest.raises(CommandError, match="1 geocoded resort"):
            call_command(COMMAND, "--check", stdout=out)
        assert "unlinked: Ovronnaz" in out.getvalue()

    def test_ignores_a_resort_with_no_coordinates(self) -> None:
        """A resort with no pin cannot be linked, so it is backlog, not a gap."""
        ResortFactory.create(name="Nowhere")
        out = StringIO()
        call_command(COMMAND, "--check", stdout=out)
        assert "No unlinked geocoded resorts." in out.getvalue()

    def test_check_writes_nothing(self) -> None:
        """It must never write the pin it is reporting the absence of."""
        resort = _legacy_resort()
        with pytest.raises(CommandError):
            call_command(COMMAND, "--check", stdout=StringIO())
        resort.refresh_from_db()
        assert resort.location is None
        assert not Location.objects.exists()
