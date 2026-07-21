"""
tests/e2e/test_sync_log_panel.py — Playwright tests for the SNOW-482
manage-page sync-log panel.

Uses ``signed_in_page`` (``tests/e2e/conftest.py``) — a real-SW,
authenticated-subscriber page — since ``static/js/sync_log.js`` reads
``window.pwaDb.getSyncLog()``, which needs the real IndexedDB database
``static/js/db.js`` opens on page load.

Behind the ``sync_log`` waffle flag, matching ``accounts.views.manage_view``
and ``accounts/templates/accounts/manage.html``.

Every test clears ``log:sync`` after the first navigation and before
asserting on it: ``signed_in_page`` (via ``pwa_page``) navigates to ``/``
— the map homepage — twice during SW install/activation, and the map's
own JS fetches ``/api/ratings/`` on load, which is itself a legitimate
qualifying sync and lands real rows in ``log:sync`` before this file's
own test body ever runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from waffle.testutils import override_flag

from tests.e2e.conftest import SignedInPage

MANAGE_URL_PATH = "/account/manage/"


@override_flag("sync_log", active=False)
@pytest.mark.django_db(transaction=True)
def test_panel_absent_when_flag_inactive(signed_in_page: SignedInPage) -> None:
    """The sync-log panel is entirely absent when the flag is off."""
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    assert page.locator('[data-testid="sync-log-panel"]').count() == 0


@override_flag("sync_log", active=True)
@pytest.mark.django_db(transaction=True)
def test_panel_shows_no_syncs_yet_before_any_sync(
    signed_in_page: SignedInPage,
) -> None:
    """With an empty log:sync store, the panel shows the "No syncs yet" placeholder."""
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    # Clear rows the fixture's own earlier map-page navigations left
    # behind (see module docstring), then reload so sync_log.js repaints
    # from a genuinely empty store.
    page.evaluate("async () => window.pwaDb.clear('log:sync')")
    page.reload()
    page.wait_for_load_state("load")

    # The panel is a collapsed <details> (includes/_collapsible_panel.html)
    # — its content isn't "visible" to Playwright until opened, even
    # though sync_log.js has already painted it.
    page.wait_for_selector('[data-testid="sync-log-panel"]', state="attached")
    page.click('[data-testid="sync-log-panel"] summary')
    page.wait_for_function(
        """() => {
            const el = document.querySelector('[data-role="sync-log-placeholder"]');
            return !!el && el.textContent.trim().length > 0;
          }"""
    )
    placeholder_text = page.eval_on_selector(
        '[data-role="sync-log-placeholder"]', "(el) => el.textContent"
    )
    assert "No syncs yet" in placeholder_text
    assert page.locator('[data-testid="sync-log-row"]').count() == 0


@override_flag("sync_log", active=True)
@pytest.mark.django_db(transaction=True)
def test_panel_paints_seeded_rows_newest_first(
    signed_in_page: SignedInPage, django_db_blocker: Any
) -> None:
    """Rows written to log:sync (e.g. by a prior session) paint on load, newest first."""
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    # Clear rows the fixture's own earlier map-page navigations left
    # behind (see module docstring), seed exactly the two rows this test
    # cares about, then reload so sync_log.js's on-load read (bound to
    # DOMContentLoaded — see its header comment) picks them up.
    page.evaluate(
        """async () => {
            await window.pwaDb.clear('log:sync');
            await window.pwaDb.appendSyncLog({ at: '2026-04-08T09:00:00.000Z', path: '/api/ratings/' });
            await window.pwaDb.appendSyncLog({ at: '2026-04-08T09:05:00.000Z', path: '/api/version' });
          }"""
    )
    page.reload()
    page.wait_for_load_state("load")

    # Open the collapsed <details> panel — its content is painted
    # regardless, but not "visible" to Playwright until opened.
    page.click('[data-testid="sync-log-panel"] summary')
    page.wait_for_selector('[data-testid="sync-log-row"]')
    paths = page.eval_on_selector_all(
        '[data-testid="sync-log-row"]',
        "(rows) => rows.map((row) => row.textContent)",
    )
    assert len(paths) == 2
    # Newest first — /api/version (09:05) before /api/ratings/ (09:00).
    assert "/api/version" in paths[0]
    assert "/api/ratings/" in paths[1]
