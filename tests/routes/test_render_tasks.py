"""
tests/routes/test_render_tasks.py — the render rebuild's row tasks (SNOW-1400).

Each task re-reads its row through ``stale_q``, so a row gone or already
current is a no-op; a stale one is rebuilt; a walked tour whose record no
longer matches its line is reported unwalked and left without a render.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.routes.services.render_tasks import (
    REBUILT,
    UNWALKED,
    rebuild_route_render_task,
    rebuild_tour_render_task,
)
from tests.factories import RouteFactory, SkiTourFactory
from track.render import is_current

pytestmark = pytest.mark.django_db

_KEPT: dict[str, Any] = {"v": 1, "segments": [], "walked_reversed": False}


def test_a_stale_route_is_rebuilt() -> None:
    """The stored render becomes current."""
    route = RouteFactory.create(track_render={"v": 0})
    assert rebuild_route_render_task.call(route.pk) == REBUILT
    route.refresh_from_db()
    assert is_current(route.track_render)


def test_a_current_route_is_a_no_op() -> None:
    """A duplicate enqueue, or a route another writer reached first."""
    route = RouteFactory.create()
    assert rebuild_route_render_task.call(route.pk) is None


def test_a_gone_route_is_a_no_op() -> None:
    """Deleted since it was enqueued."""
    assert rebuild_route_render_task.call(999_999) is None


def test_a_walked_tour_is_rebuilt() -> None:
    """A tour holding a record for its line gets its render."""
    tour = SkiTourFactory.create(slope_samples=_KEPT)
    assert rebuild_tour_render_task.call(tour.pk) == REBUILT
    tour.refresh_from_db()
    assert is_current(tour.track_render)


def test_a_tour_held_in_the_other_order_is_unwalked() -> None:
    """Nothing to build from; no render, no walk."""
    tour = SkiTourFactory.create(slope_samples={**_KEPT, "walked_reversed": True})
    assert rebuild_tour_render_task.call(tour.pk) == UNWALKED
    tour.refresh_from_db()
    assert tour.track_render is None


def test_an_unwalked_tour_is_a_no_op() -> None:
    """A tour with no record is not a candidate at all."""
    tour = SkiTourFactory.create()
    assert rebuild_tour_render_task.call(tour.pk) is None
