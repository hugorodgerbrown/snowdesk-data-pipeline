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
    Function-scoped fixture that seeds the navigable test dataset (via
    ``seed_test_dataset`` — ``loaddata eaws_CH`` + ``import_resorts
    --commit`` + ``seed_test_data --all --commit``) before each e2e test.  The seed happens inside
    ``django_db_blocker.unblock()`` after the ``transactional_db`` fixture has
    flushed the database (which it does at the start of every
    transaction-enabled test).  The canonical bulletin URL
    ``/ch-4115/martigny-verbier/2026-04-08/`` is present in the seeded data
    and is used by the share-button smoke test.

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
    script in ``test_offline_favourite_submit.py`` /
    ``test_offline_observation_submit.py``) because a real SW's own
    lifecycle timing used to be unreliable to assert on. SNOW-389's
    spike (``tests/e2e/_spike_results.py``) found the opposite once the SW
    is driven correctly (poll ``navigator.serviceWorker.controller?.state``
    rather than relying on Playwright's page/context network-interception
    hooks, which do not see a service worker's own script fetches at all) —
    these two fixtures are the ones that actually drive it.

``favourites_page`` (SNOW-414)
    A plain ``page`` + ``live_server`` combination (no ``pwa_page`` — the
    favourites map-surface tests don't drive the SW lifecycle) with a
    session cookie for a regular ``Account``.

``no_script_page`` (SNOW-616)
    The same, with ``java_script_enabled=False``. The project position is
    that JavaScript is progressive enhancement and core functionality must
    work without it; nothing tested that until this fixture, so a control
    that only appeared once a script had run was indistinguishable from one
    that worked without. It builds its own context because
    ``java_script_enabled`` is a context-creation argument and cannot be
    flipped on an existing one.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from playwright.sync_api import Browser, BrowserContext, Page
from pytest_django.live_server_helper import LiveServer

from apps.accounts.models import Account
from tests.factories import AccountFactory
from tests.seeding import seed_test_dataset

# Kept local (not imported from a test file) so this conftest has no
# dependency on any single test module's internals. Matches the DB name
# constant duplicated across test_offline_favourite_submit.py /
# test_offline_observation_submit.py.
_PWA_DB_NAME = "snowdesk-pwa-v1"

# Session backend used for magic-link / passkey logins — see
# apps/accounts/backends.py. Matches the pattern in
# tests/accounts/test_passkey_views.py's _make_session_client.
_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"


@pytest.fixture()
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Grant clipboard and notification permissions to the test context."""
    return {
        **browser_context_args,
        "permissions": ["clipboard-read", "clipboard-write", "notifications"],
    }


@pytest.fixture(autouse=True)
def _stub_elevation_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the Open-Meteo elevation lookup for every e2e test.

    Creating a ``Favourite`` runs ``apps.favourites.services.create_favourite``
    → ``fetch_elevation``, which makes a live ``requests.get`` to
    ``https://api.open-meteo.com/v1/elevation`` with a 30s timeout. The favourite-submit / drain tests replay that POST against
    the real ``live_server`` (deliberately un-mocked at the ``page.route``
    layer — ``page.route`` only intercepts *browser* requests, never the
    server's own outbound HTTP), so the round-trip is at the mercy of a
    third-party API: a slow or rate-limited Open-Meteo stalls the drain past
    the test's poll timeout, which is exactly the CI flake in SNOW-479 /
    test_favourites.

    ``live_server`` runs in-process, so patching the name in
    ``apps.favourites.services``'s namespace (where it's looked up at call
    time) is visible to the server thread — the same mechanism that makes
    ``override_flag`` work here. Patched autouse because no e2e test should
    depend on an external API; tests that never create a favourite are
    unaffected.
    """
    monkeypatch.setattr(
        "apps.favourites.services.fetch_elevation",
        lambda latitude, longitude, base_url=None: 1500.0,
    )


@pytest.fixture(autouse=True)
def _suppress_home_intro(request: pytest.FixtureRequest) -> None:
    """Start every e2e test with ``#home-intro`` already dismissed.

    SNOW-535 grew the first-run intro card to two paragraphs plus a CTA and
    two onward links. It is absolutely positioned top-left over ``#map`` and
    grows downward with no bottom bound, so at common viewport sizes it now
    covers map controls — the bottom-left (i) legend toggle, and at 375px
    the whole utility cluster (``#basemap-toggle``, ``#report-btn``,
    ``#favourite-add-btn``). Playwright then fails the click with "subtree
    intercepts pointer events".

    Nearly every e2e test on ``/`` is about the map, not the intro, and a
    returning visitor has long since dismissed the panel — so seeding the
    dismissed flag is both the realistic default and the one that keeps
    these tests testing what they claim to. Doing it here rather than
    per-test avoids sprinkling a dismissal through ~30 files and re-fixing
    the next one that trips over the panel.

    Tests whose subject *is* the intro opt out with
    ``@pytest.mark.shows_home_intro`` (see tests/e2e/test_home_intro_tour.py).
    Tests that want to exercise the dismissal interaction itself should still
    call ``_dismiss_home_intro``, which is a no-op once already hidden.

    The write is wrapped because init scripts also run on ``about:blank``,
    where the origin is opaque and ``localStorage`` access throws.
    """
    if "shows_home_intro" in request.keywords:
        return
    page = request.getfixturevalue("page")
    page.add_init_script(
        "try { localStorage.setItem('snowdesk.home.intro', 'dismissed'); } catch (_) {}"
    )


@pytest.fixture()
def _load_test_data(django_db_blocker: Any) -> None:
    """Seed the navigable test dataset before each e2e test.

    Builds the dataset via ``seed_test_dataset`` (``loaddata eaws_CH`` +
    ``import_resorts --commit`` +
    ``seed_test_data --all --commit``) — the factory path that replaced the old
    ``loaddata test_data`` fixture.

    Must be function-scoped because ``transactional_db`` (used implicitly
    by ``live_server``) calls ``flush`` at the start of every test, wiping
    any session-scoped pre-load.  Running at function scope, after
    ``transactional_db`` setup, ensures the rows are visible to the live
    server thread when the test body executes.
    """
    with django_db_blocker.unblock():
        seed_test_dataset()
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

    Same pattern as ``_delete_db`` in ``test_pwa_client_signals.py`` —
    duplicated here (rather than imported) so this conftest has no
    dependency on a particular test module.
    """
    page.evaluate(
        """(name) => new Promise((resolve) => {
            try {
              const req = indexedDB.deleteDatabase(name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              // No onblocked handler: it fires while the delete is still
              // pending (blocked by db.js's live, self-yielding connection),
              // not done — resolving there returns before the DB is gone. See
              // tests/js/test_db.js's deleteDb() for the full rationale.
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


def _wait_for_sw_control(page: Page, timeout: int = 5000) -> None:
    """Wait until ``navigator.serviceWorker.controller`` is activated.

    A registered service worker is not necessarily a *controlling* one —
    the controller reference only appears once the activated worker has
    actually claimed the page. Polling ``controller?.state`` (rather than
    Playwright's page/context network-interception hooks, which don't see
    a service worker's own fetches at all — see the module docstring)
    is the one reliable signal that the SW is ready to intercept fetches,
    so callers gate on this (SNOW-516) before doing anything that depends
    on the SW being in control, e.g. going offline.
    """
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=timeout,
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

    Two further guarantees make ``queue:events`` rows deterministic to
    assert on (SNOW-427 — the ``pwa.sw.installed`` CI flake):

    1. **The buffer never drains.** ``queue:events`` is a drain-by-design
       buffer: ``telemetry.js`` POSTs it to ``/api/telemetry`` on
       ``pagehide`` (every reload!), on a 30s interval, and past a depth
       threshold, deleting the rows on any 2xx — and the receiver returns
       204 even with no PostHog key configured. An init-script stub makes
       ``window.fetch`` resolve with a synthetic 503 for that endpoint on
       every page load of this fixture; ``telemetry.js``'s 5xx branch
       leaves the rows in place for the next (equally stubbed) retry. A
       resolved 503 rather than a rejection because ``pwa_offline.js``
       wraps ``window.fetch`` too and repaints the header connectivity
       symbol as OFFLINE whenever a fetch REJECTS — a rejecting stub would
       strike the symbol through the moment telemetry.js's ``online``-event
       flush fires, breaking the offline tests that wait on its state. The stub is JS-level, so it also covers the ``pagehide``
       flush's ``keepalive`` fetch, which Playwright's ``page.route``
       cannot see. ``navigator.sendBeacon`` (the critical-event fast
       path) is not ``fetch`` and is unaffected.

    2. **The SW lifecycle rows land before the reload.** ``pwa.sw.installed``
       and ``pwa.sw.activated`` reach ``queue:events`` via an async chain
       (SW ``postMessage`` → page message task → ``emit()``'s IndexedDB
       reads/writes) that is still settling when the controller's state
       first reads ``activated`` — reloading at that instant can tear the
       page down mid-chain, and since the browser fires ``install`` /
       ``activate`` once per SW instance the rows are then lost for good.
       The fixture polls for both rows before reloading, inside ONE
       ``evaluate`` call per the one-round-trip rule (see
       ``test_pwa_push_journey.py``'s module docstring).

    Yields:
        A ``PwaPage`` bundling the page with the lifecycle-assertion
        helpers.

    """
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.add_init_script("Math.random = () => 0;")
    # Guarantee 1 above — answer the telemetry drain POST with a synthetic
    # 503 so queue:events rows stay observable for the whole test.
    page.add_init_script(
        """(() => {
            const realFetch = window.fetch.bind(window);
            window.fetch = (input, init) => {
              const url =
                typeof input === 'string' ? input : (input && input.url) || '';
              if (url.includes('/api/telemetry')) {
                return Promise.resolve(
                  new Response('', {
                    status: 503,
                    statusText: 'pwa_page fixture: telemetry drain blocked',
                  }),
                );
              }
              return realFetch(input, init);
            };
          })();"""
    )
    page.goto(live_server.url + "/")
    page.wait_for_load_state("load")
    _wait_for_sw_control(page)
    # Guarantee 2 above — both SW lifecycle rows must be in queue:events
    # before the reload can be allowed to happen.
    telemetry_state = page.evaluate(
        """async ({ timeoutMs }) => {
            const wanted = ['pwa.sw.installed', 'pwa.sw.activated'];
            const deadline = Date.now() + timeoutMs;
            let seen = [];
            while (Date.now() < deadline) {
              const rows = await window.pwaDb.getAll('queue:events');
              seen = rows.map((r) => r.event);
              if (wanted.every((w) => seen.includes(w))) {
                return { ok: true, seen };
              }
              await new Promise((r) => setTimeout(r, 100));
            }
            return { ok: false, seen };
          }""",
        {"timeoutMs": 15_000},
    )
    assert telemetry_state["ok"], (
        "pwa.sw.installed / pwa.sw.activated did not both reach queue:events "
        f"before the SW-controlled reload; events present: "
        f"{telemetry_state['seen']!r}"
    )
    # Second, SW-controlled load — see the docstring above for why this is
    # needed to make "Scenario P1 completed" actually true.
    page.reload()
    page.wait_for_load_state("load")
    # Re-confirm control after the reload (SNOW-516) rather than assuming
    # the first wait's guarantee still holds post-reload.
    _wait_for_sw_control(page)
    yield PwaPage(page=page, live_server_url=live_server.url, page_errors=page_errors)
    assert page_errors == [], f"JS errors during the test: {page_errors}"
    _unregister_service_workers(page)
    _delete_pwa_db(page)


def _session_login(context: BrowserContext, live_server_url: str, user: User) -> None:
    """Add a valid Django session cookie for ``user`` to the browser context.

    Builds the session via Django's test ``Client.force_login()`` — the
    same mechanism ``tests/accounts/test_passkey_views.py``'s
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
class FavouritesPage:
    """A plain (no real-SW) ``Page`` authenticated as a regular Account.

    Unlike ``signed_in_page``, this does not go through ``pwa_page`` — the
    favourites map-surface tests don't need a real service-worker
    lifecycle, and skipping it keeps the fixture cheap.
    """

    page: Page
    live_server_url: str
    account: Account


@pytest.fixture()
def favourites_page(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> FavouritesPage:
    """A live-server ``page``, navigated nowhere yet, with an account session.

    Tests using this fixture are responsible for their own ``page.goto()``
    (mirroring ``tests/e2e/test_home_ribbon.py``'s ``_navigate_home``
    convention) since the session cookie must be added to the browser
    context before the first navigation to the live-server origin.

    Args:
        live_server: The live Django server.
        page: The Playwright page (real SW registration untouched — this
            fixture doesn't disable it, but favourites tests don't drive
            the SW lifecycle either way).
        django_db_blocker: pytest-django's DB-access guard, unblocked here
            to create the ``Account``.

    Returns:
        A ``FavouritesPage`` bundling the page, server URL, and account.

    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()
    _session_login(page.context, live_server.url, account.user)
    return FavouritesPage(page=page, live_server_url=live_server.url, account=account)


@dataclass
class NoScriptPage:
    """A ``Page`` with JavaScript disabled, authenticated as a regular Account.

    SNOW-616: the project position is that JavaScript is progressive
    enhancement and core functionality must work without it. Nothing tested
    that until this fixture — every other e2e page runs with scripts on, so
    a control that only exists once a script has run looked identical to one
    that does not.

    The context is built by hand rather than through the ``page`` fixture,
    because ``java_script_enabled`` is a context-creation argument: it
    cannot be flipped on a context that already exists.
    """

    page: Page
    live_server_url: str
    account: Account


@pytest.fixture()
def no_script_page(
    browser: Browser,
    browser_context_args: dict[str, Any],
    live_server: LiveServer,
    django_db_blocker: Any,
) -> Iterator[NoScriptPage]:
    """A signed-in page with ``java_script_enabled=False``.

    Navigated nowhere yet — the session cookie has to be added to the
    context before the first navigation to the live-server origin, same
    convention as ``favourites_page``.

    Args:
        browser: The Playwright browser to open a fresh context on.
        browser_context_args: The project's own context defaults, so this
            page differs from every other one in exactly one respect.
        live_server: The live Django server.
        django_db_blocker: pytest-django's DB-access guard, unblocked here
            to create the ``Account``.

    Yields:
        A ``NoScriptPage`` bundling the page, server URL, and account.

    """
    context = browser.new_context(
        **{**browser_context_args, "java_script_enabled": False}
    )
    with django_db_blocker.unblock():
        account = AccountFactory.create()
    _session_login(context, live_server.url, account.user)
    page = context.new_page()
    try:
        yield NoScriptPage(page=page, live_server_url=live_server.url, account=account)
    finally:
        context.close()


def _dismiss_home_intro(page: Page) -> None:
    """Dismiss the ``#home-intro`` overlay via its "×", if it is showing.

    SNOW-535 grew ``#home-intro``'s copy to two paragraphs plus two onward
    links, tall enough at common desktop viewport sizes to cover other map
    controls (e.g. the bottom-left (i) legend toggle — the regression this
    guards against). Any test that navigates to ``/`` and then clicks a map
    control should call this first, mirroring what a real visitor does
    before using the map.

    Uses the "×" (``#home-intro-close``) rather than the "Explore the map"
    CTA (``#home-intro-dismiss``) deliberately: the CTA also opens the
    map-help coachmark tour (SNOW-535), a side effect most callers of this
    helper don't want.

    A no-op if ``#home-intro`` is absent or already hidden (e.g.
    ``show_intro=False``, or a prior dismissal in the same test persisted
    via localStorage) — most callers don't need to know or care which.
    """
    intro = page.locator("#home-intro")
    if intro.count() and intro.is_visible():
        page.locator("#home-intro-close").click()
