"""
apps/regions/services/area_content.py — what sits inside a downloaded area.

SNOW-953. An offline download has to know which bulletins and which
weather sheets its boundary contains. The client used to work that out
for itself, which meant loading every country's region outlines first —
764 KB over the wire to discover roughly 55 KB of pages, on the thin
connection the whole feature exists to serve.

The selection RULE is unchanged and still the one
``docs/decisions/inside-the-boundary-is-complete.md`` describes: a crude,
edge-inclusive rectangle over each region's bounding box, never real
geometry, deliberately over-inclusive. What changes is where it runs.
The server already holds every boundary, so it can answer "what is inside
this rectangle" in one small response — and the failure mode SNOW-931
closed (the candidate set being whatever countries the client happened to
have loaded) stops being possible by construction, because the candidate
set is now the whole mapped estate every time.

Public API:
    micro_region_index()             — ``[RegionBox]`` over every mapped
                                        micro-region, in ``region_id``
                                        order.
    weather_location_index()         — ``[WeatherPoint]`` over every
                                        PUBLIC ``Location``, in
                                        ``short_id`` order.
    bboxes_overlap(a, b)             — edge-inclusive rectangle overlap.
    point_in_bbox(lon, lat, bbox)    — edge-inclusive point containment.
    area_content(bbox, …)            — the answer served by
                                        ``/api/area-content/``.

The two predicates are the Python twins of
``static/js/basemap_download_core.js``'s ``bboxesOverlap`` and
``pointInBBox``, which SNOW-953 removed from that file; their
edge-inclusive rule and its rationale moved here with them, and
``tests/regions/services/test_area_content.py`` pins a golden vector
recording the answers that JS twin gave.

Coordinate convention: **(lon, lat) order** throughout, matching GeoJSON
positions and ``basemap_tiles.py`` next door — the same deliberate
carve-out from the project's usual (lat, lon) argument order, and for the
same reason (a reader porting between this module and its JS counterpart
never has to swap axes).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any, NamedTuple, TypeGuard, cast

from django.conf import settings
from django.utils.text import slugify

from apps.locations.models import Location
from apps.regions.models import MicroRegion
from apps.regions.services.basemap_tiles import bbox_from_boundary

logger = logging.getLogger(__name__)

# A ``[west, south, east, north]`` rectangle in degrees.
BBox = list[float]


class RegionBox(NamedTuple):
    """One micro-region reduced to what a rectangle test needs.

    Attributes:
        region_id: The region's EAWS id, e.g. ``"CH-4115"`` — the same
            value ``regions.geojson`` puts on ``properties.id``.
        slug: The name-derived slug that forms a bulletin URL's second
            path component (``MicroRegion.name_slug``).
        bbox: ``[west, south, east, north]`` over the region's boundary.

    """

    region_id: str
    slug: str
    bbox: BBox


class WeatherPoint(NamedTuple):
    """One public weather location reduced to what a point test needs.

    Attributes:
        short_id: ``Location.short_id`` — never the primary key
            (``docs/decisions/no-integer-pks-in-urls.md``), and the key
            the weather-detail URL is built from.
        lon: Longitude in degrees.
        lat: Latitude in degrees.

    """

    short_id: str
    lon: float
    lat: float


def micro_region_index() -> list[RegionBox]:
    """Return a bounding box for every micro-region the map draws.

    The filter is the one ``_build_micro_regions_payload``
    (``apps/public/api.py``) applies — a mapped parent
    (``display_on_map``), a stored boundary, and any of
    ``settings.MAP_COUNTRY_CODES`` — so the candidate set here is exactly
    the one the client used to plan from when it had loaded all four
    country feeds. Anything narrower would under-fetch; anything wider
    would name a region no bulletin page exists for.

    Only three columns are read. The boundary is the expensive one and
    cannot be avoided (it is what the box is derived from), so this is
    memoised by its caller rather than made cheap here.

    Returns:
        One ``RegionBox`` per region, ordered by ``region_id`` so a plan
        built from it is reproducible.

    """
    countries = [code.upper() for code in settings.MAP_COUNTRY_CODES]
    rows = (
        MicroRegion.objects.filter(
            subregion__major__country__in=countries,
            subregion__major__display_on_map=True,
            boundary__isnull=False,
        )
        .order_by("region_id")
        .values_list("region_id", "name", "boundary")
    )
    index: list[RegionBox] = []
    for region_id, name, boundary in rows.iterator():
        try:
            bbox = bbox_from_boundary(cast("dict[str, Any]", boundary))
        except IndexError, KeyError, TypeError, ValueError:
            # A boundary this module cannot measure is one no rectangle
            # test can answer for. Skipping it keeps the endpoint serving
            # the other 460 regions rather than 500-ing the whole plan on
            # one malformed row; the log line is what an operator reads.
            #
            # IndexError is in that list for a position too short to be
            # one — ``[]`` or ``[7.0]`` — which ``bbox_from_boundary``
            # reaches through to ``pos[1]`` on. Every other malformation
            # it can meet raises one of the three beside it: a geometry
            # with no ``type`` (KeyError), a non-sequence position
            # (TypeError), an unsupported type or a ring with no
            # positions at all (ValueError).
            logger.warning(
                "area_content: unusable boundary on micro-region %s", region_id
            )
            continue
        index.append(RegionBox(region_id=region_id, slug=slugify(name), bbox=bbox))
    return index


def weather_location_index() -> list[WeatherPoint]:
    """Return a point for every weather location anyone may see.

    ``Location.objects.public()``, never ``active()`` — the same privacy
    contract ``weather_geojson`` states. ``active()`` also reaches the
    locations a ``Favourite`` points at, and naming one here would tell
    an unauthenticated caller the coordinates of a stranger's private pin
    just as surely as putting it on the map feed would.

    A row whose ``short_id`` is still null is skipped: it has no weather
    page for a plan to name (``Location.get_absolute_url`` returns ``""``
    for one), so naming it would put a URL in the plan that cannot be
    built.

    Returns:
        One ``WeatherPoint`` per location, ordered by ``short_id`` so a
        plan built from it is reproducible.

    """
    rows = (
        Location.objects.public()
        .exclude(short_id__isnull=True)
        .order_by("short_id")
        .values_list("short_id", "longitude", "latitude")
    )
    return [
        WeatherPoint(short_id=cast("str", short_id), lon=lon, lat=lat)
        for short_id, lon, lat in rows.iterator()
    ]


def bboxes_overlap(a: BBox | None, b: BBox | None) -> bool:
    """Return whether two ``[west, south, east, north]`` boxes touch or overlap.

    INCLUSIVE at the edges. The question this answers is only "might this
    region have anything in the area", and a shared edge costs one HTML
    page to include and a missing bulletin to exclude — the contract in
    ``docs/decisions/inside-the-boundary-is-complete.md`` picks the page.
    The rule and this wording moved here from
    ``basemap_download_core.js``'s ``bboxesOverlap`` (SNOW-924, SNOW-953);
    a golden vector records the answers that implementation gave.

    Args:
        a: A box, or anything that is not one.
        b: A box, or anything that is not one.

    Returns:
        ``False`` when either is not a well-formed box — an unanswerable
        question is not an overlap.

    """
    if not _is_bbox(a) or not _is_bbox(b):
        return False
    west_a, south_a, east_a, north_a = a
    west_b, south_b, east_b, north_b = b
    return (
        west_a <= east_b
        and west_b <= east_a
        and south_a <= north_b
        and south_b <= north_a
    )


def point_in_bbox(lon: float, lat: float, bbox: BBox | None) -> bool:
    """Return whether a point sits in a ``[west, south, east, north]`` box.

    Inclusive at the edges, for the same reason as ``bboxes_overlap``.

    Args:
        lon: Longitude in degrees.
        lat: Latitude in degrees.
        bbox: The box to test against.

    Returns:
        ``False`` for a malformed box or a non-finite coordinate.

    """
    if not _is_bbox(bbox) or not _is_finite(lon) or not _is_finite(lat):
        return False
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def area_content(
    bbox: BBox,
    *,
    regions: list[RegionBox] | None = None,
    locations: list[WeatherPoint] | None = None,
) -> dict[str, Any]:
    """Return the regions and weather locations inside ``bbox``.

    The whole answer ``/api/area-content/`` serves. The client composes
    the URLs from it: a bulletin page per (region, day) over its own day
    window, and one undated weather sheet per location.

    Args:
        bbox: ``[west, south, east, north]`` in degrees, already
            validated by the caller.
        regions: The candidate regions. Defaults to a freshly built
            ``micro_region_index()``; the view passes a memoised one.
        locations: The candidate locations, likewise defaulting to
            ``weather_location_index()``.

    Returns:
        ``{"regions": [{"id", "slug"}, …], "weather": [{"short_id"}, …]}``,
        each list in the index's own stable order.

    """
    candidates = micro_region_index() if regions is None else regions
    points = weather_location_index() if locations is None else locations
    return {
        "regions": [
            {"id": region.region_id, "slug": region.slug}
            for region in candidates
            if bboxes_overlap(bbox, region.bbox)
        ],
        "weather": [
            {"short_id": point.short_id}
            for point in points
            if point_in_bbox(point.lon, point.lat, bbox)
        ],
    }


def _is_bbox(value: object) -> TypeGuard[Sequence[float]]:
    """Return whether ``value`` is a well-formed four-number box.

    Args:
        value: The candidate box.

    Returns:
        True for a sequence of exactly four finite numbers.

    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    return all(_is_finite(item) for item in value)


def _is_finite(value: object) -> TypeGuard[float]:
    """Return whether ``value`` is a finite real number.

    ``bool`` is excluded deliberately: ``True`` is an ``int`` in Python,
    and a box carrying one is malformed input rather than a box at 1°.

    Args:
        value: The candidate number.

    Returns:
        True for a finite ``int`` or ``float``.

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)
