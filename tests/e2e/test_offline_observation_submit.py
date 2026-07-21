"""
tests/e2e/test_offline_observation_submit.py — Playwright test for SNOW-420:
offline field-report submission through the client mutation queue.

Covers the full offline → reconnect journey wiring
``observations.views.report_submit`` through ``window.pwaMutationQueue``
(``static/js/report.js`` / ``static/js/mutation_queue.js``, SNOW-376):

1. A tap while offline enqueues exactly one ``queue:mutations`` row and
   returns immediately (no network round-trip) — the optimistic
   confirmation renders straight away with the "will sync when you're back
   online" pending line visible, the nav sync badge shows "1 change
   queued", and zero ``FieldObservation`` rows exist yet.
2. Reconnecting drains the queue against the REAL ``report_submit`` view
   (not a mocked route) — exactly one ``FieldObservation`` row lands, and
   its ``observed_at`` is the tap-time instant captured client-side before
   the reconnect, not whenever the mutation happened to replay.
3. Replaying the same operation a second time — same Idempotency-Key, same
   body — does not create a duplicate row:
   ``core.idempotency.IdempotencyMiddleware`` dedupes it server-side.

Uses the simulated-SW pattern (``navigator.serviceWorker`` stripped) — see
``docs/client-side-tests.md``'s "SW-lifecycle tests: real vs simulated" —
this test is about the queue's own logic plus ``report_submit``, not the
SW lifecycle itself. Geolocation is driven via a real, permission-granted
``navigator.geolocation`` fix (rather than dispatching
``snowdesk:geolocate`` directly) so the test also exercises report.js's
real GPS-path form load, not just the submit handler in isolation.

Polling note: every multi-step wait is driven by ``_poll`` (Python-side,
one ``page.evaluate`` round-trip at a time) rather than
``page.wait_for_function`` with an async body — see
``tests/e2e/test_mutation_queue.py``'s module docstring for why (xdist
parallel-run race between overlapping async predicate evaluations).
"""

from __future__ import annotations

import datetime
import time
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, UserFactory

DB_NAME = "snowdesk-pwa-v1"


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map + queue.

    Stripping serviceWorker (before any page script runs) makes
    sw_register.js and mutation_queue.js's ``_registerBackgroundSync()``
    bail out immediately — see ``tests/e2e/test_mutation_queue.py``'s
    ``_load`` for the identical technique and rationale.
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
    """Poll ``page.evaluate(predicate_js)`` until it returns a truthy value.

    See the module docstring's "Polling note".
    """
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


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_offline_report_submission_syncs_without_duplicate(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Offline tap enqueues + confirms optimistically; reconnect syncs once."""
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    # Open the sheet — report.js requests a real GPS fix, which resolves
    # immediately against the granted/stubbed geolocation above.
    page.click("#report-btn")
    page.wait_for_selector("#report-form")

    # Go offline — mutation_queue.js reads navigator.onLine (not the browser
    # context's own offline flag), matching test_mutation_queue.py.
    page.evaluate(
        "() => Object.defineProperty(navigator, 'onLine', "
        "{ value: false, configurable: true })"
    )

    tap_time = datetime.datetime.now(datetime.timezone.utc)
    page.click('#report-form button[name="observation_type"][value="WHUMPFING"]')

    # The optimistic confirmation renders immediately — before any network
    # I/O — cloned from the #report-confirmation-template embedded in the
    # form partial.
    page.wait_for_selector('#report-sheet:has-text("Thank you for your report")')
    assert page.locator("#report-sheet #report-form").count() == 0
    pending = page.locator("#report-sheet [data-report-pending]")
    assert pending.is_visible()

    # The badge refresh is a separate un-awaited promise chain from
    # enqueue()'s own IndexedDB write (mutation_queue.js fires it off
    # without the caller awaiting it) — poll rather than read once.
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

    # Nothing has actually reached the server yet.
    with django_db_blocker.unblock():
        assert FieldObservation.objects.count() == 0

    # Deliberate gap so the eventual observed_at is distinguishable from
    # "whenever the queue happened to drain".
    page.wait_for_timeout(1500)

    # Reconnect — drain replays the real POST against the live server (no
    # page.route mock: report_submit runs for real).
    page.evaluate(
        "() => { "
        "Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); "
        "window.dispatchEvent(new Event('online')); "
        "}"
    )

    _poll(
        page,
        "async () => (await window.pwaDb.getAll('queue:mutations')).length === 0",
    )

    with django_db_blocker.unblock():
        assert FieldObservation.objects.count() == 1
        obs = FieldObservation.objects.get()
        assert obs.observation_type == FieldObservation.OBSERVATION_TYPE.WHUMPFING
        assert obs.user == user
        # observed_at is the tap-time instant captured above, not whenever
        # the reconnect/replay happened — close to the tap, and clearly
        # earlier than "now" by roughly the deliberate gap.
        assert abs((obs.observed_at - tap_time).total_seconds()) < 2
        now = datetime.datetime.now(datetime.timezone.utc)
        assert (now - obs.observed_at).total_seconds() >= 1

    # Idempotency: re-insert a row carrying the SAME Idempotency-Key/body
    # and drain again — the server must not create a second row
    # (core.idempotency.IdempotencyMiddleware dedupes the replay).
    # principal: row.principal preserves the original enqueue-time stamp so
    # the SNOW-462 drain-guard doesn't discard this row before it reaches
    # the server (which would make the assertion below pass for the wrong
    # reason).
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
    page.evaluate("async () => { await window.pwaMutationQueue.drain(); }")
    _poll(
        page,
        "async () => (await window.pwaDb.getAll('queue:mutations')).length === 0",
    )

    with django_db_blocker.unblock():
        assert FieldObservation.objects.count() == 1


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_reset_required_state_shows_error_toast_not_false_confirmation(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """When IndexedDB is in the terminal Reset-Required state, a report tap
    must NOT show the optimistic "Thank you" confirmation.

    ``window.pwaMutationQueue.enqueue()`` is defensively non-fatal: in
    Reset-Required it silently drops the operation and still resolves
    (SNOW-375/376 contract), so awaiting it cannot tell report.js the report
    was lost. report.js guards on ``window.pwaDb.isResetRequired()`` up front
    and surfaces the error toast instead of a success confirmation. Without
    that guard the user would see "Thank you for your report" for a report
    that was never persisted and can never sync.
    """
    with django_db_blocker.unblock():
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=True)

    _session_login(page.context, live_server.url, user)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    # Poison the DB to a version above db.js's DB_VERSION, then reload so
    # db.js's fresh open() hits VersionError → terminal Reset-Required state.
    # Same failure path as tests/e2e/test_pwa_db.py, driven via reload so
    # db.js opens the poisoned version from a clean module state.
    page.evaluate(
        """async (name) => {
            await new Promise((resolve) => {
              const del = indexedDB.deleteDatabase(name);
              // Only settle on a real terminal event — NOT onblocked, which
              // fires while the delete is still pending behind db.js's live
              // connection. Resolving there would reopen at version 99 against
              // the still-present DB. db.js yields on versionchange, so the
              // block clears itself. See tests/e2e/test_pwa_db.py::_delete_db.
              del.onsuccess = del.onerror = () => resolve();
            });
            await new Promise((resolve, reject) => {
              const req = indexedDB.open(name, 99);
              req.onsuccess = () => { req.result.close(); resolve(); };
              req.onerror = () => reject(req.error);
              req.onupgradeneeded = () => {};
            });
          }""",
        DB_NAME,
    )
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    _poll(page, "() => window.pwaDb.isResetRequired() === true")

    # Remove the full-screen terminal overlay so it can't intercept the taps
    # below — this test exercises report.js's guard, not the overlay itself.
    page.evaluate(
        "() => { const o = document.getElementById('pwa-reset-required'); "
        "if (o) o.remove(); }"
    )
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )

    page.click("#report-btn")
    page.wait_for_selector("#report-form")
    page.click('#report-form button[name="observation_type"][value="WHUMPFING"]')

    # The guard fires: the error toast appears, the form stays put (no
    # optimistic confirmation swapped in), and nothing is claimed as recorded.
    page.wait_for_selector("#report-toast")
    assert page.locator("#report-sheet #report-form").count() == 1
    assert (
        page.locator('#report-sheet:has-text("Thank you for your report")').count() == 0
    )
