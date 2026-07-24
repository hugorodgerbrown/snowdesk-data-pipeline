"""
tests/e2e/test_offline_favourite_submit.py — Playwright test for SNOW-479:
offline favourite creation through the client mutation queue.

Covers the full offline → reconnect journey wiring
``favourites.views.favourite_create`` through ``window.pwaMutationQueue``
(``static/js/favourites.js`` / ``static/js/mutation_queue.js``, SNOW-376):

1. A "Save" tap while offline enqueues exactly one ``queue:mutations`` row
   and returns immediately (no network round-trip) — the optimistic
   confirmation renders straight away with the "will sync when you're back
   online" pending line visible, map.js paints a synthetic ``pending`` pin
   in the ``favourites`` source, the nav sync badge shows "1 change
   queued", and zero ``Favourite`` rows exist yet.
2. Reconnecting drains the queue against the REAL ``favourite_create`` view
   (not a mocked route) — exactly one ``Favourite`` row lands at the tapped
   coordinate, the successful drain re-dispatches
   ``snowdesk:favourites-changed``, and the authoritative (non-``pending``)
   pin replaces the optimistic one.
3. Replaying the same operation a second time — same Idempotency-Key, same
   body — does not create a duplicate row:
   ``core.idempotency.IdempotencyMiddleware`` dedupes it server-side.

SNOW-496: the cap-path case (queued create replays to a 409 at
``FAVOURITES_MAX_PER_USER``, which the queue treats as an immediate
permanent failure — toast + nav badge, no ``Favourite`` created, pending
pin dropped) is removed — the classification mechanics (any non-{408,429}
4xx, including 409, is 'permanent') are proven generically by
``tests/js/test_mutation_queue.js``'s permanent-4xx cases; this file keeps
the one real, user-observable offline→reconnect→real-server journey that
those synthetic-endpoint unit tests can't reach.

Uses the simulated-SW pattern (``navigator.serviceWorker`` stripped) — see
``docs/client-side-tests.md``'s "SW-lifecycle tests: real vs simulated" —
this test is about ``favourite_create`` plus the queue's real-server
integration, not the SW lifecycle itself. Placing the pin drives the
touch-friendly place-picker via ``MAP.setCenter`` (as
``tests/e2e/test_favourites.py`` does) rather than a canvas click, for
headless-WebGL determinism.

Polling note: every multi-step wait is driven by ``_poll`` (Python-side,
one ``page.evaluate`` round-trip at a time) rather than
``page.wait_for_function`` with an async body — see
``tests/js/test_mutation_queue.js``'s module docstring for the same xdist
parallel-run race between overlapping async predicate evaluations.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from favourites.models import Favourite
from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory

DB_NAME = "snowdesk-pwa-v1"


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map + queue.

    Mirrors ``tests/e2e/test_offline_observation_submit.py`` — stripping
    serviceWorker before any page script runs makes sw_register.js and
    mutation_queue.js's ``_registerBackgroundSync()`` bail out immediately.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _poll(
    page: Page,
    predicate_js: str,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> Any:
    """Poll ``page.evaluate(predicate_js)`` until it returns a truthy value."""
    deadline = time.monotonic() + timeout_s
    result = None
    while time.monotonic() < deadline:
        result = page.evaluate(predicate_js)
        if result:
            return result
        time.sleep(interval_s)
    raise AssertionError(
        f"condition never became true: {predicate_js!r} (last={result!r})"
    )


def _open_create_form_at(page: Page, lat: float, lon: float) -> None:
    """Open the add sheet and pan the place-picker to (lat, lon).

    Leaves the create form on screen with its hidden lat/lon inputs seeded
    from the map centre.
    """
    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")
    page.wait_for_selector("#favourite-create-form")
    page.wait_for_selector("#map-place-pin:not([hidden])")
    page.evaluate(f"() => MAP.setCenter([{lon}, {lat}])")
    page.wait_for_function(
        "() => { "
        "const el = document.querySelector('#favourite-create-form input[name=lat]'); "
        f"return el && Math.abs(parseFloat(el.value) - {lat}) < 0.01; "
        "}"
    )


def _go_offline(page: Page) -> None:
    """Force ``navigator.onLine`` false (mutation_queue.js reads this flag)."""
    page.evaluate(
        "() => Object.defineProperty(navigator, 'onLine', "
        "{ value: false, configurable: true })"
    )


def _go_online(page: Page) -> None:
    """Restore ``navigator.onLine`` and fire the ``online`` drain trigger."""
    page.evaluate(
        "() => { "
        "Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); "
        "window.dispatchEvent(new Event('online')); "
        "}"
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_offline_favourite_creation_syncs_without_duplicate(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Offline Save enqueues + confirms optimistically; reconnect syncs once."""
    with django_db_blocker.unblock():
        account = AccountFactory.create()

    _session_login(page.context, live_server.url, account.user)
    _navigate_home_with_sw_stripped(page, live_server.url)

    _open_create_form_at(page, lat=46.2, lon=7.6)
    page.fill("#favourite-name-input", "Offline Peak")

    _go_offline(page)
    page.click("#favourite-create-form button[type=submit]")

    # Optimistic confirmation renders immediately — before any network I/O —
    # cloned from #favourite-confirmation-template, with the offline "will
    # sync" line revealed.
    page.wait_for_selector('#favourite-sheet:has-text("Pin saved")')
    assert page.locator("#favourite-create-form").count() == 0
    pending_line = page.locator("#favourite-sheet [data-favourite-pending]")
    assert pending_line.is_visible()

    # The badge refresh is a separate un-awaited promise chain from enqueue()'s
    # own IndexedDB write — poll rather than read once.
    _poll(
        page,
        """() => {
            const el = document.querySelector('[data-sync-badge]');
            return el && el.textContent === '1 change queued';
          }""",
    )

    rows = page.evaluate("() => window.pwaDb.getAll('queue:mutations')")
    assert len(rows) == 1
    queued_row = rows[0]
    assert queued_row["method"] == "POST"
    assert queued_row["idempotency_key"]

    # The optimistic pending pin is painted into the map's favourites source.
    _poll(
        page,
        """() => {
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.name === 'Offline Peak'
                       && f.properties.pending === true,
            );
        }""",
    )

    # Nothing has actually reached the server yet.
    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 0

    # Reconnect — drain replays the real POST against the live server (no
    # page.route mock: favourite_create runs for real). Generous timeout: the
    # create round-trip includes a live Open-Meteo elevation lookup.
    _go_online(page)
    _poll(
        page,
        "() => window.pwaDb.getAll('queue:mutations').then(r => r.length === 0)",
        timeout_s=15.0,
    )

    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 1
        fav = Favourite.objects.get()
        assert fav.user == account.user
        assert fav.name == "Offline Peak"
        assert float(fav.latitude) == pytest.approx(46.2, abs=0.01)
        assert float(fav.longitude) == pytest.approx(7.6, abs=0.01)

    # The successful drain re-dispatched snowdesk:favourites-changed, so the
    # authoritative (non-pending) server pin has replaced the optimistic one.
    _poll(
        page,
        """() => {
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            const feats = data.features || [];
            const real = feats.filter(
                (f) => f.properties && f.properties.name === 'Offline Peak'
                       && !f.properties.pending,
            );
            const stillPending = feats.some(
                (f) => f.properties && f.properties.pending === true,
            );
            return real.length === 1 && !stillPending;
        }""",
        timeout_s=15.0,
    )

    # Idempotency: re-insert a row carrying the SAME Idempotency-Key/body and
    # drain again — the server must not create a second row. principal is
    # preserved so the SNOW-462 drain-guard doesn't discard the row before it
    # reaches the server.
    page.evaluate(
        """(row) => window.pwaDb.put('queue:mutations', {
            idempotency_key: row.idempotency_key,
            method: row.method,
            url: row.url,
            headers: row.headers,
            body: row.body,
            created_at: new Date().toISOString(),
            attempts: 0,
            status: 'queued',
            next_attempt_at: Date.now(),
            principal: row.principal,
        })""",
        queued_row,
    )
    page.evaluate("() => window.pwaMutationQueue.drain()")
    _poll(
        page,
        "() => window.pwaDb.getAll('queue:mutations').then(r => r.length === 0)",
    )

    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 1
