"""
tests/regions/migrations/test_0019_close_region_boundary_rings.py

Covers the SNOW-115 data migration that closes open polygon rings on
``MicroRegion.boundary``. Two layers of coverage:

1. Pure-Python tests of the ``_close_rings`` helper (Polygon,
   MultiPolygon, idempotency, non-geometry passthrough).
2. A Django-DB integration test that runs the forward function against
   real ``MicroRegion`` rows and asserts only the open-ring rows are
   rewritten.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest
from django.apps import apps as django_apps

from regions.models import MicroRegion
from tests.factories import MicroRegionFactory

# The migration module name starts with a digit so it cannot be imported
# with ``from … import`` — go through ``importlib`` instead.
_MIG = import_module("pipeline.migrations.0019_close_region_boundary_rings")
_close_rings = _MIG._close_rings
close_open_rings = _MIG.close_open_rings


def _polygon(closed: bool) -> dict[str, Any]:
    """Return a small valid Polygon, with the ring closed iff ``closed``."""
    base = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    ring = [*base, base[0]] if closed else base
    return {"type": "Polygon", "coordinates": [ring]}


# ---------------------------------------------------------------------------
# Pure-Python helper tests
# ---------------------------------------------------------------------------


class TestCloseRings:
    """Unit tests for the ``_close_rings`` helper."""

    def test_open_polygon_ring_is_closed(self) -> None:
        """An open Polygon ring gets its first vertex appended."""
        new, changed = _close_rings(_polygon(closed=False))
        assert changed is True
        ring = new["coordinates"][0]
        assert ring[0] == ring[-1] == [0.0, 0.0]
        assert len(ring) == 5  # 4 original + 1 closing

    def test_closed_polygon_is_unchanged(self) -> None:
        """An already-closed ring is returned untouched (idempotent)."""
        original = _polygon(closed=True)
        new, changed = _close_rings(original)
        assert changed is False
        assert new is original

    def test_multipolygon_closes_every_subring(self) -> None:
        """Every ring across every sub-polygon of a MultiPolygon is closed."""
        open_ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        closed_ring = [[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 2.0]]
        boundary: dict[str, Any] = {
            "type": "MultiPolygon",
            "coordinates": [[open_ring], [closed_ring]],
        }
        new, changed = _close_rings(boundary)
        assert changed is True
        first, second = new["coordinates"]
        assert first[0][0] == first[0][-1] == [0.0, 0.0]
        assert second[0] == closed_ring  # untouched

    def test_non_polygon_input_is_returned_unchanged(self) -> None:
        """Point / LineString / unknown types pass through untouched."""
        point: dict[str, Any] = {"type": "Point", "coordinates": [7.5, 46.8]}
        new, changed = _close_rings(point)
        assert changed is False
        assert new is point


# ---------------------------------------------------------------------------
# Migration integration test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCloseOpenRingsMigration:
    """Tests for the ``close_open_rings`` data-migration function."""

    def test_open_rings_are_closed_idempotently(self) -> None:
        """Open rows close, closed rows untouched, second run is a no-op."""
        open_region = MicroRegionFactory.create(
            region_id="CH-9001", boundary=_polygon(closed=False)
        )
        closed_region = MicroRegionFactory.create(
            region_id="CH-9002", boundary=_polygon(closed=True)
        )
        null_region = MicroRegionFactory.create(region_id="CH-9003", boundary=None)

        close_open_rings(django_apps, schema_editor=None)

        open_region.refresh_from_db()
        closed_region.refresh_from_db()
        null_region.refresh_from_db()

        # The previously-open ring is now closed.
        assert open_region.boundary is not None
        new_ring = open_region.boundary["coordinates"][0]
        assert new_ring[0] == new_ring[-1]
        assert len(new_ring) == 5

        # The closed ring and the null boundary are untouched.
        assert closed_region.boundary == _polygon(closed=True)
        assert null_region.boundary is None

        # Second run is a true no-op — the JSON value is byte-identical.
        snapshot = MicroRegion.objects.get(pk=open_region.pk).boundary
        close_open_rings(django_apps, schema_editor=None)
        assert MicroRegion.objects.get(pk=open_region.pk).boundary == snapshot
