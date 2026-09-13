"""
tests/locations/services/test_terrain_sources.py — the source registry.

Covers:
  - source_from_payload: the committed grid.json's one real entry, and the
    strictness that stops a changed definition being half-read;
  - TerrainQuality: the tier ranking, and an unknown tier raising rather
    than being silently accepted;
  - covers: the published bbox, in and out, and its edges;
  - select_source: the tier rule against a synthetic second source — the
    rule is what is under test, not the number of sources published today;
  - native_resolution_m (2 m) is reported separately from the grid's
    cell_size_m (5 m) and is never conflated with it.

No HTTP, no database: the registry is a parser and three comparisons.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.locations.services.terrain_grid import grid_from_payload
from apps.locations.services.terrain_sources import (
    TerrainQuality,
    TerrainSource,
    covers,
    select_source,
    source_from_payload,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "terrain"

# The published swissALTI3D rectangle, EPSG:3035 metres.
SWISS_BBOX = (4007680.0, 2512640.0, 4359680.0, 2745600.0)

# Zermatt village projected — inside the rectangle.
INSIDE = (4146515.586, 2547946.114)
# Somewhere over the Atlantic — outside it by a long way.
OUTSIDE = (3000000.0, 2000000.0)


def _payload() -> dict[str, Any]:
    """Return the committed grid.json, parsed.

    Returns:
        The published definition as a dict.

    """
    payload: dict[str, Any] = json.loads((FIXTURES / "grid.json").read_text())
    return payload


def _source_payload() -> dict[str, Any]:
    """Return the committed definition's one sources[] entry.

    Returns:
        The swissALTI3D entry.

    """
    entry: dict[str, Any] = _payload()["sources"][0]
    return entry


def _synthetic(
    identifier: str,
    quality: TerrainQuality,
    native_resolution_m: float,
    bbox: tuple[float, float, float, float] = SWISS_BBOX,
) -> TerrainSource:
    """Build a source for the tier rule to choose between.

    Args:
        identifier: The source's id.
        quality: Its survey tier.
        native_resolution_m: The resolution it was surveyed at.
        bbox: Its coverage rectangle. Defaults to the Swiss one.

    Returns:
        The source.

    """
    return TerrainSource(
        id=identifier,
        name=identifier,
        provider="Test",
        native_resolution_m=native_resolution_m,
        quality=quality,
        attribution="© test",
        licence="test",
        licence_url="https://example.invalid/licence",
        source_url="https://example.invalid/",
        bbox=bbox,
        tile_x_range=(0, 1),
        tile_y_range=(0, 1),
    )


class TestSourceFromPayload:
    """Parsing one published sources[] entry."""

    def test_parses_the_swissalti3d_entry(self) -> None:
        """Provenance arrives whole, ready to print beside an answer."""
        source = source_from_payload(_source_payload())
        assert source.id == "swissalti3d"
        assert source.name == "swissALTI3D"
        assert source.provider == "Federal Office of Topography swisstopo"
        assert source.quality is TerrainQuality.LIDAR
        assert source.attribution == "© swisstopo"
        assert source.licence == "swisstopo free geodata (OGD)"
        assert source.bbox == SWISS_BBOX
        assert source.tile_x_range == (3131, 3405)
        assert source.tile_y_range == (1963, 2144)

    def test_native_resolution_is_not_the_grid_cell_size(self) -> None:
        """2 m surveyed, 5 m stored — two different claims, kept apart."""
        grid = grid_from_payload(_payload())
        source = grid.sources[0]
        assert source.native_resolution_m == 2.0
        assert grid.cell_size_m == 5.0

    def test_an_unknown_quality_tier_raises(self) -> None:
        """A survey method nothing has ranked must not be ranked by guess."""
        entry = _source_payload()
        entry["quality"] = "vibes"
        with pytest.raises(ValueError, match="unknown terrain quality tier"):
            source_from_payload(entry)

    def test_a_missing_field_raises(self) -> None:
        """Half an entry is not a source."""
        entry = _source_payload()
        del entry["licence_url"]
        with pytest.raises(KeyError):
            source_from_payload(entry)

    def test_a_short_bbox_raises(self) -> None:
        """A rectangle needs four numbers, and three is not three of them."""
        entry = _source_payload()
        entry["coverage"]["bbox"] = [1, 2, 3]
        with pytest.raises(ValueError, match="four numbers"):
            source_from_payload(entry)

    def test_to_string_names_the_product_and_its_resolution(self) -> None:
        """The human description carries both halves of the provenance."""
        source = source_from_payload(_source_payload())
        assert str(source) == "swissALTI3D (2 m, lidar)"


class TestQualityTiers:
    """The survey tier ranking."""

    def test_lidar_outranks_the_coarser_tiers(self) -> None:
        """The ordering is real, not notional."""
        assert TerrainQuality.LIDAR.rank > TerrainQuality.PHOTOGRAMMETRIC.rank
        assert TerrainQuality.PHOTOGRAMMETRIC.rank > TerrainQuality.RADAR.rank

    def test_parse_accepts_a_published_tier(self) -> None:
        """The wire string round-trips to its member."""
        assert TerrainQuality.parse("lidar") is TerrainQuality.LIDAR

    def test_parse_names_the_known_tiers_when_it_raises(self) -> None:
        """Whoever hits this needs to know what the tileset may say."""
        with pytest.raises(ValueError, match="known tiers: lidar"):
            TerrainQuality.parse("guesswork")


class TestCovers:
    """The bbox containment predicate."""

    def test_a_point_inside_the_rectangle(self) -> None:
        """Zermatt is claimed by swissALTI3D."""
        source = source_from_payload(_source_payload())
        assert covers(source, *INSIDE) is True

    def test_a_point_outside_the_rectangle(self) -> None:
        """Ground no source claims is refused without a request."""
        source = source_from_payload(_source_payload())
        assert covers(source, *OUTSIDE) is False

    def test_the_edges_are_inclusive(self) -> None:
        """A point on the rectangle is better asked about than refused."""
        source = source_from_payload(_source_payload())
        assert covers(source, SWISS_BBOX[0], SWISS_BBOX[1]) is True
        assert covers(source, SWISS_BBOX[2], SWISS_BBOX[3]) is True

    def test_just_outside_an_edge(self) -> None:
        """One metre past the corner is past it."""
        source = source_from_payload(_source_payload())
        assert covers(source, SWISS_BBOX[2] + 1, SWISS_BBOX[3]) is False
        assert covers(source, SWISS_BBOX[0], SWISS_BBOX[1] - 1) is False


class TestSelectSource:
    """Which source answers when more than one claims a point."""

    def test_the_only_source_wins(self) -> None:
        """The published case today."""
        source = source_from_payload(_source_payload())
        assert select_source([source], *INSIDE) is source

    def test_the_higher_tier_wins_over_the_finer_resolution(self) -> None:
        """A coarse laser scan beats a fine photograph of the treetops."""
        lidar = _synthetic("lidar-5m", TerrainQuality.LIDAR, 5.0)
        photo = _synthetic("photo-1m", TerrainQuality.PHOTOGRAMMETRIC, 1.0)
        assert select_source([photo, lidar], *INSIDE) is lidar

    def test_the_finer_resolution_breaks_a_tie_within_a_tier(self) -> None:
        """Inside one tier, more measurements win."""
        fine = _synthetic("lidar-2m", TerrainQuality.LIDAR, 2.0)
        coarse = _synthetic("lidar-10m", TerrainQuality.LIDAR, 10.0)
        assert select_source([coarse, fine], *INSIDE) is fine

    def test_a_point_covered_by_neither_selects_nothing(self) -> None:
        """Outside every rectangle there is no source to name."""
        lidar = _synthetic("lidar-5m", TerrainQuality.LIDAR, 5.0)
        photo = _synthetic("photo-1m", TerrainQuality.PHOTOGRAMMETRIC, 1.0)
        assert select_source([photo, lidar], *OUTSIDE) is None

    def test_only_the_source_claiming_the_point_is_considered(self) -> None:
        """A better source elsewhere does not answer for ground it lacks."""
        far = _synthetic(
            "lidar-elsewhere",
            TerrainQuality.LIDAR,
            1.0,
            bbox=(0.0, 0.0, 1000.0, 1000.0),
        )
        here = _synthetic("radar-here", TerrainQuality.RADAR, 30.0)
        assert select_source([far, here], *INSIDE) is here

    def test_the_choice_is_stable_when_everything_ties(self) -> None:
        """Two identical sources resolve by id, not by list order."""
        first = _synthetic("aaa", TerrainQuality.LIDAR, 2.0)
        second = _synthetic("bbb", TerrainQuality.LIDAR, 2.0)
        assert select_source([second, first], *INSIDE) is first
        assert select_source([first, second], *INSIDE) is first

    def test_no_sources_at_all(self) -> None:
        """An empty registry claims nothing."""
        assert select_source([], *INSIDE) is None
