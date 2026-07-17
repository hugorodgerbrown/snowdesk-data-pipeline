"""
tests/e2e/conftest.py — Playwright fixtures for end-to-end browser tests.

Fixtures defined here:

``browser_context_args``
    Overrides the default Playwright browser-context arguments to grant
    ``clipboard-read``, ``clipboard-write`` and ``notifications``
    permissions. Without the clipboard grants,
    ``navigator.clipboard.writeText()`` throws ``NotAllowedError`` in
    headless Chromium and the share-button fallback path appears broken.
    Without the ``notifications`` grant (SNOW-389),
    ``Notification.permission`` stays ``'default'`` and
    ``self.registration.showNotification()`` inside ``sw.js``'s ``push``
    handler rejects.

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

``pwa_page`` / ``signed_in_page`` (SNOW-389)
    Real-service-worker lifecycle fixtures — see their docstrings below.
    Every other fixture and test in this directory that touches
    ``navigator.serviceWorker`` disables or strips it (``_disable_real_sw``
    in ``test_pwa_client_signals.py``, the stripped-``serviceWorker`` init
    script in ``test_pwa_db.py`` / ``test_pwa_telemetry.py``) because a real
    SW's own lifecycle timing used to be unreliable to assert on. SNOW-389's
    spike (``tests/e2e/_spike_results.py``) found the opposite once the SW
    is driven correctly (poll ``navigator.serviceWorker.controller?.state``
    rather than relying on Playwright's page/context network-interception
    hooks, which do not see a service worker's own script fetches at all) —
    these two fixtures are the ones that actually drive it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from playwright.sync_api import BrowserContext, Page
from pytest_django.live_server_helper import LiveServer

from subscriptions.models import Subscriber
from tests.factories import SubscriberFactory

# Kept local (not imported from a test file) so this conftest has no
# dependency on any single test module's internals. Matches the DB name
# constant duplicated across test_pwa_db.py / test_pwa_telemetry.py.
_PWA_DB_NAME = "snowdesk-pwa-v1"

# Session backend used for magic-link / passkey logins — see
# subscriptions/backends.py. Matches the pattern in
# tests/subscriptions/test_passkey_views.py's _make_session_client.
_TOKEN_BACKEND = "subscriptions.backends.TokenBackend"


@pytest.fixture()
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Grant clipboard and notification permissions to the test context."""
    return {
        **browser_context_args,
        "permissions": ["clipboard-read", "clipboard-write", "notifications"],
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
    # The ratings API caches its payload server-side (cache.get_or_set keyed on
    # country/date). LocMemCache persists across tests in one process, so a
    # prior test that hit /api/ratings/ against the empty (pre-load) DB would
    # otherwise leave an empty payload cached and starve this test's ribbon.
    cache.clear()


# ---------------------------------------------------------------------------
# SNOW-389 — real service-worker lifecycle fixtures
# ---------------------------------------------------------------------------


def _delete_pwa_db(page: Page) -> None:
    """Delete the PWA IndexedDB so the next test starts from a clean slate.

    Same pattern as ``_delete_db`` in ``test_pwa_client_signals.py`` /
    ``test_pwa_db.py`` — duplicated here (rather than imported) so this
    conftest has no dependency on a particular test module.
    """
    page.evaluate(
        """(name) => new Promise((resolve) => {
            try {
              const req = indexedDB.deleteDatabase(name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              req.onblocked = () => resolve();
            } catch (_e) { resolve(); }
          })""",
        _PWA_DB_NAME,
    )


def _unregister_service_workers(page: Page) -> None:
    """Unregister every SW registration for the current origin, best-effort."""
    page.evaluate(
        """async () => {
            if (!('serviceWorker' in navigator)) return;
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map((r) => r.unregister().catch(() => {})));
          }"""
    )


@dataclass
class PwaPage:
    """A ``Page`` with a real, activated service worker plus lifecycle helpers.

    Bundles the raw Playwright ``Page`` with two invariant-checking helpers
    every SNOW-389 lifecycle test needs:

    ``wait_for_event``
        Poll ``queue:events`` in IndexedDB for a telemetry emit and return
        its row once found.

    ``assert_sw_absent``
        The "never stuck / adrift / abandoned" check — no SW registration,
        no ``snowdesk-shell-*`` caches, no ``snowdesk-pwa-v1`` IndexedDB.
        This is the central invariant SNOW-389 exists to enforce after a
        kill switch, a forced reset, or a min-version update.

    ``page_errors``
        Populated by the ``pwa_page`` fixture's ``pageerror`` listener
        (same technique as ``tests/e2e/test_share_button.py``) so every
        lifecycle test gets the template-tag-in-JS-comment / script-parse
        regression guard for free, without repeating the boilerplate in
        each test file. Tests that care can assert ``pwa_page.page_errors
        == []`` explicitly; ``pwa_page``'s own teardown asserts it too.
    """

    page: Page
    live_server_url: str
    page_errors: list[str]

    def wait_for_event(self, event_name: str, timeout: int = 5000) -> dict[str, Any]:
        """Poll ``queue:events`` until ``event_name`` appears; return the row.

        Args:
            event_name: The ``pwa.*`` event name to wait for.
            timeout: Milliseconds to poll before giving up.

        Returns:
            The matching row from ``queue:events``.

        Raises:
            playwright.sync_api.Error: If the event never lands within
                ``timeout`` milliseconds.

        """
        self.page.wait_for_function(
            """(name) => window.pwaDb.getAll('queue:events').then(
                 (rows) => rows.some((r) => r.event === name),
               )""",
            arg=event_name,
            timeout=timeout,
        )
        row = self.page.evaluate(
            """async (name) => {
                const rows = await window.pwaDb.getAll('queue:events');
                return rows.find((r) => r.event === name);
              }""",
            event_name,
        )
        assert row is not None, f"event {event_name!r} vanished after being found"
        return cast(dict[str, Any], row)

    def assert_sw_absent(self) -> None:
        """Assert no SW registration, no shell caches, no PWA IndexedDB survive."""
        state = self.page.evaluate(
            """async (dbName) => {
                const regs = await navigator.serviceWorker.getRegistrations();
                const cacheKeys = (await caches.keys()).filter(
                  (k) => k.startsWith('snowdesk-shell-'),
                );
                let hasDb = false;
                if ('databases' in indexedDB) {
                  const dbs = await indexedDB.databases();
                  hasDb = dbs.some((d) => d.name === dbName);
                }
                return { regCount: regs.length, cacheKeys, hasDb };
              }""",
            _PWA_DB_NAME,
        )
        assert state["regCount"] == 0, (
            f"expected no SW registrations, found {state['regCount']}"
        )
        assert state["cacheKeys"] == [], (
            f"expected no snowdesk-shell-* caches, found {state['cacheKeys']}"
        )
        assert state["hasDb"] is False, "expected snowdesk-pwa-v1 IDB to be absent"


@pytest.fixture()
def pwa_page(live_server: LiveServer, page: Page) -> Iterator[PwaPage]:
    """A ``Page`` navigated to ``/`` with the real SW registered and activated.

    Unlike every other fixture in this file, this one does NOT disable or
    strip ``navigator.serviceWorker`` — it is what SNOW-389's lifecycle
    tests use specifically to drive the real ``/sw.js``. Waits for
    first-install activation, then reloads once more: a fresh
    registration has no prior controller, so ``sw.js`` activates
    automatically without needing ``skipWaiting()`` (see the header
    comment in ``static/js/sw.js``) and its ``activate`` handler calls
    ``clients.claim()``, which hands control of the page to the new
    worker — but the very first navigation that triggered registration in
    the first place was never itself intercepted (the SW didn't exist yet
    when it was requested), so the shell's own HTML is NOT cached until a
    second, SW-controlled load happens. Every scenario in
    ``docs/testing-scenarios.md`` §"PWA Shell" after P1 lists "Scenario P1
    completed" as a precondition — this reload is what makes that true for
    every consuming test, not just the install test itself.

    Teardown unregisters any SW registration and deletes the
    ``snowdesk-pwa-v1`` IndexedDB so the next test starts clean — fixtures
    that disable the real SW don't need this (there is nothing to clean up);
    this one drives it, so it owns cleaning up after it.

    Stubs ``Math.random`` to always return 0 before the first navigation
    (same technique as the freshness / storage-eviction tests in
    ``test_pwa_client_signals.py``) so ``telemetry.js``'s sample-rate gate
    (``pwa.sw.installed`` / ``.activated`` / ``.update_available`` /
    ``.update_applied`` are sampled at 25% on repeat launches) always
    passes — without it, a test asserting on more than one of those events
    in the same page-load session would be flaky, since only the first
    ``pwa.sw.*`` emit per session is forced to 100% by the
    first-launch bump.

    Yields:
        A ``PwaPage`` bundling the page with the lifecycle-assertion
        helpers.

    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.add_init_script("Math.random = () => 0;")
    page.goto(live_server.url + "/")
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )
    # Second, SW-controlled load — see the docstring above for why this is
    # needed to make "Scenario P1 completed" actually true.
    page.reload()
    page.wait_for_load_state("load")
    yield PwaPage(page=page, live_server_url=live_server.url, page_errors=page_errors)
    assert page_errors == [], f"JS errors during the test: {page_errors}"
    _unregister_service_workers(page)
    _delete_pwa_db(page)


def _session_login(context: BrowserContext, live_server_url: str, user: User) -> None:
    """Add a valid Django session cookie for ``user`` to the browser context.

    Builds the session via Django's test ``Client.force_login()`` — the
    same mechanism ``tests/subscriptions/test_passkey_views.py``'s
    ``_make_session_client`` uses — rather than driving the magic-link
    email or WebAuthn passkey sign-in flow through the browser, which
    SNOW-389's lifecycle tests don't need to exercise. ``force_login()``
    writes the session key straight onto ``client.cookies``, which is then
    handed to Playwright as a real cookie for the live-server origin.

    Args:
        context: The Playwright browser context to add the cookie to.
        live_server_url: Origin the cookie is scoped to.
        user: The ``auth.User`` to authenticate as.

    """
    client = Client()
    client.force_login(user, backend=_TOKEN_BACKEND)
    session_cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_cookie.value,
                "url": live_server_url,
            }
        ]
    )


@dataclass
class SignedInPage(PwaPage):
    """A ``PwaPage`` authenticated as a freshly-created ``Subscriber``.

    Reused by the P12 manage-page journey (reset local data), which needs
    a signed-in session before ``/subscribe/manage/`` renders anything but
    a redirect to sign-in.
    """

    subscriber: Subscriber


@pytest.fixture()
def signed_in_page(pwa_page: PwaPage, django_db_blocker: Any) -> SignedInPage:
    """``pwa_page``, plus a Django session cookie for a fresh ``Subscriber``.

    Q5 (SNOW-389 plan step 1): the ``SubscriberFactory.create()`` +
    ``force_login`` + ``add_cookies`` round-trip measured well under the
    plan's 2s cutoff — see ``tests/e2e/_spike_results.py`` — so the full
    manage-page journey ships directly rather than splitting off a cheaper
    subset.

    Args:
        pwa_page: The real-SW page to attach the session cookie to.
        django_db_blocker: pytest-django's DB-access guard — ``pwa_page``
            does not itself request DB access, so this fixture must
            explicitly unblock it to create the ``Subscriber``.

    Returns:
        A ``SignedInPage`` — the same interface as ``PwaPage`` plus the
        authenticated ``subscriber``.

    """
    with django_db_blocker.unblock():
        subscriber = SubscriberFactory.create()
    _session_login(pwa_page.page.context, pwa_page.live_server_url, subscriber.user)
    return SignedInPage(
        page=pwa_page.page,
        live_server_url=pwa_page.live_server_url,
        page_errors=pwa_page.page_errors,
        subscriber=subscriber,
    )
