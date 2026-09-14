"""
apps/locations/services/terrain_sources.py — who surveyed the ground here.

Contains one enum, one dataclass and three functions:

  TerrainQuality
      The ranked survey tiers a source can belong to. ``lidar`` is the only
      one published today; the coarser tiers exist so the ordering is real
      rather than notional.

  TerrainSource
      One entry from ``grid.json``'s ``sources[]`` — who made the height
      data, at what native resolution, under what licence, and over which
      rectangle of EPSG:3035.

  source_from_payload(entry)
      Builds one from the published JSON, raising rather than guessing.

  covers(source, easting, northing)
      Whether a projected point falls inside a source's coverage bbox.

  select_source(sources, easting, northing)
      Which source answers for a point when more than one could.

**The bbox is a deliberate SUPERSET, not the coverage.** swissALTI3D fills
27,331 of the 53,760 tile slots its rectangle spans, because Switzerland is
a diagonal country in an axis-aligned box. A ``True`` from ``covers`` means
"a source claims this rectangle, so it is worth asking the origin" — the
origin's ``204 No Content`` is the real answer, and it is the one
``apps.locations.services.terrain`` reports as ``OUTSIDE_COVERAGE``. Making
this predicate exact would mean shipping the country's outline and testing
a polygon per sample, to save a request the tile cache already remembers.

**``native_resolution_m`` is not ``cell_size_m``, and conflating them is a
false claim about accuracy.** swissALTI3D is surveyed at 2 m; Snowdesk
resamples it onto a 5 m grid. The first says how finely the ground was
measured and belongs in provenance; the second says how finely we store it
and is what a slope kernel steps across. A 5 m grid cannot recover 2 m
detail, and a 2 m source does not make our 5 m answer a 2 m answer.

**Why a tier rule exists for one source.** SNOW-693 adds coverage outside
Switzerland from coarser national models. The rule below decides which
answer wins where two rectangles overlap, and it is written and tested now
so that ticket is an addition to ``sources[]`` rather than a rebuild of
everything that reads it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TerrainQuality(StrEnum):
    """How the ground under a source was surveyed, best tier first.

    Ordering is by measurement method rather than by stated resolution: a
    coarse laser scan knows where the ground is under the trees and a fine
    photogrammetric one does not, so the tier decides first and the
    resolution only breaks a tie inside it.
    """

    LIDAR = "lidar"
    PHOTOGRAMMETRIC = "photogrammetric"
    RADAR = "radar"

    @property
    def rank(self) -> int:
        """Return the tier's rank, where a higher number is a better survey.

        Returns:
            The rank, 3 for lidar down to 1 for radar.

        """
        return _QUALITY_RANK[self]

    @classmethod
    def parse(cls, value: str) -> TerrainQuality:
        """Return the tier named by a ``grid.json`` quality string.

        RAISES ON AN UNKNOWN TIER rather than falling back to a default.
        An unrecognised string means the tileset has grown a survey method
        this code has never ranked, and the ranking is what decides which
        source answers for a point — quietly treating it as the worst (or
        the best) tier would silently re-route every overlapping sample.

        Args:
            value: The ``quality`` field of a ``sources[]`` entry.

        Returns:
            The matching tier.

        Raises:
            ValueError: If ``value`` names no known tier.

        """
        try:
            return cls(value)
        except ValueError:
            known = ", ".join(tier.value for tier in cls)
            raise ValueError(
                f"unknown terrain quality tier {value!r} (known tiers: {known})"
            ) from None


# Kept beside the enum rather than inside it: a StrEnum member's value is
# its wire string, so the rank cannot live on the member itself.
_QUALITY_RANK: dict[TerrainQuality, int] = {
    TerrainQuality.LIDAR: 3,
    TerrainQuality.PHOTOGRAMMETRIC: 2,
    TerrainQuality.RADAR: 1,
}


@dataclass(frozen=True)
class TerrainSource:
    """One height source, with its provenance and its coverage rectangle.

    Attributes:
        id: Stable identifier, e.g. ``"swissalti3d"``.
        name: The product's own name.
        provider: The organisation that publishes it.
        native_resolution_m: The resolution it was SURVEYED at — see the
            module docstring; this is not the grid's cell size.
        quality: Its survey tier.
        attribution: The credit line a rendered answer must carry.
        licence: The licence's name.
        licence_url: Where the licence is published.
        source_url: Where the product itself is published.
        bbox: ``(min_easting, min_northing, max_easting, max_northing)`` in
            EPSG:3035 metres — a superset of the real coverage.
        tile_x_range: Inclusive ``(first, last)`` tile column it spans.
        tile_y_range: Inclusive ``(first, last)`` tile row it spans.

    """

    id: str
    name: str
    provider: str
    native_resolution_m: float
    quality: TerrainQuality
    attribution: str
    licence: str
    licence_url: str
    source_url: str
    bbox: tuple[float, float, float, float]
    tile_x_range: tuple[int, int]
    tile_y_range: tuple[int, int]

    def to_string(self) -> str:
        """Return a short human description of the source.

        Returns:
            The product name and its native resolution.

        """
        return f"{self.name} ({self.native_resolution_m:g} m, {self.quality})"

    def __str__(self) -> str:
        """Return ``to_string()``.

        Returns:
            The short human description.

        """
        return self.to_string()


def source_from_payload(entry: Mapping[str, Any]) -> TerrainSource:
    """Build a ``TerrainSource`` from one published ``sources[]`` entry.

    Deliberately strict. A missing key raises ``KeyError`` and an
    unrecognised tier raises ``ValueError``, both of which
    ``terrain_grid.load_grid`` turns into "no grid" — the tileset's
    definition changing shape under us is exactly the case where guessing
    produces plausible, wrong answers about somebody's slope.

    Args:
        entry: One object from ``grid.json``'s ``sources`` array.

    Returns:
        The parsed source.

    Raises:
        KeyError: If a required field is absent.
        ValueError: If the quality tier is unknown, or the coverage
            rectangle or either tile range is the wrong length.

    """
    coverage = entry["coverage"]
    bbox = tuple(float(value) for value in coverage["bbox"])
    if len(bbox) != 4:
        raise ValueError(f"coverage bbox must carry four numbers, got {len(bbox)}")

    # Length-checked for the same reason as the bbox, and separately from
    # it: indexing [1] on a one-element range raises IndexError, which is
    # NOT in the set load_grid catches, so it would escape the "never
    # raises for a data problem" contract as a 500 rather than an
    # UNAVAILABLE. A ValueError is the shape the caller already handles.
    tile_x = coverage["tile_x"]
    tile_y = coverage["tile_y"]
    for axis, published in (("tile_x", tile_x), ("tile_y", tile_y)):
        if len(published) != 2:
            raise ValueError(
                f"coverage {axis} must carry two numbers, got {len(published)}"
            )

    return TerrainSource(
        id=str(entry["id"]),
        name=str(entry["name"]),
        provider=str(entry["provider"]),
        native_resolution_m=float(entry["native_resolution_m"]),
        quality=TerrainQuality.parse(str(entry["quality"])),
        attribution=str(entry["attribution"]),
        licence=str(entry["licence"]),
        licence_url=str(entry["licence_url"]),
        source_url=str(entry["source_url"]),
        # ``bbox`` is a 4-tuple by the length check above; mypy cannot see
        # that through a generator expression.
        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
        tile_x_range=(int(tile_x[0]), int(tile_x[1])),
        tile_y_range=(int(tile_y[0]), int(tile_y[1])),
    )


def covers(source: TerrainSource, easting: float, northing: float) -> bool:
    """Return whether a projected point falls in a source's rectangle.

    Four comparisons against the bbox, and NOT a coverage test — see the
    module docstring. Inclusive on all four edges: the rectangle is
    provenance metadata rather than a grid cell, so the half-open rule that
    governs cell addressing does not apply, and a point on the edge is
    better sent to the origin than refused here.

    Args:
        source: The source to test.
        easting: EPSG:3035 easting in metres.
        northing: EPSG:3035 northing in metres.

    Returns:
        True when the point is inside the source's declared rectangle.

    """
    min_easting, min_northing, max_easting, max_northing = source.bbox
    return (
        min_easting <= easting <= max_easting
        and min_northing <= northing <= max_northing
    )


def select_source(
    sources: Iterable[TerrainSource],
    easting: float,
    northing: float,
) -> TerrainSource | None:
    """Return the best source claiming a point, or None if none does.

    The tier rule: the highest survey tier wins outright, and the finest
    ``native_resolution_m`` breaks a tie inside a tier. Ties beyond that are
    broken by ``id`` so the choice is stable across processes rather than
    dependent on the order the JSON happened to list them in.

    Args:
        sources: The sources to choose between.
        easting: EPSG:3035 easting in metres.
        northing: EPSG:3035 northing in metres.

    Returns:
        The winning source, or None when the point is outside every
        declared rectangle.

    """
    candidates: Sequence[TerrainSource] = [
        source for source in sources if covers(source, easting, northing)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda source: (
            -source.quality.rank,
            source.native_resolution_m,
            source.id,
        ),
    )
