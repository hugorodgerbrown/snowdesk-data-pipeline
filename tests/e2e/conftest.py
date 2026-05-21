"""
tests/e2e/conftest.py — Playwright fixtures for end-to-end browser tests.

Two fixtures are defined here:

``browser_context_args``
    Overrides the default Playwright browser-context arguments to grant
    ``clipboard-read`` and ``clipboard-write`` permissions.  Without this,
    ``navigator.clipboard.writeText()`` throws ``NotAllowedError`` in
    headless Chromium and the share-button fallback path appears broken.

``_load_test_data``
    Function-scoped fixture that loads ``bulletins/fixtures/test_data.json``
    via Django's ``loaddata`` command before each e2e test.  The load
    happens inside ``django_db_blocker.unblock()`` after the ``transactional_db``
    fixture has flushed the database (which it does at the start of every
    transaction-enabled test).  The canonical bulletin URL
    ``/ch-4115/martigny-verbier/2026-04-08/`` is pre-seeded in that
    fixture and is used by the share-button smoke test.

    Why function-scoped rather than session-scoped: pytest-django's
    ``transactional_db`` fixture (which ``live_server`` implicitly requests
    via ``_live_server_helper``) calls ``flush`` to clear the DB before each
    test.  Loading data at session start would be washed away by that flush.
    Loading it function-scoped, AFTER the flush, ensures the rows are present
    when the test body runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command


@pytest.fixture()
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Grant clipboard permissions to the headless Chromium context."""
    return {
        **browser_context_args,
        "permissions": ["clipboard-read", "clipboard-write"],
    }


@pytest.fixture()
def _load_test_data(django_db_blocker: Any) -> None:
    """Load the test_data fixture before each e2e test.

    Must be function-scoped because ``transactional_db`` (used implicitly
    by ``live_server``) calls ``flush`` at the start of every test, wiping
    any session-scoped pre-load.  Running at function scope, after
    ``transactional_db`` setup, ensures the rows are visible to the live
    server thread when the test body executes.
    """
    with django_db_blocker.unblock():
        call_command("loaddata", "test_data", verbosity=0)
