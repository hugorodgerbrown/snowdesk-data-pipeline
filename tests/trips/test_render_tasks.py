"""
tests/trips/test_render_tasks.py — the trip's render rebuild task (SNOW-1400).

A stale trip is rebuilt; a current or gone one is a no-op.
"""

from __future__ import annotations

import pytest

from apps.routes.services.render_tasks import REBUILT
from apps.trips.services.render_tasks import rebuild_trip_render_task
from tests.factories import TripFactory
from track.render import is_current

pytestmark = pytest.mark.django_db


def test_a_stale_trip_is_rebuilt_once() -> None:
    """The second call finds it current and does nothing."""
    trip = TripFactory.create(track_render=None)
    assert rebuild_trip_render_task.call(trip.pk) == REBUILT
    trip.refresh_from_db()
    assert is_current(trip.track_render)
    assert rebuild_trip_render_task.call(trip.pk) is None


def test_a_gone_trip_is_a_no_op() -> None:
    """Deleted since it was enqueued."""
    assert rebuild_trip_render_task.call(999_999) is None
