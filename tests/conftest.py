"""
tests/conftest.py — shared pytest fixtures.

The ``_force_sync_email`` autouse fixture forces
``settings.SUBSCRIPTIONS_EMAIL_ASYNC = False`` for every test so existing
locmem-backend ``mail.outbox`` assertions in test_email.py / test_views.py
keep working synchronously.  Tests that need to exercise the async
dispatch path opt back in via ``@override_settings(...)`` per-test or
per-class.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_sync_email(settings) -> None:
    """Force synchronous email dispatch in tests by default."""
    settings.SUBSCRIPTIONS_EMAIL_ASYNC = False
