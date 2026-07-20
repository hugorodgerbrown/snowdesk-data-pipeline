"""
tests/e2e/test_mutation_queue_account_change.py — Playwright test for
SNOW-462: clear/partition the PWA mutation queue on account change.

Headline regression: user A queues a real ``report_submit`` mutation
offline, signs out, and user B signs in on the same browser before
reconnect. Without the SNOW-462 fix (principal-stamped rows + reconcile-on-
load + drain-guard, ``static/js/mutation_queue.js``), A's queued mutation
would replay attributed to whichever account happens to be signed in at
drain time — here, B. This test proves it never reaches the server at all
under B's session: the reconcile-on-load path clears A's row the moment
B's page load renders a different ``<meta name="pwa-user-id">``.

Modelled on ``tests/e2e/test_offline_observation_submit.py`` — the real
first consumer of the mutation queue — reusing its offline
field-observation-submit journey (geolocation grant, ``field_observations``
waffle flag, ``navigator.onLine`` toggle) rather than a synthetic mutation,
so this test exercises the actual call site, not just the queue's own
machinery (which ``tests/e2e/test_mutation_queue.py`` already covers with
synthetic rows).

Uses the simulated-SW pattern (``navigator.serviceWorker`` stripped) — see
``tests/e2e/test_mutation_queue.py``'s module docstring for the rationale
and the ``_poll`` polling note (xdist parallel-run race between
overlapping async predicate evaluations).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory, UserFactory


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with navigator.serviceWorker stripped, wait for the map + queue.

    Identical technique to ``test_offline_observation_submit.py``'s helper
    of the same name — duplicated here (rather than imported) so this
    module has no dependency on that file's internals.
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

    See ``tests/e2e/test_mutation_queue.py``'s module docstring's "Polling
    note" for why this is used instead of ``page.wait_for_function`` with
    an async predicate.
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
def test_account_change_before_reconnect_discards_the_other_users_queue(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """A's offline report never lands under B's account on the same browser.

    User A queues a real field-report submission while offline; before
    reconnecting, user B signs in on the same browser and the page reloads
    (re-rendering ``<meta name="pwa-user-id">`` as B). The reconcile-on-
    load path in ``mutation_queue.js`` must clear A's queued row at that
    point — reconnecting afterwards must create ZERO ``FieldObservation``
    rows for either user's session under B, and specifically none
    attributed to B.
    """
    with django_db_blocker.unblock():
        user_a = UserFactory.create()
        AccountFactory.create(user=user_a, is_verified=True)
        user_b = UserFactory.create()
        AccountFactory.create(user=user_b, is_verified=True)

    _session_login(page.context, live_server.url, user_a)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 46.10, "longitude": 7.10, "accuracy": 10})

    _navigate_home_with_sw_stripped(page, live_server.url)

    # Open the sheet — report.js requests a real GPS fix, which resolves
    # immediately against the granted/stubbed geolocation above.
    page.click("#report-btn")
    page.wait_for_selector("#report-form")

    # Go offline before submitting — mutation_queue.js reads navigator.onLine
    # (not the browser context's own offline flag), matching
    # test_offline_observation_submit.py / test_mutation_queue.py.
    page.evaluate(
        "() => Object.defineProperty(navigator, 'onLine', "
        "{ value: false, configurable: true })"
    )
    page.click('#report-form button[name="observation_type"][value="WHUMPFING"]')

    # The optimistic confirmation renders immediately — before any network
    # I/O — confirming the row was actually queued client-side.
    page.wait_for_selector('#report-sheet:has-text("Thank you for your report")')

    row = _poll(
        page,
        """async () => {
            const rows = await window.pwaDb.getAll('queue:mutations');
            return rows.length === 1 ? rows[0] : false;
          }""",
    )
    assert row["principal"] == str(user_a.pk)

    with django_db_blocker.unblock():
        assert FieldObservation.objects.count() == 0

    # User B signs in on the SAME browser, still offline, and the page
    # reloads — re-rendering pwa-user-id as B and running the
    # reconcile-on-load path first thing in _wireLifecycle().
    _session_login(page.context, live_server.url, user_b)
    _navigate_home_with_sw_stripped(page, live_server.url)

    # A's row must be gone before reconnect even happens — the
    # reconcile-on-load path, not the drain-guard, is what did this.
    _poll(
        page,
        """async () => (await window.pwaDb.getAll('queue:mutations')).length === 0""",
    )

    # Reconnect and drain — nothing left to replay, but exercise the full
    # lifecycle path the way a real reconnect would.
    page.evaluate(
        "() => { "
        "Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); "
        "window.dispatchEvent(new Event('online')); "
        "}"
    )
    page.evaluate("async () => { await window.pwaMutationQueue.drain(); }")
    _poll(
        page,
        """async () => (await window.pwaDb.getAll('queue:mutations')).length === 0""",
    )

    with django_db_blocker.unblock():
        # The core regression: B's account must never receive A's report.
        assert FieldObservation.objects.filter(user=user_b).count() == 0
        # And, in fact, nothing reached the server under either account —
        # the queued mutation was discarded, not silently re-attributed.
        assert FieldObservation.objects.count() == 0
