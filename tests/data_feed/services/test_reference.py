"""
tests/data_feed/services/test_reference.py — Applying the v2 reference feeds (SNOW-1272).

The end-to-end path is in the command's tests; these pin the rules that
path does not reach in one run: a v1-pulled row adopted rather than
duplicated, a region centre moved, a resort naming something not here,
and every tombstone outcome — above all that a row this instance's users
point at is detached, never deleted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.data_feed.services.apply import ApplyError
from apps.data_feed.services.reference import (
    ABSENT,
    CREATED,
    DELETED,
    DETACHED,
    UNCHANGED,
    UPDATED,
    ReferenceIndex,
    apply_reference_location,
    apply_reference_pass,
    apply_reference_resort,
    apply_tombstone,
    reference_weather_lookups,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    PassFactory,
    ResortFactory,
    ResortLocationFactory,
    TripFactory,
)


def _place(source: uuid.UUID, **document: Any) -> dict[str, Any]:
    """Return a v2 locations item for ``source``."""
    return {
        "uuid": str(source),
        "deleted_at": None,
        "fields": {
            "name": "Attelas",
            "latitude": 46.1,
            "longitude": 7.2,
            "elevation_m": 2730.0,
        },
        "document": {"kind": "PEAK", "centroid_of": [], **document},
    }


@pytest.mark.django_db
class TestLocations:
    """A place is matched on ``source_uuid``, and claims its region centres."""

    def test_created_with_the_service_uuid_then_unchanged(self) -> None:
        """A new place keeps the service's uuid as its own, and a re-apply is a no-op."""
        source = uuid.uuid4()
        index = ReferenceIndex.load()
        assert apply_reference_location(_place(source), commit=True, index=index) == (
            CREATED
        )
        location = Location.objects.get(source_uuid=source)
        assert (location.uuid, location.name, location.kind) == (
            source,
            "Attelas",
            "PEAK",
        )
        assert (
            apply_reference_location(
                _place(source), commit=True, index=ReferenceIndex.load()
            )
            == UNCHANGED
        )

    def test_a_dry_run_writes_nothing(self) -> None:
        """Without commit the outcome is decided and nothing is saved."""
        outcome = apply_reference_location(
            _place(uuid.uuid4()), commit=False, index=ReferenceIndex.load()
        )
        assert outcome == CREATED
        assert not Location.objects.exists()

    def test_a_v1_pulled_row_is_adopted(self) -> None:
        """A row with the service uuid as its uuid but no source_uuid is reused."""
        existing = LocationFactory.create(name="Old name")
        index = ReferenceIndex.load()
        apply_reference_location(_place(existing.uuid), commit=True, index=index)
        existing.refresh_from_db()
        assert existing.source_uuid == existing.uuid
        assert existing.name == "Attelas"
        assert Location.objects.count() == 1

    def test_a_region_centre_moves(self) -> None:
        """centroid_of claims a region and releases one no longer named."""
        old = MicroRegionFactory.create(region_id="CH-1111")
        new = MicroRegionFactory.create(region_id="CH-2222")
        source = uuid.uuid4()
        apply_reference_location(
            _place(source, centroid_of=["CH-1111"]),
            commit=True,
            index=ReferenceIndex.load(),
        )
        location = Location.objects.get(source_uuid=source)
        old.refresh_from_db()
        assert old.centroid_location_id == location.pk

        outcome = apply_reference_location(
            _place(source, centroid_of=["CH-2222"]),
            commit=True,
            index=ReferenceIndex.load(),
        )
        assert outcome == UPDATED
        assert MicroRegion.objects.get(pk=old.pk).centroid_location_id is None
        assert MicroRegion.objects.get(pk=new.pk).centroid_location_id == location.pk


@pytest.mark.django_db
class TestPassesAndResorts:
    """Passes and resorts by ``source_uuid``, with their relations."""

    def test_a_pass_is_created_adopted_and_updated(self) -> None:
        """A v1 pass with the slug is adopted; a changed name is an update."""
        PassFactory.create(slug="magic-pass", name="Old")
        source = uuid.uuid4()
        item: dict[str, Any] = {
            "uuid": str(source),
            "slug": "magic-pass",
            "document": {"name": "Magic Pass", "website": ""},
        }
        assert apply_reference_pass(item, commit=True, index=ReferenceIndex.load()) == (
            CREATED
        )
        assert Pass.objects.get().source_uuid == source
        item["document"]["name"] = "Magic Pass 2027"
        assert apply_reference_pass(item, commit=True, index=ReferenceIndex.load()) == (
            UPDATED
        )
        assert Pass.objects.get().name == "Magic Pass 2027"

    def test_a_resort_with_its_links(self) -> None:
        """Region, pin, passes and roles are set from the document's uuids."""
        MicroRegionFactory.create(region_id="CH-4115")
        pin = LocationFactory.create(source_uuid=uuid.uuid4())
        pass_ = PassFactory.create(source_uuid=uuid.uuid4())
        source = uuid.uuid4()
        item = {
            "uuid": str(source),
            "slug": "verbier",
            "document": {
                "fields": {"name": "Verbier"},
                "region": "CH-4115",
                "location": str(pin.source_uuid),
                "passes": [str(pass_.source_uuid)],
                "locations": [
                    {
                        "location": str(pin.source_uuid),
                        "role": ResortLocation.ROLE.BASE,
                        "is_primary": True,
                    }
                ],
            },
        }
        assert (
            apply_reference_resort(item, commit=True, index=ReferenceIndex.load())
            == CREATED
        )
        resort = Resort.objects.get(source_uuid=source)
        assert (resort.name, resort.location, resort.region.region_id) == (
            "Verbier",
            pin,
            "CH-4115",
        )
        assert list(resort.passes.all()) == [pass_]
        assert resort.resort_locations.get().is_primary
        assert (
            apply_reference_resort(item, commit=True, index=ReferenceIndex.load())
            == UNCHANGED
        )

    def test_a_resort_naming_what_is_not_here_fails(self) -> None:
        """An unknown region or place is an ApplyError, not a half-written row."""
        MicroRegionFactory.create(region_id="CH-4115")
        item = {
            "uuid": str(uuid.uuid4()),
            "slug": "verbier",
            "document": {"fields": {}, "region": "CH-9999"},
        }
        with pytest.raises(ApplyError, match="unknown region"):
            apply_reference_resort(item, commit=True, index=ReferenceIndex.load())
        item["document"] = {
            "fields": {},
            "region": "CH-4115",
            "location": str(uuid.uuid4()),
        }
        with pytest.raises(ApplyError, match="unknown place"):
            apply_reference_resort(item, commit=True, index=ReferenceIndex.load())
        assert not Resort.objects.exists()


@pytest.mark.django_db
class TestTombstones:
    """A removal deletes, unless this instance's user data needs the row."""

    def test_an_unused_place_is_deleted_with_its_links(self) -> None:
        """No user points at it: the row goes, and its resort roles and pin."""
        link = ResortLocationFactory.create(location__source_uuid=uuid.uuid4())
        location = link.location
        Resort.objects.filter(pk=link.resort_id).update(location=location)
        region = MicroRegionFactory.create(centroid_location=location)
        outcome = apply_tombstone(
            "locations",
            {"uuid": str(location.source_uuid)},
            commit=True,
            index=ReferenceIndex.load(),
        )
        assert outcome == DELETED
        assert not Location.objects.filter(pk=location.pk).exists()
        assert not ResortLocation.objects.exists()
        region.refresh_from_db()
        assert region.centroid_location_id is None

    @pytest.mark.parametrize("user_data", ["favourite", "trip"])
    def test_a_place_users_point_at_is_detached(self, user_data: str) -> None:
        """A favourite or trip keeps its target; the row leaves the estate."""
        location = LocationFactory.create(source_uuid=uuid.uuid4())
        if user_data == "favourite":
            FavouriteFactory.create(location=location)
        else:
            TripFactory.create(meeting_point=location)
        outcome = apply_tombstone(
            "locations",
            {"uuid": str(location.source_uuid)},
            commit=True,
            index=ReferenceIndex.load(),
        )
        assert outcome == DETACHED
        location.refresh_from_db()
        assert location.source_uuid is None
        assert not Location.objects.reference_data().exists()

    def test_a_favourited_resort_is_detached_and_another_deleted(self) -> None:
        """A favourite keeps its resort; an unfavourited resort is deleted."""
        kept = ResortFactory.create(source_uuid=uuid.uuid4())
        FavouriteFactory.create(resort=kept)
        ResortLocationFactory.create(resort=kept)
        gone = ResortFactory.create(source_uuid=uuid.uuid4())
        index = ReferenceIndex.load()
        assert (
            apply_tombstone(
                "resorts", {"uuid": str(kept.source_uuid)}, commit=True, index=index
            )
            == DETACHED
        )
        assert (
            apply_tombstone(
                "resorts", {"uuid": str(gone.source_uuid)}, commit=True, index=index
            )
            == DELETED
        )
        kept.refresh_from_db()
        assert kept.source_uuid is None
        assert not kept.resort_locations.exists()
        assert not Resort.objects.filter(pk=gone.pk).exists()

    def test_a_pass_is_deleted_and_an_unknown_one_is_absent(self) -> None:
        """A pass carries no user data; a tombstone for nothing here is absent."""
        pass_ = PassFactory.create(source_uuid=uuid.uuid4())
        index = ReferenceIndex.load()
        assert (
            apply_tombstone(
                "passes", {"uuid": str(pass_.source_uuid)}, commit=True, index=index
            )
            == DELETED
        )
        assert (
            apply_tombstone(
                "passes", {"uuid": str(uuid.uuid4())}, commit=True, index=index
            )
            == ABSENT
        )
        assert not Pass.objects.exists()

    def test_a_dry_run_deletes_nothing(self) -> None:
        """Without commit the outcome is decided and the row stays."""
        location = LocationFactory.create(source_uuid=uuid.uuid4())
        outcome = apply_tombstone(
            "locations",
            {"uuid": str(location.source_uuid)},
            commit=False,
            index=ReferenceIndex.load(),
        )
        assert outcome == DELETED
        assert Location.objects.filter(pk=location.pk).exists()


@pytest.mark.django_db
def test_weather_lookups_resolve_the_service_uuid() -> None:
    """A v2 weather item names its place by source uuid; a bad one is skipped."""
    location = LocationFactory.create(source_uuid=uuid.uuid4())
    lookups = reference_weather_lookups(
        [
            {"location": str(location.source_uuid), "observed_on": "2026-10-01"},
            {"location": "not-a-uuid", "observed_on": "2026-10-01"},
        ],
        planned={"x"},
    )
    assert lookups.locations == {str(location.source_uuid): location}
    assert lookups.planned == {"x"}
