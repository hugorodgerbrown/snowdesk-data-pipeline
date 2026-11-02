"""
tests/ingest/test_rest_zones.py — The service's rest-zone import (SNOW-1263).

Against a fake geo.admin: a first import creates, a second changes
nothing, a vanished zone is tombstoned, and a failing canton — or an
unreadable zone, or an empty answer for a canton that holds zones —
writes nothing, so the zones already held survive.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from apps.ingest.models import SourceRestZone
from apps.ingest.rest_zones import (
    REST_ZONES,
    RestZoneImportError,
    import_rest_zones,
)
from apps.ingest.sources.geo_admin import GeoAdminError
from tests.ingest.normalise.test_rest_zones import feature

pytestmark = pytest.mark.django_db


def geo_admin(
    zones: dict[str, list[dict[str, Any]]], failing: str | None = None
) -> Callable[[str], list[dict[str, Any]]]:
    """A fake ``fetch_canton``: ``zones`` by canton, ``failing`` raising."""

    def fetch(canton: str) -> list[dict[str, Any]]:
        if canton == failing:
            raise GeoAdminError(f"{canton} is down")
        return zones.get(canton, [])

    return fetch


def test_a_first_import_creates_every_zone() -> None:
    """Two cantons' zones, every other canton empty."""
    counts = import_rest_zones(geo_admin({"GR": [feature(1)], "VS": [feature(2)]}))
    assert counts[REST_ZONES]["created"] == 2
    assert SourceRestZone.objects.live().count() == 2


def test_a_second_import_changes_nothing() -> None:
    """The feed's "since" stays quiet between FOEN's edits."""
    fetch = geo_admin({"GR": [feature(1), feature(2)]})
    import_rest_zones(fetch)
    counts = import_rest_zones(fetch)[REST_ZONES]
    assert counts["unchanged"] == 2
    assert counts["created"] == counts["updated"] == counts["deleted"] == 0


def test_a_vanished_zone_is_tombstoned() -> None:
    """FOEN drops one; the service keeps it as a tombstone."""
    import_rest_zones(geo_admin({"GR": [feature(1), feature(2)]}))
    counts = import_rest_zones(geo_admin({"GR": [feature(1)]}))[REST_ZONES]
    assert counts["deleted"] == 1
    assert SourceRestZone.objects.tombstoned().count() == 1


def test_a_renamed_zone_is_updated_in_place() -> None:
    """Same feature id, same row: identity is not the name."""
    import_rest_zones(geo_admin({"GR": [feature(1)]}))
    before = SourceRestZone.objects.get()
    import_rest_zones(geo_admin({"GR": [feature(1, name="Renamed")]}))
    after = SourceRestZone.objects.get()
    assert after.uuid == before.uuid
    assert after.document["name"] == "Renamed"


def test_a_failing_canton_writes_nothing() -> None:
    """One canton down: no tombstones, no creations, an error naming it."""
    import_rest_zones(geo_admin({"GR": [feature(1)], "VS": [feature(2)]}))
    with pytest.raises(RestZoneImportError, match="VS"):
        import_rest_zones(geo_admin({"GR": [feature(3)]}, failing="VS"))
    assert SourceRestZone.objects.live().count() == 2
    assert not SourceRestZone.objects.tombstoned().exists()
    assert not SourceRestZone.objects.filter(document__feature_id=3).exists()


def test_an_unreadable_zone_writes_nothing() -> None:
    """A zone with no polygon would otherwise tombstone itself."""
    import_rest_zones(geo_admin({"GR": [feature(1)]}))
    broken = feature(1)
    broken["geometry"] = {}
    with pytest.raises(RestZoneImportError, match="no polygon"):
        import_rest_zones(geo_admin({"GR": [broken]}))
    assert SourceRestZone.objects.live().count() == 1


def test_a_canton_that_answers_empty_while_holding_zones_writes_nothing() -> None:
    """An empty 200 for GR is not a repeal of every zone in GR."""
    import_rest_zones(geo_admin({"GR": [feature(1), feature(2)]}))
    with pytest.raises(RestZoneImportError, match="GR answered no zones but 2"):
        import_rest_zones(geo_admin({}))
    assert SourceRestZone.objects.live().count() == 2
    assert not SourceRestZone.objects.tombstoned().exists()


def test_an_empty_answer_everywhere_is_refused_even_on_a_first_import() -> None:
    """Nothing held and nothing answered: a broken layer, not an empty country."""
    with pytest.raises(RestZoneImportError, match="no zones at all"):
        import_rest_zones(geo_admin({}))
    assert not SourceRestZone.objects.exists()
