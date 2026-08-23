"""
tests/locations/test_models.py — Tests for the Location and ResortLocation models.

Covers:
  - Factories produce valid instances via .create().
  - to_string() / __str__() for a named, an anonymous and an unresolved row.
  - Meta.ordering.
  - LocationQuerySet.named() / .anonymous() / .unresolved().
  - The sharing the M2M exists for: one location referenced by four
    resorts, holding a different role in each.
  - PROTECT on ResortLocation.location and CASCADE on .resort.
  - unique_together (resort, location).
"""

import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.locations.models import Location, ResortLocation
from tests.factories import (
    ForecastPointFactory,
    LocationFactory,
    ResortFactory,
    ResortLocationFactory,
)


@pytest.mark.django_db
class TestLocationModel:
    """Location — the domain primitive every place reaches."""

    def test_factory_creates_valid_instance(self) -> None:
        """The factory produces a persisted, curated location."""
        location = LocationFactory.create()
        assert location.pk is not None
        assert location.name
        assert location.kind == Location.KIND.PEAK

    def test_unresolved_by_default(self) -> None:
        """A fresh location has no elevation and no forecast cell.

        Both need an Open-Meteo call, which cannot ride on a save — they
        are filled by link_location_forecast_cells (SNOW-701).
        """
        location = LocationFactory.create()
        assert location.elevation_m is None
        assert location.forecast_cell is None

    def test_anonymous_trait_carries_no_name_or_kind(self) -> None:
        """A location minted from user data carries neither name nor kind.

        Naming is a curation act — SNOW-704 and SNOW-709 mint rows like
        this, and they must not become curated places by accident.
        """
        location = LocationFactory.create(anonymous=True)
        assert location.name == ""
        assert location.kind == ""

    def test_to_string_named_and_resolved(self) -> None:
        """A fully curated location renders name, kind, coordinate, elevation."""
        location = LocationFactory.create(
            name="Mont Fort",
            kind=Location.KIND.PEAK,
            latitude=46.10361,
            longitude=7.29889,
            elevation_m=3328.0,
        )
        assert location.to_string() == "Mont Fort (Peak) 46.10361,7.29889 @3328m"
        assert str(location) == location.to_string()

    def test_to_string_anonymous_is_the_coordinate(self) -> None:
        """An anonymous, unresolved location renders as its coordinate alone.

        The coordinate is the only thing every location has.
        """
        location = LocationFactory.create(
            anonymous=True, latitude=46.0961, longitude=7.2286
        )
        assert location.to_string() == "46.09610,7.22860"

    def test_to_string_omits_elevation_when_unresolved(self) -> None:
        """An unresolved location renders no elevation rather than a zero."""
        location = LocationFactory.create(name="Attelas", kind=Location.KIND.MID)
        assert "@" not in location.to_string()
        assert location.to_string().startswith("Attelas (Mid-mountain)")

    def test_ordering_is_newest_first(self) -> None:
        """Meta.ordering puts the most recently created row first."""
        first = LocationFactory.create()
        second = LocationFactory.create()
        assert list(Location.objects.all())[:2] == [second, first]


@pytest.mark.django_db
class TestLocationQuerySet:
    """LocationQuerySet — named / anonymous / unresolved."""

    def test_named_excludes_anonymous_rows(self) -> None:
        """named() is the curated estate; anonymous() is its complement."""
        curated = LocationFactory.create(name="Mont Fort")
        minted = LocationFactory.create(anonymous=True)

        assert curated in Location.objects.named()
        assert minted not in Location.objects.named()
        assert minted in Location.objects.anonymous()
        assert curated not in Location.objects.anonymous()

    def test_named_and_anonymous_partition_the_table(self) -> None:
        """Every row is in exactly one of named() and anonymous()."""
        LocationFactory.create(name="Mont Fort")
        LocationFactory.create(anonymous=True)

        named = set(Location.objects.named())
        anonymous = set(Location.objects.anonymous())

        assert named | anonymous == set(Location.objects.all())
        assert named & anonymous == set()

    def test_unresolved_selects_rows_missing_either_half(self) -> None:
        """unresolved() catches a null elevation OR a null cell, not only both.

        The two are filled together today, but a row that has one and not
        the other is still work for link_location_forecast_cells — treating
        it as done would leave it permanently unfetched.
        """
        neither = LocationFactory.create()
        elevation_only = LocationFactory.create(elevation_m=1500.0)
        cell_only = LocationFactory.create(
            forecast_cell=ForecastPointFactory.create(latitude=47.9, longitude=8.9)
        )
        done = LocationFactory.create(resolved=True)

        unresolved = set(Location.objects.unresolved())
        assert unresolved == {neither, elevation_only, cell_only}
        assert done not in unresolved


@pytest.mark.django_db
class TestResortLocationSharing:
    """The sharing the many-to-many exists for."""

    def test_one_location_is_shared_by_four_resorts(self) -> None:
        """Mont Fort is one row that four resorts reference.

        The case the model exists for: the resort sheet currently records
        3330 as a scalar copied into four unrelated rows, which is real
        data stored as a coincidence.
        """
        mont_fort = LocationFactory.create(name="Mont Fort", kind=Location.KIND.PEAK)
        for name in ("Verbier", "Nendaz", "Veysonnaz", "Thyon"):
            ResortLocationFactory.create(
                resort=ResortFactory.create(name=name),
                location=mont_fort,
                role=ResortLocation.ROLE.TOP,
            )

        assert mont_fort.resort_locations.count() == 4
        assert Location.objects.filter(name="Mont Fort").count() == 1

    def test_one_location_holds_different_roles_for_different_resorts(self) -> None:
        """The same point is the top of one resort and the mid of another.

        This is why role lives on the through model rather than on the
        location, and why kind and role are not duplicates: Attelas is a
        mid-mountain place, and that is fixed, but what it is *to a resort*
        depends on the resort.
        """
        attelas = LocationFactory.create(name="Attelas", kind=Location.KIND.MID)
        small = ResortLocationFactory.create(
            location=attelas, role=ResortLocation.ROLE.TOP
        )
        verbier = ResortLocationFactory.create(
            location=attelas, role=ResortLocation.ROLE.MID
        )

        assert small.role == ResortLocation.ROLE.TOP
        assert verbier.role == ResortLocation.ROLE.MID
        assert small.location == verbier.location

    def test_same_resort_and_location_cannot_be_linked_twice(self) -> None:
        """unique_together rejects a duplicate link."""
        link = ResortLocationFactory.create()
        with pytest.raises(IntegrityError):
            ResortLocationFactory.create(
                resort=link.resort,
                location=link.location,
                role=ResortLocation.ROLE.BASE,
            )

    def test_to_string_names_both_ends_and_the_role(self) -> None:
        """to_string() reads resort -> location [ROLE]."""
        link = ResortLocationFactory.create(
            resort=ResortFactory.create(name="Verbier"),
            location=LocationFactory.create(
                name="Mont Fort",
                kind=Location.KIND.PEAK,
                latitude=46.10361,
                longitude=7.29889,
                elevation_m=3328.0,
            ),
            role=ResortLocation.ROLE.TOP,
        )
        # Resort.to_string() carries its region id, so build the expectation
        # from the resort rather than hard-coding a second model's format.
        assert link.to_string() == (
            f"{link.resort} -> Mont Fort (Peak) 46.10361,7.29889 @3328m [TOP]"
        )
        assert str(link) == link.to_string()

    def test_primary_selects_the_link_the_resort_leads_with(self) -> None:
        """primary() returns only the flagged link."""
        resort = ResortFactory.create()
        village = ResortLocationFactory.create(
            resort=resort, role=ResortLocation.ROLE.BASE, is_primary=True
        )
        ResortLocationFactory.create(resort=resort, role=ResortLocation.ROLE.TOP)

        assert list(resort.resort_locations.primary()) == [village]


@pytest.mark.django_db
class TestResortLocationDeletion:
    """PROTECT on location, CASCADE on resort."""

    def test_location_referenced_by_a_resort_cannot_be_deleted(self) -> None:
        """Deleting a shared location is refused while any link remains.

        Mont Fort must survive Verbier being deleted while Nendaz,
        Veysonnaz and Thyon still reference it.
        """
        link = ResortLocationFactory.create()
        with pytest.raises(ProtectedError):
            link.location.delete()

    def test_location_is_deletable_once_every_link_is_gone(self) -> None:
        """PROTECT is a live-reference guard, not a permanent one."""
        link = ResortLocationFactory.create()
        location = link.location
        link.delete()
        location.delete()
        assert not Location.objects.filter(pk=location.pk).exists()

    def test_deleting_a_resort_removes_its_links_and_keeps_the_location(self) -> None:
        """CASCADE clears the resort's own links; the shared place survives."""
        mont_fort = LocationFactory.create(name="Mont Fort")
        verbier = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(resort=verbier, location=mont_fort)
        ResortLocationFactory.create(
            resort=ResortFactory.create(name="Nendaz"), location=mont_fort
        )

        verbier.delete()

        assert Location.objects.filter(pk=mont_fort.pk).exists()
        assert mont_fort.resort_locations.count() == 1
