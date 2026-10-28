"""
tests/regions/services/test_reference.py — Mirroring passes and resorts into the service (SNOW-1271).

Production is where the estate is curated, so its ``Pass`` and ``Resort``
rows are copied into ``SourcePass`` and ``SourceResort`` under their own
uuids, every relation named by the service's uuid. A resort with no slug
has no key on the wire and is left out.
"""

from __future__ import annotations

import pytest

from apps.ingest.public import SourceLocation, SourcePass, SourceResort
from apps.regions.models import Pass, Resort
from apps.regions.services.reference import (
    mirror_passes,
    mirror_reference_data,
    mirror_resorts,
    resort_document,
)
from tests.factories import (
    LocationFactory,
    PassFactory,
    ResortFactory,
    ResortLocationFactory,
)

pytestmark = pytest.mark.django_db


def _resort_with_everything() -> Resort:
    """Return a geocoded resort on a pass, with one village role."""
    resort = ResortFactory.create(
        name="Verbier", latitude=46.0961, longitude=7.2286, num_lifts=34
    )
    resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic Pass"))
    ResortLocationFactory.create(
        resort=resort,
        location=LocationFactory.create(name="Verbier village"),
        role="VILLAGE",
        is_primary=True,
    )
    return resort


class TestMirrorPasses:
    """Each pass becomes a SourcePass under its own uuid."""

    def test_a_pass_is_linked_and_mirrored(self) -> None:
        """``source_uuid`` is the pass's uuid; the document is name and site."""
        ski_pass = PassFactory.create(
            slug="magic-pass", name="Magic Pass", website="https://magicpass.ch"
        )
        counts = mirror_passes()
        ski_pass.refresh_from_db()
        assert ski_pass.source_uuid == ski_pass.uuid
        row = SourcePass.objects.get()
        assert row.uuid == ski_pass.uuid
        assert row.slug == "magic-pass"
        assert row.document == {"name": "Magic Pass", "website": "https://magicpass.ch"}
        assert counts["linked"] == 1
        assert counts["created"] == 1
        assert list(Pass.objects.reference_data()) == [ski_pass]

    def test_a_second_run_changes_nothing(self) -> None:
        """Linked once; afterwards the mirror is a no-op."""
        PassFactory.create()
        mirror_passes()
        counts = mirror_passes()
        assert (counts["linked"], counts["unchanged"]) == (0, 1)


class TestMirrorResorts:
    """Each slugged resort becomes a SourceResort."""

    def test_the_document_names_every_relation_by_the_services_uuid(self) -> None:
        """Region by code; pin, roles and passes by source uuid."""
        resort = _resort_with_everything()
        mirror_reference_data()
        resort.refresh_from_db()
        pin = resort.location
        assert pin is not None
        pin.refresh_from_db()
        link = resort.resort_locations.get()
        link.location.refresh_from_db()
        ski_pass = resort.passes.get()

        row = SourceResort.objects.get()
        assert row.uuid == resort.uuid == resort.source_uuid
        assert row.slug == resort.slug
        document = row.document
        assert document["region"] == resort.region.region_id
        assert document["location"] == str(pin.source_uuid)
        assert document["passes"] == [str(ski_pass.source_uuid)]
        assert document["locations"] == [
            {
                "location": str(link.location.source_uuid),
                "role": "VILLAGE",
                "is_primary": True,
            }
        ]
        assert document["fields"]["name"] == "Verbier"
        assert document["fields"]["num_lifts"] == 34
        assert document["fields"]["latitude"] == 46.0961
        assert "source_uuid" not in document["fields"]
        assert list(Resort.objects.reference_data()) == [resort]

    def test_every_place_a_resort_names_is_a_station(self) -> None:
        """A client can resolve the document: pin and roles are all mirrored."""
        _resort_with_everything()
        mirror_reference_data()
        document = SourceResort.objects.get().document
        named = {document["location"]} | {
            link["location"] for link in document["locations"]
        }
        stations = {
            str(u) for u in SourceLocation.objects.values_list("uuid", flat=True)
        }
        assert named <= stations

    def test_a_resort_without_a_slug_is_left_out(self) -> None:
        """A slug is a resort's key on the wire; until it has one it is not sent."""
        resort = ResortFactory.create()
        Resort.objects.filter(pk=resort.pk).update(slug=None)
        mirror_passes()
        assert mirror_resorts()["created"] == 0

    def test_a_second_run_changes_nothing_and_a_removed_resort_is_tombstoned(
        self,
    ) -> None:
        """The document hashes the same each time; a deleted resort leaves a tombstone."""
        resort = _resort_with_everything()
        mirror_reference_data()
        assert mirror_reference_data()["resorts"]["unchanged"] == 1

        resort.delete()
        assert mirror_reference_data()["resorts"]["deleted"] == 1
        assert SourceResort.objects.tombstoned().count() == 1

    def test_a_place_without_a_source_uuid_is_refused(self) -> None:
        """The location mirror must run first; skipping it is a bug, not a gap."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        with pytest.raises(ValueError, match="no source_uuid"):
            resort_document(resort)
