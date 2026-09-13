"""
tests/regions/services/test_area_content.py — Tests for the area_content service.

Covers:
  bboxes_overlap / point_in_bbox — the edge-inclusive rectangle rule
                      SNOW-953 moved out of ``basemap_download_core.js``,
                      including the golden vector that pins the behaviour
                      inherited from its retired JS twin.
  micro_region_index — the candidate set: every mapped micro-region with
                      a boundary, in ``region_id`` order, and nothing
                      whose parent is off the map or outside
                      ``MAP_COUNTRY_CODES``.
  weather_location_index — ``Location.objects.public()`` only; a
                      favourite-only location is never named.
  area_content       — the served answer's shape and ordering.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.locations.models import Location
from apps.regions.services.area_content import (
    area_content,
    bboxes_overlap,
    micro_region_index,
    point_in_bbox,
    weather_location_index,
)
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    ResortLocationFactory,
    SubRegionFactory,
)

# The golden vector: one box, and the answer for every interesting
# neighbour of it. These are the answers ``basemap_download_core.js``'s
# ``bboxesOverlap`` and ``pointInBBox`` gave before SNOW-953 moved the rule
# here, written down as values rather than left implicit in a port — so a
# later edit that "tidies" an edge case has to change a number a reviewer
# can see, rather than a comparison operator nobody notices.
GOLDEN_BOX: list[float] = [7.0, 46.0, 8.0, 47.0]
GOLDEN_OVERLAPS: list[tuple[list[float], bool]] = [
    ([8.0, 46.0, 9.0, 47.0], True),  # shares the eastern edge
    ([6.0, 45.0, 7.0, 46.0], True),  # shares the south-western corner
    ([7.4, 46.4, 7.5, 46.5], True),  # wholly inside
    ([8.01, 46.0, 9.0, 47.0], False),  # a hundredth of a degree clear
    ([7.0, 47.01, 8.0, 48.0], False),  # clear to the north
]
GOLDEN_POINTS: list[tuple[float, float, bool]] = [
    (7.0, 46.0, True),  # the south-western corner itself
    (8.0, 47.0, True),  # the north-eastern corner itself
    (7.5, 46.5, True),
    (8.01, 46.5, False),
    (7.5, 45.99, False),
]


def _square(bbox: list[float]) -> dict[str, Any]:
    """Return a GeoJSON Polygon covering ``bbox`` exactly.

    Args:
        bbox: ``[west, south, east, north]`` in degrees.

    Returns:
        A GeoJSON Polygon geometry dict.

    """
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


# ---------------------------------------------------------------------------
# bboxes_overlap / point_in_bbox
# ---------------------------------------------------------------------------


def test_bboxes_overlap_matches_the_golden_vector() -> None:
    """The rectangle rule answers exactly what its JS twin answered."""
    for other, expected in GOLDEN_OVERLAPS:
        assert bboxes_overlap(GOLDEN_BOX, other) is expected
        # Symmetric: "might these two touch" cannot depend on argument order.
        assert bboxes_overlap(other, GOLDEN_BOX) is expected


def test_point_in_bbox_matches_the_golden_vector() -> None:
    """Point containment answers exactly what its JS twin answered."""
    for lon, lat, expected in GOLDEN_POINTS:
        assert point_in_bbox(lon, lat, GOLDEN_BOX) is expected


def test_a_shared_edge_counts_as_an_overlap() -> None:
    """Edge-inclusive on purpose — a shared edge costs one page, not a bulletin.

    The contract in ``docs/decisions/inside-the-boundary-is-complete.md``
    picks over-inclusion every time, so tightening either predicate to a
    strict ``<`` is a defect and not a tidy-up.
    """
    assert bboxes_overlap([0.0, 0.0, 1.0, 1.0], [1.0, 1.0, 2.0, 2.0]) is True
    assert point_in_bbox(1.0, 1.0, [0.0, 0.0, 1.0, 1.0]) is True


def test_a_malformed_box_is_never_an_overlap() -> None:
    """An unanswerable question is not an overlap."""
    assert bboxes_overlap(GOLDEN_BOX, None) is False
    assert bboxes_overlap(None, GOLDEN_BOX) is False
    assert bboxes_overlap(GOLDEN_BOX, [7.0, 46.0, 8.0]) is False
    assert bboxes_overlap(GOLDEN_BOX, [7.0, 46.0, 8.0, float("nan")]) is False
    assert point_in_bbox(7.5, 46.5, [7.0, 46.0]) is False
    assert point_in_bbox(float("nan"), 46.5, GOLDEN_BOX) is False


# ---------------------------------------------------------------------------
# micro_region_index
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_micro_region_index_covers_every_mapped_region_in_id_order() -> None:
    """Every mapped, bounded micro-region, ordered so a plan is reproducible."""
    major = MajorRegionFactory.create(prefix="CH-2", country="CH")
    sub = SubRegionFactory.create(prefix="CH-21", major=major)
    MicroRegionFactory.create(
        region_id="CH-2102",
        name="Second Region",
        subregion=sub,
        boundary=_square([7.5, 46.5, 7.6, 46.6]),
    )
    MicroRegionFactory.create(
        region_id="CH-2101",
        name="First Region",
        subregion=sub,
        boundary=_square([7.0, 46.0, 7.1, 46.1]),
    )

    index = micro_region_index()

    assert [row.region_id for row in index] == ["CH-2101", "CH-2102"]
    assert [row.slug for row in index] == ["first-region", "second-region"]
    assert index[0].bbox == [7.0, 46.0, 7.1, 46.1]


@pytest.mark.django_db
def test_micro_region_index_skips_unmapped_unbounded_and_foreign_regions() -> None:
    """The candidate set is the one ``regions.geojson`` is built from.

    A region with no boundary cannot be measured, a parent flagged off the
    map is not drawn, and a country outside ``MAP_COUNTRY_CODES`` has no
    map to be inside of. Each is excluded by the same filter
    ``_build_micro_regions_payload`` applies.
    """
    hidden_major = MajorRegionFactory.create(
        prefix="CH-3", country="CH", display_on_map=False
    )
    hidden_sub = SubRegionFactory.create(prefix="CH-31", major=hidden_major)
    MicroRegionFactory.create(
        region_id="CH-3101",
        subregion=hidden_sub,
        boundary=_square([7.0, 46.0, 7.1, 46.1]),
    )

    shown_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    shown_sub = SubRegionFactory.create(prefix="CH-41", major=shown_major)
    MicroRegionFactory.create(region_id="CH-4101", subregion=shown_sub, boundary=None)

    foreign_major = MajorRegionFactory.create(prefix="DE-1", country="DE")
    foreign_sub = SubRegionFactory.create(prefix="DE-11", major=foreign_major)
    MicroRegionFactory.create(
        region_id="DE-1101",
        subregion=foreign_sub,
        boundary=_square([7.0, 46.0, 7.1, 46.1]),
    )

    assert micro_region_index() == []


@pytest.mark.django_db
def test_micro_region_index_skips_a_boundary_it_cannot_measure() -> None:
    """One unusable geometry loses its own region, never the other 460."""
    major = MajorRegionFactory.create(prefix="CH-5", country="CH")
    sub = SubRegionFactory.create(prefix="CH-51", major=major)
    MicroRegionFactory.create(
        region_id="CH-5101",
        subregion=sub,
        boundary={"type": "GeometryCollection", "geometries": []},
    )
    MicroRegionFactory.create(
        region_id="CH-5102",
        subregion=sub,
        boundary=_square([7.0, 46.0, 7.1, 46.1]),
    )

    assert [row.region_id for row in micro_region_index()] == ["CH-5102"]


# ---------------------------------------------------------------------------
# weather_location_index
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_weather_location_index_is_public_locations_only() -> None:
    """A favourite-only location is never named — the ``public()`` contract.

    ``active()`` would also reach it, and this endpoint is unauthenticated:
    naming one here would hand a stranger's private pin to every caller,
    the same leak ``weather_geojson`` refuses.
    """
    curated = LocationFactory.create(latitude=46.1, longitude=7.1)
    ResortLocationFactory.create(location=curated)
    private = LocationFactory.create(latitude=46.2, longitude=7.2)
    FavouriteFactory.create(location=private)

    short_ids = [point.short_id for point in weather_location_index()]

    assert curated.short_id in short_ids
    assert private.short_id not in short_ids


@pytest.mark.django_db
def test_weather_location_index_is_ordered_by_short_id() -> None:
    """Ordered, so two identical requests plan identically."""
    for _ in range(3):
        ResortLocationFactory.create(
            location=LocationFactory.create(latitude=46.1, longitude=7.1)
        )

    points = weather_location_index()

    assert [point.short_id for point in points] == list(
        Location.objects.public()
        .order_by("short_id")
        .values_list("short_id", flat=True)
    )


# ---------------------------------------------------------------------------
# area_content
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_area_content_returns_only_what_the_rectangle_touches(
    _mapped_regions: None,
) -> None:
    """The served answer: ids and slugs inside, nothing outside."""
    payload = area_content([7.05, 46.05, 7.25, 46.25])

    assert payload["regions"] == [
        {"id": "CH-9101", "slug": "inside"},
        {"id": "CH-9102", "slug": "straddles"},
    ]


@pytest.mark.django_db
def test_area_content_is_empty_for_a_box_touching_nothing(
    _mapped_regions: None,
) -> None:
    """A box over open sea names no region and no location."""
    payload = area_content([1.0, 41.0, 1.1, 41.1])

    assert payload == {"regions": [], "weather": []}


@pytest.mark.django_db
def test_area_content_names_the_locations_inside_the_box() -> None:
    """Weather is a point test, not a rectangle one."""
    inside = LocationFactory.create(latitude=46.15, longitude=7.15)
    ResortLocationFactory.create(location=inside)
    outside = LocationFactory.create(latitude=47.2, longitude=9.2)
    ResortLocationFactory.create(location=outside)

    payload = area_content([7.1, 46.1, 7.2, 46.2])

    assert payload["weather"] == [{"short_id": inside.short_id}]


@pytest.fixture
def _mapped_regions() -> None:
    """Create three mapped micro-regions: inside, straddling, and far away.

    The same three shapes ``tests/js/test_basemap_download_core.js`` used
    for ``areaContentPlan`` before SNOW-953, so the selection behaviour
    reads the same on both sides of the move.

    """
    major = MajorRegionFactory.create(prefix="CH-9", country="CH")
    sub = SubRegionFactory.create(prefix="CH-91", major=major)
    for region_id, name, bbox in (
        ("CH-9101", "Inside", [7.0, 46.0, 7.3, 46.3]),
        ("CH-9102", "Straddles", [7.2, 46.2, 7.6, 46.6]),
        ("CH-9103", "Far Away", [9.0, 47.0, 9.5, 47.5]),
    ):
        MicroRegionFactory.create(
            region_id=region_id, name=name, subregion=sub, boundary=_square(bbox)
        )
