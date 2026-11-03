"""
tests/locations/conftest.py — fixtures shared by the locations tests.

The what3words and elevation tasks open a breaker on their service when a
call fails (SNOW-1398). The mark lives in the cache, which outlives a
test, so it is closed around every test here: one test's failed lookup
must not make the next test's tasks skip.
"""

from collections.abc import Iterator

import pytest

from apps.core.command_tasks import clear_back_off
from apps.locations.services.elevation import OPEN_METEO
from apps.locations.services.what3words import WHAT3WORDS


@pytest.fixture(autouse=True)
def _closed_breakers() -> Iterator[None]:
    """Close both services' breakers before and after each test."""
    for service in (WHAT3WORDS, OPEN_METEO):
        clear_back_off(service)
    yield
    for service in (WHAT3WORDS, OPEN_METEO):
        clear_back_off(service)
