"""
tests/regions/services/test_resort_pin.py — Tests for set_resort_pin (SNOW-1144).

Covers:
  placing   — a new pin creates and links an anonymous Location, mirrors the
              coordinate, and reuses an anonymous row already at the point.
  moving    — the same Location row is corrected in place and keeps its
              Weather rows; a shared or named row is never moved.
  reusing   — a primary ResortLocation's Location is adopted, never duplicated.
  clearing  — the FK and mirror clear; the Location survives.
  guards    — an unsaved resort or a half pair is refused; an unchanged pin
              writes nothing.
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.locations.models import Location
from apps.regions.models import Resort
from apps.regions.services.resort_pin import set_resort_pin
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

pytestmark = pytest.mark.django_db


class TestPlacingAPin:
    """A resort with no pin gets a Location at the new coordinate."""

    def test_creates_and_links_an_anonymous_location(self) -> None:
        """The pin is a new anonymous Location carrying the resort's base height."""
        resort = ResortFactory.create(base_elevation_m=1500)

        location = set_resort_pin(resort, 46.2, 7.3)

        resort.refresh_from_db()
        assert location is not None
        assert resort.location == location
        assert (location.latitude, location.longitude) == (46.2, 7.3)
        assert location.name == ""
        assert location.elevation_m == 1500.0

    def test_mirrors_the_coordinate_onto_the_resort(self) -> None:
        """latitude/longitude on the resort follow the pin."""
        resort = ResortFactory.create()

        set_resort_pin(resort, 46.2, 7.3)

        resort.refresh_from_db()
        assert (resort.latitude, resort.longitude) == (46.2, 7.3)

    def test_reuses_an_anonymous_location_already_at_the_point(self) -> None:
        """anchor_location semantics: an anonymous row at the point is reused."""
        existing = LocationFactory.create(anonymous=True, latitude=46.2, longitude=7.3)
        resort = ResortFactory.create()

        assert set_resort_pin(resort, 46.2, 7.3) == existing
        assert Location.objects.count() == 1

    def test_never_adopts_a_favourites_location_at_the_point(self) -> None:
        """A user's resort favourite mints a row at the pin; it stays theirs."""
        favourite = FavouriteFactory.create(latitude=46.2, longitude=7.3)
        favourite_location = favourite.location
        assert favourite_location is not None
        favourite_location.latitude, favourite_location.longitude = 46.2, 7.3
        favourite_location.elevation_m = 1234.0
        favourite_location.save()
        resort = ResortFactory.create(base_elevation_m=1500)

        location = set_resort_pin(resort, 46.2, 7.3)

        assert location is not None and location.pk != favourite_location.pk
        assert Location.objects.get(pk=favourite_location.pk).elevation_m == 1234.0

    def test_the_pin_is_in_the_fetched_estate(self) -> None:
        """A pinned resort's Location is public and active, so weather is fetched."""
        resort = ResortFactory.create()

        location = set_resort_pin(resort, 46.2, 7.3)

        assert location in Location.objects.public()
        assert location in Location.objects.active()


class TestMovingAPin:
    """A moved pin corrects the same row, so its weather stays attached."""

    def test_updates_the_same_location_row(self) -> None:
        """The Location id is unchanged; its coordinate is the new one."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        original = resort.location
        assert original is not None

        moved = set_resort_pin(resort, 46.3, 7.5)

        assert moved is not None
        assert moved.pk == original.pk
        original.refresh_from_db()
        assert (original.latitude, original.longitude) == (46.3, 7.5)
        assert Location.objects.count() == 1

    def test_keeps_its_weather_rows(self) -> None:
        """Weather on the moved Location stays attached to it."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        assert resort.location is not None
        past = timezone.localdate() - datetime.timedelta(days=3)
        WeatherFactory.create(location=resort.location, observed_on=past)
        WeatherFactory.create(location=resort.location)

        moved = set_resort_pin(resort, 46.3, 7.5)

        assert Weather.objects.filter(location=moved).count() == 2

    def test_clears_coordinate_derived_columns(self) -> None:
        """what3words dies with the old coordinate; the height is re-derived."""
        resort = ResortFactory.create(
            latitude=46.1, longitude=7.4, base_elevation_m=None
        )
        location = resort.location
        assert location is not None
        location.what3words = "one.two.three"
        location.what3words_fetched_at = timezone.now()
        location.elevation_m = 2100.0
        location.save()

        set_resort_pin(resort, 46.3, 7.5)

        fresh = Location.objects.get(pk=location.pk)
        assert fresh.what3words is None
        assert fresh.what3words_fetched_at is None
        assert fresh.elevation_m is None

    def test_never_moves_a_named_location(self) -> None:
        """A curated place is re-anchored away from, not dragged along."""
        village = LocationFactory.create(
            name="Verbier village", latitude=46.1, longitude=7.2
        )
        resort = ResortFactory.create(location=village, latitude=46.1, longitude=7.2)

        moved = set_resort_pin(resort, 46.3, 7.5)

        village.refresh_from_db()
        assert (village.latitude, village.longitude) == (46.1, 7.2)
        assert moved is not None and moved.pk != village.pk
        assert (moved.latitude, moved.longitude) == (46.3, 7.5)

    def test_never_moves_a_location_another_resort_pins(self) -> None:
        """A row two resorts share stays put for the other one."""
        shared = LocationFactory.create(anonymous=True, latitude=46.1, longitude=7.2)
        resort = ResortFactory.create(location=shared, latitude=46.1, longitude=7.2)
        ResortFactory.create(location=shared, latitude=46.1, longitude=7.2)

        moved = set_resort_pin(resort, 46.3, 7.5)

        shared.refresh_from_db()
        assert (shared.latitude, shared.longitude) == (46.1, 7.2)
        assert moved is not None and moved.pk != shared.pk

    def test_never_moves_a_location_a_favourite_reaches(self) -> None:
        """A user's favourite on the same row is not relocated."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        shared = resort.location
        assert shared is not None
        FavouriteFactory.create(location=shared, latitude=46.1, longitude=7.4)

        moved = set_resort_pin(resort, 46.3, 7.5)

        shared.refresh_from_db()
        assert (shared.latitude, shared.longitude) == (46.1, 7.4)
        assert moved is not None and moved.pk != shared.pk


class TestReusingAPrimaryLink:
    """The pre-SNOW-1144 primary link's Location is adopted, not duplicated."""

    def test_adopts_the_primary_link_location_at_the_same_point(self) -> None:
        """The backfill case: the minted row becomes the pin."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(
            region=region, latitude=46.1, longitude=7.4, location=None
        )
        minted = LocationFactory.create(anonymous=True, latitude=46.1, longitude=7.4)
        ResortLocationFactory.create(
            resort=resort, location=minted, role="", is_primary=True
        )

        assert set_resort_pin(resort, 46.1, 7.4) == minted
        assert Location.objects.count() == 1

    def test_never_adopts_a_named_primary_link_location_at_the_pin(self) -> None:
        """A curated village at exactly the pin gets a separate anonymous pin.

        Adopting it would PROTECT a curated row from import_locations and
        make the village editor move the pin.
        """
        resort = ResortFactory.create(latitude=46.1, longitude=7.4, location=None)
        village = LocationFactory.create(
            name="Verbier village", kind="VILLAGE", latitude=46.1, longitude=7.4
        )
        ResortLocationFactory.create(
            resort=resort, location=village, role="BASE", is_primary=True
        )
        before = Location.objects.values().get(pk=village.pk)

        pin = set_resort_pin(resort, 46.1, 7.4)

        assert pin is not None and pin.pk != village.pk
        assert pin.name == ""
        assert (pin.latitude, pin.longitude) == (46.1, 7.4)
        assert Location.objects.values().get(pk=village.pk) == before
        assert not village.pinned_resorts.exists()

    def test_a_named_location_already_on_the_fk_is_replaced(self) -> None:
        """Even an unchanged pin moves off a named row onto an anonymous one."""
        village = LocationFactory.create(name="Village", latitude=46.1, longitude=7.4)
        resort = ResortFactory.create(location=village, latitude=46.1, longitude=7.4)

        pin = set_resort_pin(resort, 46.1, 7.4)

        assert pin is not None and pin.pk != village.pk and pin.name == ""

    def test_moves_the_primary_link_location_when_it_is_the_resorts_own(
        self,
    ) -> None:
        """A primary link's anonymous row is moved in place, keeping its weather."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4, location=None)
        minted = LocationFactory.create(anonymous=True, latitude=46.1, longitude=7.4)
        ResortLocationFactory.create(
            resort=resort, location=minted, role="", is_primary=True
        )
        WeatherFactory.create(location=minted)

        assert set_resort_pin(resort, 46.3, 7.5) == minted
        assert Weather.objects.filter(location=minted).count() == 1


class TestClearingAPin:
    """None clears the FK and the mirror, never the Location."""

    def test_clears_the_fk_and_the_mirror(self) -> None:
        """The resort has no pin afterwards and drops out of geocoded()."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        location = resort.location

        assert set_resort_pin(resort, None, None) is None

        resort.refresh_from_db()
        assert resort.location is None
        assert (resort.latitude, resort.longitude) == (None, None)
        assert not Resort.objects.geocoded().filter(pk=resort.pk).exists()
        assert Location.objects.filter(pk=location.pk).exists()  # type: ignore[union-attr]


class TestGuards:
    """Refusals and the no-op path."""

    def test_refuses_an_unsaved_resort(self) -> None:
        """The service persists, so it needs a row to persist to."""
        with pytest.raises(ValueError, match="saved resort"):
            set_resort_pin(Resort(name="X"), 46.1, 7.4)

    def test_refuses_half_a_pair(self) -> None:
        """A latitude without a longitude is not a place."""
        resort = ResortFactory.create()
        with pytest.raises(ValueError, match="both"):
            set_resort_pin(resort, 46.1, None)

    def test_an_unchanged_pin_writes_nothing(self) -> None:
        """Re-setting the same pin leaves updated_at alone."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        before = Resort.objects.get(pk=resort.pk).updated_at

        set_resort_pin(resort, 46.1, 7.4)

        assert Resort.objects.get(pk=resort.pk).updated_at == before
