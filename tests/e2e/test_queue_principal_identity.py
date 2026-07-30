"""
tests/e2e/test_queue_principal_identity.py — Playwright test for SNOW-549:
the PWA mutation queue's principal is ``Account.uuid``, not the user PK.

The queue partitions queued mutations by "principal" — the value in the
``pwa-user-id`` meta tag — so a mutation queued by one account is never
replayed under another (SNOW-462). SNOW-549 changed what that value *is*,
from ``str(request.user.pk)`` to the account uuid, because the sequential
primary key leaked the size and growth rate of the user base and made
spoofing a client-supplied identity a one-guess exercise.

``mutation_queue.js`` / ``db.js`` / ``sw.js`` treat the principal as an
opaque string and only compare it for equality, so the change needed no JS
edit. That is exactly the claim worth an end-to-end test, because it is
easy to break silently from the server side. Two halves:

1. **The identity is right, and the queue still works under it** — the meta
   tag carries the signed-in user's ``Account.uuid`` (and *not* their PK),
   a mutation queued offline records that same principal, and reconnecting
   drains it into a real ``Favourite`` row.
2. **Partitioning still holds** — a queued row whose principal belongs to a
   different account is discarded on drain rather than replayed under the
   current one. This is the fail-safe direction, and it is what makes the
   deploy-boundary flush safe: every existing user's principal changes from
   an integer to a uuid on first load after deploy, so their queue is
   discarded once rather than misattributed.

Simulated-SW pattern (``navigator.serviceWorker`` stripped) and Python-side
``_poll`` for the same reasons as
``tests/e2e/test_offline_favourite_submit.py`` — see that module's
docstring and ``docs/client-side-tests.md``.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from apps.favourites.models import Favourite
from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory


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


def _navigate_home_with_sw_stripped(page: Page, live_server_url: str) -> None:
    """Load / with serviceWorker stripped; wait for the map and the queue."""
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


def _open_create_form_at(page: Page, lat: float, lon: float) -> None:
    """Open the add sheet and pan the place-picker to (lat, lon)."""
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


def _meta_user_id(page: Page) -> str:
    """Return the ``pwa-user-id`` meta tag content as the page sees it."""
    content = page.evaluate(
        "() => document.querySelector('meta[name=pwa-user-id]')?.content ?? ''"
    )
    return str(content)


@pytest.mark.django_db(transaction=True)
def test_queue_principal_is_the_account_uuid_and_still_drains(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """The principal is Account.uuid, and an offline mutation still syncs."""
    with django_db_blocker.unblock():
        account = AccountFactory.create()
        account_uuid = str(account.uuid)
        user_pk = str(account.user.pk)

    _session_login(page.context, live_server.url, account.user)
    _navigate_home_with_sw_stripped(page, live_server.url)

    principal = _meta_user_id(page)
    assert principal == account_uuid
    assert principal != user_pk, "the sequential user PK is still being exposed"

    _open_create_form_at(page, lat=46.2, lon=7.6)
    page.fill("#favourite-name-input", "Principal Peak")

    _go_offline(page)
    page.click("#favourite-create-form button[type=submit]")

    queued = _poll(
        page,
        """() => window.pwaDb.getAll('queue:mutations')
             .then(rows => (rows.length === 1 ? rows[0] : null))""",
    )
    assert queued["principal"] == account_uuid

    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 0

    _go_online(page)
    _poll(
        page,
        "() => window.pwaDb.getAll('queue:mutations').then(r => r.length === 0)",
        timeout_s=15.0,
    )

    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_queue_row_from_another_principal_is_discarded(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """A row queued under a different principal is dropped, never replayed.

    This is the SNOW-462 guard that makes SNOW-549's deploy-boundary flush
    safe: on first load after deploy every existing user's principal changes
    from an integer PK to a uuid, and their queued rows are discarded rather
    than replayed under the new identity.
    """
    with django_db_blocker.unblock():
        account = AccountFactory.create()
        other = AccountFactory.create()
        stale_principal = str(other.uuid)

    _session_login(page.context, live_server.url, account.user)
    _navigate_home_with_sw_stripped(page, live_server.url)

    assert _meta_user_id(page) == str(account.uuid)

    # Hand-queue a well-formed favourite create owned by the *other* account.
    page.evaluate(
        """(principal) => window.pwaDb.put('queue:mutations', {
            idempotency_key: 'stale-principal-key',
            method: 'POST',
            url: '/favourites/create/',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'name=Stale+Peak&lat=46.2&lon=7.6',
            created_at: new Date().toISOString(),
            attempts: 0,
            status: 'queued',
            next_attempt_at: Date.now(),
            principal: principal,
        })""",
        stale_principal,
    )

    page.evaluate("() => window.pwaMutationQueue.drain()")
    _poll(
        page,
        "() => window.pwaDb.getAll('queue:mutations').then(r => r.length === 0)",
        timeout_s=15.0,
    )

    # Discarded, not replayed — neither account gained a favourite.
    with django_db_blocker.unblock():
        assert Favourite.objects.count() == 0
