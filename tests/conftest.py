"""
tests/conftest.py — shared pytest fixtures.

The ``_force_sync_email`` autouse fixture forces
``settings.SUBSCRIPTIONS_EMAIL_ASYNC = False`` for every test so existing
locmem-backend ``mail.outbox`` assertions in test_email.py / test_views.py
keep working synchronously.  Tests that need to exercise the async
dispatch path opt back in via ``@override_settings(...)`` per-test or
per-class.

The ``_force_sync_weather_fetch`` autouse fixture forces
``settings.WEATHER_FETCH_ASYNC = False`` so weather fetch calls inside
``fetch_weather_async`` run synchronously on the main thread. This means:
  - DB assertions see the written snapshot immediately after the call.
  - The ``finally`` clause skips ``connections.close_all()`` (main-thread
    guard), so the test's transaction connection is not closed mid-test.
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
