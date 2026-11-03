"""
tests/weather/conftest.py — fixtures shared by the weather tests.

The weather commands enqueue one task per station (SNOW-1399), each taking
its turn on the shared ``open-meteo`` slot and opening its breaker on a
429. Both marks live in the cache, which outlives a test, so they are
cleared around every test here; and the commands' default pace is zeroed,
so a test that runs tasks inline measures the command, not ``time.sleep``.
"""

from collections.abc import Iterator

import pytest
from django.core.cache import cache

from apps.core.command_tasks import _SLOT_KEY, clear_back_off
from apps.locations.services.elevation import OPEN_METEO


def _clear() -> None:
    """Close the Open-Meteo breaker and forget its next slot."""
    clear_back_off(OPEN_METEO)
    cache.delete(_SLOT_KEY.format(OPEN_METEO))


@pytest.fixture(autouse=True)
def _fresh_open_meteo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with Open-Meteo unthrottled and the pace at zero."""
    monkeypatch.setattr(
        "apps.weather.management.commands.fetch_weather.DEFAULT_DELAY_S", 0.0
    )
    monkeypatch.setattr("apps.weather.services.backfill.INTER_LOCATION_DELAY", 0.0)
    _clear()
    yield
    _clear()
