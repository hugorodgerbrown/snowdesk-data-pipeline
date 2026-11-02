"""
tests/regions/services/test_geometry.py — RFC 7946 ring winding (SNOW-1375).

The map draws each region's edge band with a negative ``line-offset``, which
lands inside only for RFC 7946 winding: outer rings counter-clockwise, holes
clockwise. The stored fixtures are mixed, so ``rfc7946_rewind`` orients
every served geometry.
"""

from __future__ import annotations

import copy

from apps.regions.services.geometry import rfc7946_rewind, ring_area

# A unit square, both ways round, and a smaller square inside it.
CCW = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
CW = list(reversed(CCW))
HOLE_CCW = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75], [0.25, 0.25]]
HOLE_CW = list(reversed(HOLE_CCW))


def test_ring_area_sign() -> None:
    """Counter-clockwise is positive, clockwise negative."""
    assert ring_area(CCW) == 1.0
    assert ring_area(CW) == -1.0


def test_a_clockwise_outer_ring_is_reversed() -> None:
    """The common case in the fixtures."""
    out = rfc7946_rewind({"type": "Polygon", "coordinates": [CW]})
    assert out is not None
    assert ring_area(out["coordinates"][0]) > 0


def test_a_counter_clockwise_outer_ring_is_kept() -> None:
    """Already right: unchanged."""
    out = rfc7946_rewind({"type": "Polygon", "coordinates": [CCW]})
    assert out is not None
    assert out["coordinates"][0] == CCW


def test_holes_come_out_clockwise() -> None:
    """A hole wound either way ends clockwise."""
    for hole in (HOLE_CCW, HOLE_CW):
        out = rfc7946_rewind({"type": "Polygon", "coordinates": [CW, hole]})
        assert out is not None
        assert ring_area(out["coordinates"][0]) > 0
        assert ring_area(out["coordinates"][1]) < 0


def test_every_part_of_a_multipolygon() -> None:
    """Each polygon of a MultiPolygon is oriented on its own."""
    geometry = {"type": "MultiPolygon", "coordinates": [[CW], [CCW, HOLE_CCW]]}
    out = rfc7946_rewind(geometry)
    assert out is not None
    for polygon in out["coordinates"]:
        assert ring_area(polygon[0]) > 0
        for hole in polygon[1:]:
            assert ring_area(hole) < 0


def test_idempotent_and_does_not_mutate() -> None:
    """Twice is once, and the stored boundary is left as it was."""
    geometry = {"type": "Polygon", "coordinates": [CW, HOLE_CCW]}
    before = copy.deepcopy(geometry)
    once = rfc7946_rewind(geometry)
    assert rfc7946_rewind(once) == once
    assert geometry == before


def test_other_geometries_pass_through() -> None:
    """None and non-polygons come back as given."""
    point = {"type": "Point", "coordinates": [7.0, 46.0]}
    assert rfc7946_rewind(None) is None
    assert rfc7946_rewind(point) is point
