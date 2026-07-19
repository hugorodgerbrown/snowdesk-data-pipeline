"""
tests/e2e/test_mutation_queue.py — Playwright tests for the SNOW-376 client
mutation queue (static/js/mutation_queue.js, static/js/mutation_queue_core.js).

Covers the named cases from the SNOW-376 scoping comment:

1. Enqueue while offline → a row lands in ``queue:mutations`` and the
   caller returns without a network round-trip; on ``online`` the row
   replays and a 2xx deletes it.
2. A retried request carries the IDENTICAL ``Idempotency-Key`` across ≥2
   successive replays.
3. A ``{400, 401, 403, 404, 422}`` response marks the row ``failed`` on
   the first attempt, no further retry; the failure toast is revealed and
   ``pwa.mutation.failed_permanent`` is emitted.
4. Backoff: a ``{408, 429}`` / network failure schedules a retry
   (``next_attempt_at`` advances, ``attempts`` increments); a row becomes
   ``failed`` after 20 attempts (driven directly via ``window.pwaDb.put``
   to avoid waiting the real backoff delays).
5. The nav badge shows the live non-``failed`` count and is absent when
   the queue empties.
6. ``registration.sync.register('mutation-queue')`` is feature-detected —
   called when ``SyncManager`` is present, skipped without error
   otherwise.

Tests 1-5 use the SIMULATED-SW pattern (``navigator.serviceWorker``
stripped entirely) — these are about the queue's own logic, not the SW
lifecycle, per ``docs/client-side-tests.md``'s "SW-lifecycle tests: real
vs simulated" guidance. Test 6 needs a stubbed ``navigator.serviceWorker``
exposing a controllable ``ready`` registration so the feature-detection
branch can be exercised without a real installed worker (whose activation
timing this harness cannot reliably drive).

Real backoff delays (2s/4s/8s/…) are never awaited directly — every retry
test forces the next replay to be immediately eligible by writing
``next_attempt_at: 0`` straight into the row via ``window.pwaDb.put``
before calling ``window.pwaMutationQueue.drain()`` again.

Polling note
------------
Every multi-step wait below is driven by ``_poll`` (Python-side, one
``page.evaluate`` round-trip at a time) rather than
``page.wait_for_function`` with an ``async () => {...}`` body. Under
xdist's parallel load, Playwright's own polling loop can invoke a fresh
async predicate before an earlier invocation's IndexedDB reads have
settled; two overlapping evaluations of a predicate that does two
sequential ``await pwaDb.getAll(...)`` calls occasionally resolve out of
order, so a still-in-flight, stale "not yet" result would sometimes win
the race — a false negative on a condition that had, in fact, already
become true. Driving the loop from Python guarantees each
``page.evaluate`` call is awaited in full before the next one starts, so
this hazard cannot occur (verified empirically: the async
``wait_for_function`` form failed intermittently under this codebase's
`-n auto` xdist config; ``_poll`` did not fail across dozens of runs).
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from playwright.sync_api import Page, Route
from pytest_django.live_server_helper import LiveServer

DB_NAME = "snowdesk-pwa-v1"
MUTATION_URL = "/api/test-mutation"


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    See ``tests/e2e/test_pwa_db.py`` for the same fixture — kept per-file
    rather than in ``conftest.py`` so the escape hatch is scoped to the
    tests that actually need a bundled browser.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


def _load(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped and wait for the queue.

    Removing ``navigator.serviceWorker`` before any page script runs makes
    ``sw_register.js`` bail out on its first line, and
    ``_registerBackgroundSync()`` in mutation_queue.js bails out the same
    way (``if (!('serviceWorker' in navigator)) return;``) — so these
    tests exercise the queue's own logic without a real SW's asynchronous
    lifecycle events landing at unpredictable timing. Test 6 (Background
    Sync feature-detection) uses its own loader instead.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'serviceWorker', "
        "{ value: undefined, configurable: true });"
    )
    page.goto(live_server_url)
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")


def _delete_db(page: Page) -> None:
    """Delete the PWA DB so each test starts from a clean slate."""
    page.evaluate(
        """(name) => new Promise((resolve) => {
            try {
              const req = indexedDB.deleteDatabase(name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              req.onblocked = () => resolve();
            } catch (_e) { resolve(); }
          })""",
        DB_NAME,
    )


def _poll(
    page: Page,
    predicate_js: str,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> Any:
    """Poll ``page.evaluate(predicate_js)`` until it returns a truthy value.

    See the module docstring's "Polling note" for why this is used instead
    of ``page.wait_for_function`` for every async, IndexedDB-reading
    predicate in this file.

    Returns the truthy value itself (so a predicate can hand back the
    matched row/event, not just ``True``).
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


def _mutation_rows(page: Page) -> list[dict[str, Any]]:
    """Read every row currently in queue:mutations."""
    rows: list[dict[str, Any]] = page.evaluate(
        "() => window.pwaDb.getAll('queue:mutations')"
    )
    return rows


def _force_immediate_eligibility(page: Page) -> None:
    """Rewrite the sole queued row's next_attempt_at to 0 (i.e. "now").

    Lets a retry test drive successive replays without waiting the real
    exponential backoff delay between attempts.
    """
    page.evaluate(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            if (rows.length) {
                rows[0].next_attempt_at = 0;
                await window.pwaDb.put('queue:mutations', rows[0]);
            }
          }"""
    )


# ---------------------------------------------------------------------------
# 1. Enqueue while offline → replays on 'online' → 2xx deletes the row.
# ---------------------------------------------------------------------------


def test_enqueue_offline_then_replays_on_online(
    live_server: LiveServer, page: Page
) -> None:
    """No network round-trip while offline; the row replays once online."""
    _load(page, live_server.url)
    _delete_db(page)

    call_count = {"n": 0}

    def handler(route: Route) -> None:
        call_count["n"] += 1
        route.fulfill(status=201, body="")

    page.route(f"**{MUTATION_URL}", handler)

    depth = page.evaluate(
        """async (url) => {
            Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length;
          }""",
        MUTATION_URL,
    )
    assert depth == 1
    assert call_count["n"] == 0, (
        "enqueue while offline must not attempt a network round-trip"
    )

    page.evaluate(
        """() => {
            Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
            window.dispatchEvent(new Event('online'));
          }"""
    )
    # Fold both conditions into one predicate: the telemetry write is a
    # separate un-awaited promise chain from the row deletion drain()
    # itself awaits, so checking queue:events as a follow-up read after
    # only the row-count condition would be a race (see
    # test_permanent_4xx_fails_on_first_attempt for the same hazard).
    drained_event = _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            if (rows.length !== 0) return false;
            const events = await window.pwaDb.getAll('queue:events');
            return events.find((e) => e.event === 'pwa.mutation.drained') || false;
          }""",
    )
    assert call_count["n"] == 1
    # The real drained count (1), not the zero-row fallback drain() would
    # report on a no-op run.
    assert drained_event["properties"]["count"] == 1


# ---------------------------------------------------------------------------
# 2. Identical Idempotency-Key across ≥2 successive replays.
# ---------------------------------------------------------------------------


def test_identical_idempotency_key_across_retries(
    live_server: LiveServer, page: Page
) -> None:
    """The same row carries the same Idempotency-Key on every replay."""
    _load(page, live_server.url)
    _delete_db(page)

    captured_keys: list[str | None] = []
    call_count = {"n": 0}

    def handler(route: Route) -> None:
        call_count["n"] += 1
        captured_keys.append(route.request.headers.get("idempotency-key"))
        if call_count["n"] < 3:
            route.fulfill(status=500, body="")
        else:
            route.fulfill(status=201, body="")

    page.route(f"**{MUTATION_URL}", handler)

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    # First attempt (500) happens automatically — enqueue() drains
    # immediately when navigator.onLine is true (the default).
    _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length === 1 && rows[0].attempts === 1;
          }""",
    )

    # Force immediate eligibility twice more (skip the real 2s/4s backoff).
    for _ in range(2):
        _force_immediate_eligibility(page)
        page.evaluate("""async () => { await window.pwaMutationQueue.drain(); }""")

    _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length === 0;
          }""",
    )

    assert call_count["n"] == 3
    assert len(captured_keys) == 3
    assert all(captured_keys[0] == k for k in captured_keys), captured_keys
    assert captured_keys[0]


# ---------------------------------------------------------------------------
# 3. Permanent 4xx → failed on first attempt; toast + failed_permanent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_4xx_fails_on_first_attempt(
    live_server: LiveServer, page: Page, status: int
) -> None:
    """A non-{408,429} 4xx marks the row failed with no further retry."""
    _load(page, live_server.url)
    _delete_db(page)

    call_count = {"n": 0}

    def handler(route: Route) -> None:
        call_count["n"] += 1
        route.fulfill(status=status, body="")

    page.route(f"**{MUTATION_URL}", handler)

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    # Fold every condition into one predicate (docs/client-side-tests.md):
    # the telemetry write is a separate un-awaited promise chain from the
    # mutation-row write, so checking queue:events as a follow-up read
    # after only the row/toast conditions is a real race.
    _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            const failed = rows.length === 1 && rows[0].status === 'failed';
            const toastEl = document.getElementById('mutation-queue-toast');
            const toastVisible = toastEl ? !toastEl.classList.contains('hidden') : false;
            const events = await window.pwaDb.getAll('queue:events');
            const hasEvent = events.some((e) => e.event === 'pwa.mutation.failed_permanent');
            return failed && toastVisible && hasEvent;
          }""",
    )
    assert call_count["n"] == 1, "a permanent 4xx must not be retried"

    row = page.evaluate(
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            return rows.find((r) => r.event === 'pwa.mutation.failed_permanent');
          }"""
    )
    assert row is not None
    assert row["properties"]["reason"] == "permanent_4xx"
    # The attempt that just failed counts — a permanent 4xx is not "never
    # attempted".
    assert row["properties"]["attempts"] == 1

    stored_row = _mutation_rows(page)[0]
    assert stored_row["attempts"] == 1


def test_toast_dismiss_plays_slide_up_before_hiding(
    live_server: LiveServer, page: Page
) -> None:
    """Dismissing the toast reverses the transform/opacity first and only
    adds ``hidden`` once that transition has actually finished — not in
    the same synchronous tick (regression test: an earlier version added
    ``hidden`` immediately, which collapsed the animation to nothing).
    """
    _load(page, live_server.url)
    _delete_db(page)

    page.route(f"**{MUTATION_URL}", lambda route: route.fulfill(status=422, body=""))

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    _poll(
        page,
        """() => {
            const el = document.getElementById('mutation-queue-toast');
            return !!(el && !el.classList.contains('hidden'));
          }""",
    )

    page.evaluate(
        """() => {
            document
              .getElementById('mutation-queue-toast')
              .querySelector('[data-action="dismiss"]')
              .click();
          }"""
    )
    # Immediately after the click, the toast must still be un-hidden and
    # mid-transition (transform reversed) — not vanished outright.
    immediate = page.evaluate(
        """() => {
            const el = document.getElementById('mutation-queue-toast');
            return {
                hidden: el.classList.contains('hidden'),
                translatedUp: el.classList.contains('-translate-y-full'),
            };
          }"""
    )
    assert immediate["hidden"] is False
    assert immediate["translatedUp"] is True

    # It does eventually become hidden once the transition (or its
    # fallback timer) completes.
    _poll(
        page,
        """() => {
            const el = document.getElementById('mutation-queue-toast');
            return el ? el.classList.contains('hidden') : true;
          }""",
        timeout_s=2.0,
    )


# ---------------------------------------------------------------------------
# 4. Backoff — {408,429}/network failure schedules a retry; failed at 20.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429])
def test_retryable_status_schedules_backoff(
    live_server: LiveServer, page: Page, status: int
) -> None:
    """A {408,429} response advances next_attempt_at and increments attempts."""
    _load(page, live_server.url)
    _delete_db(page)

    page.route(f"**{MUTATION_URL}", lambda route: route.fulfill(status=status, body=""))

    before = page.evaluate("() => Date.now()")
    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length === 1 && rows[0].attempts === 1;
          }""",
    )
    row = _mutation_rows(page)[0]

    assert row["status"] == "retry-scheduled"
    assert row["attempts"] == 1
    # Backoff schedule: attempts=1 → 2s. Generous bounds absorb IPC/clock
    # skew between the test process and the page's own Date.now().
    delay = row["next_attempt_at"] - before
    assert 1000 <= delay <= 4000, delay


def test_network_error_is_treated_as_retry(live_server: LiveServer, page: Page) -> None:
    """A network-level failure (route.abort) schedules a retry, not a permanent fail."""
    _load(page, live_server.url)
    _delete_db(page)

    page.route(f"**{MUTATION_URL}", lambda route: route.abort("failed"))

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length === 1 && rows[0].attempts === 1;
          }""",
    )
    row = _mutation_rows(page)[0]
    assert row["status"] == "retry-scheduled"


def test_row_fails_after_max_attempts(live_server: LiveServer, page: Page) -> None:
    """A row that reaches MAX_ATTEMPTS (20) without a 2xx is marked failed.

    Seeds the row directly at attempts=19 (rather than replaying 19 real
    retries) so the test doesn't depend on the real backoff schedule.
    """
    _load(page, live_server.url)
    _delete_db(page)

    page.route(f"**{MUTATION_URL}", lambda route: route.fulfill(status=429, body=""))

    page.evaluate(
        """async (url) => {
            await window.pwaDb.put('queue:mutations', {
                idempotency_key: 'fixed-test-key',
                method: 'POST',
                url,
                headers: {},
                body: null,
                created_at: new Date().toISOString(),
                attempts: 19,
                status: 'retry-scheduled',
                next_attempt_at: 0,
            });
            await window.pwaMutationQueue.drain();
          }""",
        MUTATION_URL,
    )

    row = _mutation_rows(page)[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 20

    # The telemetry write is a separate un-awaited promise chain from the
    # row write drain() itself awaits — poll rather than read once.
    events = _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:events');
            const matches = rows.filter((r) => r.event === 'pwa.mutation.failed_permanent');
            return matches.length ? matches : false;
          }""",
    )
    assert any(e["properties"]["reason"] == "max_attempts" for e in events)


# ---------------------------------------------------------------------------
# 5. Nav badge — live non-failed count; absent when the queue empties.
# ---------------------------------------------------------------------------


def test_nav_badge_reflects_pending_count_and_hides_when_empty(
    live_server: LiveServer, page: Page
) -> None:
    """The badge shows the pending count and hides once the queue drains."""
    _load(page, live_server.url)
    _delete_db(page)

    page.evaluate(
        """async (url) => {
            Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )

    badge_text = page.evaluate(
        """() => {
            const el = document.querySelector('[data-sync-badge]');
            return el && !el.classList.contains('hidden') ? el.textContent : null;
          }"""
    )
    assert badge_text == "2 changes queued"

    page.route(f"**{MUTATION_URL}", lambda route: route.fulfill(status=201, body=""))
    page.evaluate(
        """() => {
            Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
          }"""
    )
    page.evaluate("""async () => { await window.pwaMutationQueue.drain(); }""")

    _poll(
        page,
        """() => {
            const el = document.querySelector('[data-sync-badge]');
            return el ? el.classList.contains('hidden') : true;
          }""",
    )


def test_nav_badge_singular_form(live_server: LiveServer, page: Page) -> None:
    """A single pending row reads "1 change queued" (not "1 changes queued")."""
    _load(page, live_server.url)
    _delete_db(page)

    badge_text = page.evaluate(
        """async (url) => {
            Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
            const el = document.querySelector('[data-sync-badge]');
            return el ? el.textContent : null;
          }""",
        MUTATION_URL,
    )
    assert badge_text == "1 change queued"


# ---------------------------------------------------------------------------
# 6. registration.sync.register('mutation-queue') is feature-detected.
# ---------------------------------------------------------------------------


def _load_with_fake_service_worker(
    page: Page, live_server_url: str, *, sync_supported: bool
) -> None:
    """Load with a stubbed navigator.serviceWorker exposing a controllable
    ``ready`` registration, so the Background Sync feature-detection branch
    in ``_registerBackgroundSync()`` can be exercised without a real
    installed worker (whose activation timing this harness cannot reliably
    drive — see docs/client-side-tests.md's "SW-lifecycle tests: real vs
    simulated"). Every ``sync.register()`` call is captured into
    ``window.__syncCalls`` for the test to inspect.
    """
    flag = "true" if sync_supported else "false"
    page.add_init_script(
        f"""(() => {{
            window.__syncCalls = [];
            const syncSupported = {flag};
            const fakeRegistration = {{
                installing: null,
                waiting: null,
                active: null,
                update: () => Promise.resolve(),
            }};
            if (syncSupported) {{
                fakeRegistration.sync = {{
                    register: (tag) => {{
                        window.__syncCalls.push(tag);
                        return Promise.resolve();
                    }},
                }};
            }}
            Object.defineProperty(navigator, 'serviceWorker', {{
                configurable: true,
                value: {{
                    addEventListener: () => {{}},
                    removeEventListener: () => {{}},
                    ready: Promise.resolve(fakeRegistration),
                    register: () => Promise.resolve(fakeRegistration),
                    getRegistrations: () => Promise.resolve([]),
                    controller: null,
                }},
            }});
          }})();"""
    )
    page.goto(live_server_url)
    page.wait_for_load_state("load")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")


def test_background_sync_registered_when_sync_manager_present(
    live_server: LiveServer, page: Page
) -> None:
    """registration.sync.register(SYNC_TAG) is called when SyncManager exists."""
    _load_with_fake_service_worker(page, live_server.url, sync_supported=True)
    _delete_db(page)

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    _poll(page, "() => window.__syncCalls.length > 0")
    calls = page.evaluate("() => window.__syncCalls")
    assert calls == ["mutation-queue"]


def test_background_sync_skipped_without_error_when_unsupported(
    live_server: LiveServer, page: Page
) -> None:
    """No SyncManager → the registration is skipped, silently, no page error."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    _load_with_fake_service_worker(page, live_server.url, sync_supported=False)
    _delete_db(page)

    page.evaluate(
        """async (url) => {
            await window.pwaMutationQueue.enqueue({ method: 'POST', url });
          }""",
        MUTATION_URL,
    )
    # Give any (incorrect) async registration attempt a moment to surface
    # before asserting nothing happened.
    page.wait_for_timeout(200)

    calls = page.evaluate("() => window.__syncCalls")
    assert calls == []
    assert page_errors == [], f"JS errors: {page_errors}"
