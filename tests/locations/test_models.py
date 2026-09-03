"""
tests/locations/test_models.py — Tests for the Location and ResortLocation models.

Covers:
  - Factories produce valid instances via .create().
  - to_string() / __str__() for a named, an anonymous and an unresolved row.
  - Meta.ordering.
  - LocationQuerySet.named() / .anonymous() / .unresolved() / .active().
  - The sharing the M2M exists for: one location referenced by four
    resorts, holding a different role in each.
  - PROTECT on ResortLocation.location and CASCADE on .resort.
  - unique_together (resort, location).
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.locations.models import Location, ResortLocation
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
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

    def test_short_id_is_minted_by_default(self) -> None:
        """Every creation path gets an eleven-character opaque id (SNOW-797).

        The default lives on the field, following ``BaseModel.uuid``, so
        a bare ``Location.objects.create()`` cannot produce a row without
        one — that is the guarantee the field default exists to give.
        """
        location = Location.objects.create(latitude=46.1, longitude=7.4)
        assert location.short_id is not None
        assert len(location.short_id) == 11
        assert location.short_id != str(location.pk)

    def test_short_ids_are_distinct_per_row(self) -> None:
        """Two rows never share a short id."""
        ids = {LocationFactory.create().short_id for _ in range(5)}
        assert len(ids) == 5

    def test_short_id_is_unique_at_the_database(self) -> None:
        """The unique constraint is the backstop behind the default."""
        location = LocationFactory.create()
        with pytest.raises(IntegrityError):
            LocationFactory.create(short_id=location.short_id)

    def test_get_absolute_url_is_the_weather_page_keyed_on_short_id(self) -> None:
        """The weather page is the location's document — no pk in its URL."""
        location = LocationFactory.create()
        assert location.get_absolute_url() == f"/weather/{location.short_id}/"
        assert str(location.pk) not in location.get_absolute_url()

    def test_get_absolute_url_is_empty_without_a_short_id(self) -> None:
        """A row the backfill has not reached has no page (SNOW-810).

        ``short_id`` is nullable until a later migration tightens it, so
        every environment spends time between the SNOW-797 migration and
        ``backfill_location_short_ids``. Reversing on ``None`` raises
        ``NoReverseMatch``, which is a 500 on every surface that links to a
        location; the empty string is the answer a caller can act on.
        """
        location = LocationFactory.create(short_id=None)
        assert location.get_absolute_url() == ""

    def test_unresolved_by_default(self) -> None:
        """A fresh location has no elevation.

        Resolving one needs an Open-Meteo call, which cannot ride on a
        save, so it is filled out-of-band.
        """
        location = LocationFactory.create()
        assert location.elevation_m is None

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

    def test_unresolved_selects_rows_with_no_elevation(self) -> None:
        """unresolved() is exactly the rows still missing an elevation."""
        missing = LocationFactory.create()
        done = LocationFactory.create(resolved=True)

        unresolved = set(Location.objects.unresolved())
        assert unresolved == {missing}
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


@pytest.mark.django_db
class TestLocationQuerySetActive:
    """Tests for ``LocationQuerySet.active()`` — what costs money to fetch.

    The set this returns is the set that costs money: one Open-Meteo call
    per row, four times a day.

    It is **not** the set the map feed publishes. That is ``public()``,
    which is narrower — see ``TestLocationQuerySetPublic``. The two were
    briefly assumed to be one question during SNOW-761 scoping; they are
    not, and conflating them puts a private favourite on a public map.
    """

    def test_a_resort_location_is_active(self) -> None:
        """A place a resort points at is public and gets a forecast."""
        link = ResortLocationFactory.create()

        assert list(Location.objects.active()) == [link.location]

    def test_a_region_centroid_is_active(self) -> None:
        """A micro-region's centroid is how a region gets weather at all."""
        location = LocationFactory.create(anonymous=True)
        MicroRegionFactory.create(centroid_location=location)

        assert list(Location.objects.active()) == [location]

    def test_a_favourited_location_is_active(self) -> None:
        """A saved pin is a place its owner asked us to watch."""
        favourite = FavouriteFactory.create()

        assert list(Location.objects.active()) == [favourite.location]

    def test_an_observation_only_location_is_excluded(self) -> None:
        """A field report must never mint a billable forecast.

        This is the assertion the ticket asks for in place of a comment. A
        report is a user saying "this happened here", not a request to
        watch the spot — and including it would also let a private report
        raise a forecast panel on a public surface.
        """
        observation = FieldObservationFactory.create()

        assert observation.location is not None
        assert list(Location.objects.active()) == []

    def test_an_unreferenced_location_is_excluded(self) -> None:
        """A location nothing points at is not worth an upstream call."""
        LocationFactory.create()

        assert list(Location.objects.active()) == []

    def test_a_location_reachable_two_ways_appears_once(self) -> None:
        """A resort's village that someone has also favourited is fetched once.

        Without ``.distinct()`` the joins multiply and the location is
        billed twice per run.
        """
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
        FavouriteFactory.create(location=location)

        assert list(Location.objects.active()) == [location]

    def test_an_observation_at_an_otherwise_active_location_is_still_active(
        self,
    ) -> None:
        """The exclusion is of observation-ONLY locations, not of observations.

        A field report dropped on a resort's village must not remove that
        village from the fetch — the report is simply not what makes it
        active.
        """
        link = ResortLocationFactory.create()
        FieldObservationFactory.create(location=link.location)

        assert list(Location.objects.active()) == [link.location]


@pytest.mark.django_db
class TestLocationQuerySetPublic:
    """Tests for ``LocationQuerySet.public()`` — what anyone may see.

    SNOW-761's map weather feed renders this set. The boundary that matters
    is the one against ``active()``: a favourite is a billable pin but a
    private one, so it belongs in ``active()`` and must never reach here.
    """

    def test_a_resort_location_is_public(self) -> None:
        """A place a resort points at is already on a public page."""
        link = ResortLocationFactory.create()

        assert list(Location.objects.public()) == [link.location]

    def test_a_region_centroid_is_public(self) -> None:
        """A region's centroid represents the region, not a person."""
        location = LocationFactory.create(anonymous=True)
        MicroRegionFactory.create(centroid_location=location)

        assert list(Location.objects.public()) == [location]

    def test_a_favourite_only_location_is_NOT_public(self) -> None:
        """The privacy boundary — a saved pin must not reach a public feed.

        A favourite is one person's private place. Publishing it would put
        that person's saved location, and its coordinates, on a map every
        visitor can read. The pre-SNOW-762 forecast feed excluded
        favourite-only points for this reason; this assertion is what keeps
        the rebuilt feed honest.
        """
        favourite = FavouriteFactory.create()

        assert list(Location.objects.active()) == [favourite.location]
        assert list(Location.objects.public()) == []

    def test_an_observation_only_location_is_not_public(self) -> None:
        """A field report is not a curation act either."""
        observation = FieldObservationFactory.create()

        assert observation.location is not None
        assert list(Location.objects.public()) == []

    def test_public_is_a_subset_of_active(self) -> None:
        """Everything public is fetched; not everything fetched is public.

        Stated as one assertion so the direction of the containment cannot
        be quietly reversed by a later edit.
        """
        resort_link = ResortLocationFactory.create()
        favourite = FavouriteFactory.create()

        public = set(Location.objects.public())
        active = set(Location.objects.active())

        assert public < active
        assert resort_link.location in public
        assert favourite.location in active
        assert favourite.location not in public

    def test_a_location_reachable_two_ways_appears_once(self) -> None:
        """A resort's village that is also a region centroid joins twice."""
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
        MicroRegionFactory.create(centroid_location=location)

        assert list(Location.objects.public()) == [location]


@pytest.mark.django_db
class TestLocationQuerySetVisibleTo:
    """Tests for ``LocationQuerySet.visible_to()`` — what one reader may open.

    ``public()`` widened by the reader's OWN favourites, and nobody
    else's (SNOW-783). The favourite card links to the forecast page for
    the pin it describes, and that pin is private by construction — so
    the owner must get through while a stranger must not.
    """

    def test_a_public_location_is_visible_to_anyone(self) -> None:
        """The curated estate needs no account."""
        link = ResortLocationFactory.create()

        assert list(Location.objects.visible_to(AnonymousUser())) == [link.location]

    def test_my_own_favourite_pin_is_visible_to_me(self) -> None:
        """The owner following their card's forecast link gets through."""
        owner = UserFactory.create()
        favourite = FavouriteFactory.create(user=owner)

        assert favourite.location in Location.objects.visible_to(owner)

    def test_someone_elses_favourite_pin_is_not_visible_to_me(self) -> None:
        """The privacy contract is about strangers, and it holds."""
        favourite = FavouriteFactory.create(user=UserFactory.create())
        stranger = UserFactory.create()

        assert favourite.location not in Location.objects.visible_to(stranger)
        assert favourite.location not in Location.objects.visible_to(AnonymousUser())

    def test_it_never_widens_beyond_active(self) -> None:
        """Whatever a reader sees, we already pay to fetch it."""
        owner = UserFactory.create()
        FavouriteFactory.create(user=owner)
        ResortLocationFactory.create()
        FieldObservationFactory.create()

        visible = set(Location.objects.visible_to(owner))
        assert visible <= set(Location.objects.active())

    def test_a_location_reachable_two_ways_appears_once(self) -> None:
        """A curated place the reader has also favourited joins twice."""
        owner = UserFactory.create()
        location = LocationFactory.create()
        ResortLocationFactory.create(location=location)
        FavouriteFactory.create(user=owner, location=location)

        assert list(Location.objects.visible_to(owner)) == [location]
