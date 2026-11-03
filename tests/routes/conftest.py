"""
tests/routes/conftest.py — fixtures shared by the routes tests.

The SAC tour render task opens a breaker on the tile origin when a walk
goes unanswered (SNOW-1397). The mark lives in the cache, which outlives a
test, so it is closed around every test here: one test's unanswered tour
must not make the next test's tasks skip.
"""

from collections.abc import Iterator

import pytest

from apps.core.command_tasks import clear_back_off
from apps.routes.services.ski_tour_render import TILE_ORIGIN


@pytest.fixture(autouse=True)
def _closed_tile_breaker() -> Iterator[None]:
    """Close the tile origin's breaker before and after each test."""
    clear_back_off(TILE_ORIGIN)
    yield
    clear_back_off(TILE_ORIGIN)
