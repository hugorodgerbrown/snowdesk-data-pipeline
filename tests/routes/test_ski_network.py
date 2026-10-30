"""
tests/routes/test_ski_network.py — Applying the ski-touring feeds (SNOW-1276).

``apply_ski_items`` on pages shaped as the v2 feeds serve them: created,
unchanged by ``updated_at``, rewritten, deleted by a tombstone, absent,
failed; a dry run deciding without writing; ``synced_since``; and
``apply_from_service`` on the service's own rows.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.utils import timezone

from apps.ingest.public import serialise_source_document, serialise_source_ski_tour
from apps.routes.models import SkiSegment, SkiTour
from apps.routes.services.ski_network import (
    ABSENT,
    CREATED,
    DELETED,
    FAILED,
    UNCHANGED,
    UPDATED,
    SkiItemError,
    apply_from_service,
    apply_ski_items,
    bbox_of,
    synced_since,
)
from tests.factories import (
    SkiSegmentFactory,
    SourceSkiSegmentFactory,
    SourceSkiTourFactory,
)

pytestmark = pytest.mark.django_db

STAMP = "2026-10-28T12:00:00+00:00"
LINE = {"type": "MultiLineString", "coordinates": [[[10.0, 46.8], [10.2, 46.9]]]}


def _segment(key: uuid.UUID | None = None, **overrides: Any) -> dict[str, Any]:
    """A ``ski-network`` item."""
    item: dict[str, Any] = {
        "uuid": str(key or uuid.uuid4()),
        "slug": "segment-7",
        "updated_at": STAMP,
        "deleted_at": None,
        "document": {
            "segment_id": 7,
            "discipline": "ski_tour",
            "direction": 2,
            "access": 1,
            "route_info": 1,
            "geometry": LINE,
        },
    }
    item.update(overrides)
    return item


def test_bbox_of_either_line_type() -> None:
    """A MultiLineString or a LineString; an empty line is refused."""
    assert bbox_of(LINE) == (10.0, 46.8, 10.2, 46.9)
    assert bbox_of({"type": "LineString", "coordinates": [[1, 2], [3, 0]]}) == (
        1,
        0,
        3,
        2,
    )
    with pytest.raises(SkiItemError):
        bbox_of({"type": "MultiLineString", "coordinates": []})


def test_a_new_segment_is_created_with_its_bbox() -> None:
    """Every column from the document, the box from the line."""
    item = _segment()
    assert apply_ski_items("ski-network", [item], commit=True)[CREATED] == 1
    row = SkiSegment.objects.get()
    assert str(row.source_uuid) == item["uuid"]
    assert (row.segment_id, row.direction, row.access) == (7, 2, 1)
    assert (row.west, row.south, row.east, row.north) == (10.0, 46.8, 10.2, 46.9)
    assert row.source_updated_at == dt.datetime(2026, 10, 28, 12, tzinfo=dt.UTC)


def test_the_same_updated_at_is_unchanged_and_a_newer_one_rewrites() -> None:
    """The service moves updated_at only on a change, so it is the test."""
    item = _segment()
    apply_ski_items("ski-network", [item], commit=True)
    assert apply_ski_items("ski-network", [item], commit=True)[UNCHANGED] == 1
    moved = _segment(
        uuid.UUID(item["uuid"]),
        updated_at="2026-10-29T12:00:00+00:00",
        document={**item["document"], "direction": 0},
    )
    assert apply_ski_items("ski-network", [moved], commit=True)[UPDATED] == 1
    assert SkiSegment.objects.get().direction == 0


def test_a_tombstone_deletes_or_is_absent() -> None:
    """A held line goes; one never held is counted absent."""
    row = SkiSegmentFactory.create()
    counts = apply_ski_items(
        "ski-network",
        [
            _segment(row.source_uuid, deleted_at=STAMP),
            _segment(deleted_at=STAMP),
        ],
        commit=True,
    )
    assert (counts[DELETED], counts[ABSENT]) == (1, 1)
    assert not SkiSegment.objects.exists()


def test_a_malformed_item_fails_and_its_page_writes_nothing() -> None:
    """No uuid, no updated_at, no line, or no id: each is counted failed.

    The good item beside them is counted but not written, so the newest
    line held, which the next pull starts from, never passes a failure.
    """
    bad = [
        _segment() | {"uuid": "nope"},
        _segment(updated_at=None),
        _segment(document={"segment_id": 1, "geometry": {"coordinates": []}}),
        _segment(document={"geometry": LINE}),
    ]
    counts = apply_ski_items("ski-network", [*bad, _segment()], commit=True)
    assert (counts[FAILED], counts[CREATED]) == (4, 1)
    assert not SkiSegment.objects.exists()


def test_a_dry_run_decides_without_writing() -> None:
    """Created, updated and deleted are counted; nothing changes."""
    held = SkiSegmentFactory.create(direction=0)
    gone = SkiSegmentFactory.create()
    counts = apply_ski_items(
        "ski-network",
        [
            _segment(),
            _segment(held.source_uuid),
            _segment(gone.source_uuid, deleted_at=STAMP),
        ],
        commit=False,
    )
    assert (counts[CREATED], counts[UPDATED], counts[DELETED]) == (1, 1, 1)
    assert SkiSegment.objects.count() == 2
    held.refresh_from_db()
    assert held.direction == 0


def test_a_tour_is_created_from_its_document() -> None:
    """The tour's columns, as SNOW-1278 reads them."""
    source = SourceSkiTourFactory.create()
    apply_ski_items("ski-tours", [serialise_source_document(source)], commit=True)
    tour = SkiTour.objects.get()
    assert tour.source_uuid == source.uuid
    assert (tour.name, tour.grade, tour.target_altitude_m) == (
        source.document["name"],
        "PD",
        3312,
    )
    assert tour.urls == {"en": "https://www.sac-cas.ch/en/"}
    assert tour.to_string() == f"{tour.name} (PD)"


def test_synced_since_is_the_newest_held() -> None:
    """None before any line; the newest source_updated_at after."""
    assert synced_since("ski-network") is None
    newest = timezone.now()
    SkiSegmentFactory.create(source_updated_at=newest - dt.timedelta(days=1))
    SkiSegmentFactory.create(source_updated_at=newest)
    assert synced_since("ski-network") == newest


def test_the_service_host_applies_its_own_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every row first; then only what changed since; a tombstone deletes."""
    monkeypatch.setattr("apps.routes.services.ski_network.APPLY_CHUNK", 2)
    segments = SourceSkiSegmentFactory.create_batch(3)
    SourceSkiTourFactory.create()
    result = apply_from_service(commit=True)
    assert result["ski-network"][CREATED] == 3
    assert result["ski-tours"][CREATED] == 1
    segments[0].deleted_at = timezone.now()
    segments[0].save()
    result = apply_from_service(commit=True)
    assert result["ski-network"][DELETED] == 1
    assert result["ski-network"][CREATED] == 0
    assert SkiSegment.objects.count() == 2


def test_the_service_host_stops_at_a_failed_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page with a failure, and every page after it, is left for next time."""
    monkeypatch.setattr("apps.routes.services.ski_network.APPLY_CHUNK", 1)
    stamp = timezone.now()
    SourceSkiSegmentFactory.create()
    broken = SourceSkiSegmentFactory.create()
    SourceSkiSegmentFactory.create()
    type(broken).objects.filter(pk=broken.pk).update(
        document={"geometry": LINE}, updated_at=stamp + dt.timedelta(seconds=1)
    )
    later = SourceSkiSegmentFactory.create()
    type(later).objects.filter(pk=later.pk).update(
        updated_at=stamp + dt.timedelta(seconds=2)
    )
    result = apply_from_service(commit=True)
    assert result["ski-network"][FAILED] == 1
    assert SkiSegment.objects.count() == 2
    assert not SkiSegment.objects.filter(source_uuid=later.uuid).exists()


def test_a_rewritten_tour_forgets_its_terrain() -> None:
    """A newer document clears the sampled terrain SNOW-1278 kept on it."""
    source = SourceSkiTourFactory.create()
    apply_ski_items("ski-tours", [serialise_source_document(source)], commit=True)
    SkiTour.objects.update(slope_samples={"v": 1}, track_render={"v": 1})
    item = serialise_source_document(source)
    item["updated_at"] = (timezone.now() + dt.timedelta(days=1)).isoformat()
    counts = apply_ski_items("ski-tours", [item], commit=True)
    assert counts[UPDATED] == 1
    # SNOW-1309: both together, never one without the other.
    tour = SkiTour.objects.get()
    assert tour.slope_samples is None
    assert tour.track_render is None


def test_a_tour_takes_the_walk_and_drawing_the_service_made() -> None:
    """SNOW-1316: copied as served, on create and on an incremental pull."""
    source = SourceSkiTourFactory.create(
        slope_samples={"segments": []}, track_render={"v": 1, "legs": []}
    )
    apply_ski_items("ski-tours", [serialise_source_ski_tour(source)], commit=True)
    tour = SkiTour.objects.get()
    assert tour.slope_samples == {"segments": []}
    assert tour.track_render == {"v": 1, "legs": []}

    item = serialise_source_ski_tour(source)
    item["track_render"] = {"v": 1, "legs": [{"climbing": True}]}
    item["updated_at"] = (timezone.now() + dt.timedelta(days=1)).isoformat()
    assert apply_ski_items("ski-tours", [item], commit=True)[UPDATED] == 1
    tour.refresh_from_db()
    assert tour.track_render == {"v": 1, "legs": [{"climbing": True}]}


def test_a_tour_the_service_has_not_drawn_holds_neither() -> None:
    """A drawing without its walk is not taken: both or neither (SNOW-1309)."""
    source = SourceSkiTourFactory.create(track_render={"v": 1})
    apply_ski_items("ski-tours", [serialise_source_ski_tour(source)], commit=True)
    tour = SkiTour.objects.get()
    assert (tour.slope_samples, tour.track_render) == (None, None)


def test_the_service_host_applies_its_own_tours_drawing() -> None:
    """The host's own apply reads a tour as the feed serves it."""
    SourceSkiTourFactory.create(slope_samples={"segments": []}, track_render={"v": 1})
    apply_from_service(commit=True)
    assert SkiTour.objects.get().track_render == {"v": 1}
