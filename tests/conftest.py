"""
tests/conftest.py — shared pytest fixtures.

The ``_force_sync_email`` autouse fixture forces
``settings.SUBSCRIPTIONS_EMAIL_ASYNC = False`` for every test so existing
locmem-backend ``mail.outbox`` assertions in test_email.py / test_views.py
keep working synchronously.  Tests that need to exercise the async
dispatch path opt back in via ``@override_settings(...)`` per-test or
per-class.

The ``_force_sync_weather_fetch`` autouse fixture forces
``settings.WEATHER_FETCH_ASYNC = False`` so any direct call to
``fetch_weather_async`` in tests runs synchronously on the main thread
(see tests/bulletins/services/test_weather_fetcher.py::TestFetchWeatherAsync).
The ``finally`` clause inside the helper skips ``connections.close_all()``
on the main thread, so the test's transaction connection is preserved.

The ``_disable_inline_weather_warmup`` autouse fixture patches
``public.views.fetch_weather_async`` to a no-op so the implicit warmup
fired from ``bulletin_detail`` on past-date renders never touches the
network in the test environment. Without this, every test that renders a
past-date bulletin page (e.g. ``_freeze("2026-03-20")`` + ``date(2026, 3, 15)``)
hangs CI on a real ``requests.get`` to the Open-Meteo archive endpoint.
Tests that need to assert the warmup IS scheduled re-patch the same
attribute with a spy; ``monkeypatch.setattr`` is LIFO so the spy wins
over this autouse no-op. Tests that exercise the helper itself import it
from ``bulletins.services.weather_fetcher`` and are unaffected by the
``public.views`` patch.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_sync_email(settings) -> None:
    """Force synchronous email dispatch in tests by default."""
    settings.SUBSCRIPTIONS_EMAIL_ASYNC = False


@pytest.fixture(autouse=True)
def _force_sync_weather_fetch(settings) -> None:
    """Force synchronous weather fetch in tests by default (SNOW-164)."""
    settings.WEATHER_FETCH_ASYNC = False


@pytest.fixture(autouse=True)
def _disable_inline_weather_warmup(monkeypatch) -> None:
    """No-op ``public.views.fetch_weather_async`` for every test by default.

    Stops the implicit past-date warmup scheduled by ``bulletin_detail``
    from hitting Open-Meteo during the test run — without this, CI hangs
    on real network calls. Tests that need to verify the warmup is
    scheduled override this with their own ``monkeypatch.setattr`` (last
    setattr wins). Tests that drive ``fetch_weather_async`` directly
    import it from ``bulletins.services.weather_fetcher`` and bypass
    this patch entirely.
    """
    monkeypatch.setattr(
        "public.views.fetch_weather_async",
        lambda *args, **kwargs: None,
    )
