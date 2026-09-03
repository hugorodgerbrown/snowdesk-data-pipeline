"""
tests/locations/management/commands/test_link_resort_locations.py

Covers ``link_resort_locations``.

The gap it closes: the edit-resorts map overlay writes
``Resort.latitude``/``longitude`` and never touches ``Location``, while the
resort page's weather section reads ``ResortLocation`` links that only the
separate edit-locations overlay creates. So a resort could carry a
hand-placed pin and still show no weather — production had 115 geocoded
resorts and 4 links.

``TestAGeocodedResortGetsWeather`` is the one that matters: it drives the
page and asserts weather is on it, rather than asserting rows exist.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.locations.models import Location, ResortLocation
from tests.factories import (
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

COMMAND = "link_resort_locations"


@pytest.mark.django_db
class TestLinkResortLocations:
    """--commit links every geocoded resort that has none."""

    def test_a_geocoded_resort_is_linked_at_its_own_pin(self) -> None:
        """The coordinate is the resort's, and the height comes from the sheet."""
        resort = ResortFactory.create(
            geocoded=True, base_elevation_m=1450, top_elevation_m=3000
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        link = ResortLocation.objects.get(resort=resort)
        assert link.is_primary is True
        assert link.location.latitude == resort.latitude
        assert link.location.longitude == resort.longitude
        assert link.location.elevation_m == 1450

    def test_the_minted_location_is_anonymous(self) -> None:
        """It is not curated data — naming it is the editor's job."""
        ResortFactory.create(geocoded=True)

        call_command(COMMAND, "--commit", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""

    def test_an_ungeocoded_resort_is_not_a_candidate(self) -> None:
        """No pin, no coordinate, nothing to anchor to."""
        ResortFactory.create()

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert not ResortLocation.objects.exists()
        assert not Location.objects.exists()

    def test_a_resort_that_already_has_a_link_is_left_alone(self) -> None:
        """Curated links win — this never adds a second, competing point."""
        existing = ResortLocationFactory.create(
            resort=ResortFactory.create(geocoded=True)
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert ResortLocation.objects.count() == 1
        assert ResortLocation.objects.get().pk == existing.pk

    def test_a_second_run_changes_nothing(self) -> None:
        """Idempotent — it runs on every deploy of every service."""
        ResortFactory.create(geocoded=True)

        call_command(COMMAND, "--commit", stdout=StringIO())
        first = (Location.objects.count(), ResortLocation.objects.count())
        call_command(COMMAND, "--commit", stdout=StringIO())

        assert (Location.objects.count(), ResortLocation.objects.count()) == first

    def test_it_reuses_an_anonymous_location_at_the_same_point(self) -> None:
        """Never mint a second row at a coordinate one already occupies.

        The same rule as the region centroids (SNOW-771): a fresh row each
        deploy would orphan the previous one and its Weather.
        """
        resort = ResortFactory.create(geocoded=True, base_elevation_m=1450)
        existing = LocationFactory.create(
            anonymous=True,
            latitude=resort.latitude,
            longitude=resort.longitude,
            elevation_m=1450,
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Location.objects.count() == 1
        assert ResortLocation.objects.get().location_id == existing.pk

    def test_dry_run_writes_nothing(self) -> None:
        """Read-only by default, per the command design rules."""
        ResortFactory.create(geocoded=True)

        out = StringIO()
        call_command(COMMAND, stdout=out)

        assert not ResortLocation.objects.exists()
        assert not Location.objects.exists()
        assert "would be linked" in out.getvalue()


@pytest.mark.django_db
class TestAGeocodedResortGetsWeather:
    """The requirement, asserted through the page rather than the rows."""

    def test_the_resort_page_links_to_the_weather_page_after_linking(
        self, client: Client
    ) -> None:
        """A resort with a pin renders a Forecasts link to its location's weather.

        This is the whole point: before this command a geocoded resort had
        a map pin and a resort page with no forecast at all, because
        nothing had ever created the ResortLocation the page's Forecasts
        list reads (SNOW-807 made that list the resort page's route to the
        weather document).
        """
        resort = ResortFactory.create(
            geocoded=True, name="Verbier", base_elevation_m=1494
        )
        call_command(COMMAND, "--commit", stdout=StringIO())
        link = ResortLocation.objects.get(resort=resort)
        WeatherFactory.create(location=link.location, observed_on=timezone.localdate())

        response = client.get(resort.get_absolute_url())
        body = response.content.decode()

        assert response.status_code == 200
        assert 'data-testid="resort-locations"' in body
        assert f'href="{link.location.get_absolute_url()}"' in body

    def test_the_link_is_labelled_with_the_resort_name(self, client: Client) -> None:
        """An anonymous location must not put raw coordinates on the page.

        ``Location.to_string()`` renders "46.09610,7.22860" for an unnamed
        row, which is what the label used to fall back to.
        """
        resort = ResortFactory.create(
            geocoded=True, name="Verbier", base_elevation_m=1494
        )
        call_command(COMMAND, "--commit", stdout=StringIO())
        link = ResortLocation.objects.get(resort=resort)
        WeatherFactory.create(location=link.location, observed_on=timezone.localdate())

        body = client.get(resort.get_absolute_url()).content.decode()

        link_markup = body.split('data-testid="resort-location-0-link"')[1][:200]
        assert ">Verbier</a>" in link_markup
        assert str(resort.latitude) not in link_markup
