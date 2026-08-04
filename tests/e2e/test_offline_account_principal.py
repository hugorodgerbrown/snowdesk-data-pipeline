"""
tests/e2e/test_offline_account_principal.py — C1 principal partitioning of
the shell cache (``docs/code-reviews/2026-08-03-js-review.md``).

``_networkFirst`` (``static/js/sw.js``) used to write every 2xx same-origin
navigation into the shell cache with nothing recording who it was rendered
for, and sign-out purges neither the cache nor the session's trace in it.
So: sign in, visit ``/account/manage/``, sign out, go offline, navigate
back — and the worker replayed the previous user's rendered page, email
address included. ``Vary: Cookie`` is no help; ``Cookie`` is a forbidden
header name, invisible to the Cache API's Vary comparison, so the match
succeeds whoever is signed in.

The guard that ships is partitioning, not avoidance. Every cached
navigation carries an ``X-SW-Principal`` header naming the account its HTML
was rendered for, read out of the response's own
``<meta name="pwa-user-id">``. The offline read compares that stamp against
the principal signed in now — the ``mutations.principal`` row in
``meta:app``, maintained by ``static/js/mutation_queue.js`` — and serves
the entry on a match alone. Everything else falls through to
``/static/offline.html``.

Why partitioned rather than ``@never_cache``: the offline favourites roster
(``tests/e2e/test_favourites_offline.py``) repaints from the manage page
served out of the shell cache, so ``no-store`` on ``manage_view`` would
take a shipped feature offline with it. The page being cached is a feature,
which is why it is the first assertion of every case here rather than
something to guard against. ``change_email_view`` does keep
``@never_cache`` — nothing needs that one offline. See
``apps/accounts/views.py`` and
``tests/accounts/test_views.py::test_response_is_not_no_store``.

Three cases:

  * the manage page IS cached for the account that loaded it, stamped with
    that account's uuid;
  * after a sign-out, an offline navigation back to it renders the offline
    fallback rather than the previous user's email;
  * a second account signed in on the same device gets the same refusal.

Two techniques every case rests on:

1. **Cache membership is read, never inferred.** Every claim about what is
   in the cache comes from ``caches.match(...)`` evaluated in the page.
   ``page.context.set_offline(True)`` governs page-side network only — a
   request the service worker re-issues goes out through the worker's own
   ``fetch()``, which reaches the live server whatever the flag says — so
   "the request failed" would prove nothing about the cache. Reading the
   entry also lets each refusal case re-check afterwards that the entry is
   still present and still stamped for the first account, which separates
   "the read was refused" from "the entry was gone".

2. **The offline flag drives navigations only.** ``page.goto`` while
   offline does reach ``_networkFirst``'s fallback branch — the pattern
   established by ``tests/e2e/test_pwa_lifecycle_offline.py::
   test_offline_navigation_to_never_visited_url_shows_offline_fallback``.
   No assertion here depends on a subresource failing to load.

Both refusal cases wait for ``mutations.principal`` to settle before going
offline. That row is written by the page's own reconcile on the load AFTER
an account change, fire-and-forget, and it is the only "who is signed in
now" signal the worker has — so going offline before it lands would test
the previous session's principal.

Strategy-level coverage of the same guard — the ``no-store`` skip, the
stamp, and the principal check on both request-matched reads — is in
``tests/js/test_sw.js``, where the worker's ``fetch`` can be made to fail
outright.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import SignedInPage, _session_login
from tests.factories import AccountFactory

MANAGE_URL_PATH = "/account/manage/"

# How long to keep polling for a URL to appear in the shell cache.
# ``_networkFirst``'s write is not awaited — the response must not wait on
# the body being buffered to read its meta tag — so an immediate check can
# fail just by being early.
_CACHE_POLL_MS = 5000

# How long to keep polling for ``mutations.principal`` to reflect an account
# change. Written by ``_reconcilePrincipal()`` on load, also fire-and-forget.
_PRINCIPAL_POLL_MS = 5000

# Reads the shell cache for one URL and reports its principal stamp. Same
# read path ``map_layer_sync_status.js::_probeExact`` uses.
_PROBE_JS = """async (url) => {
  const keys = (await caches.keys()).filter((k) => k.startsWith('snowdesk-shell-'));
  for (const key of keys) {
    const cache = await caches.open(key);
    const hit = await cache.match(url);
    if (hit) return { found: true, principal: hit.headers.get('X-SW-Principal') };
  }
  return { found: false, principal: null };
}"""

# Polls until the URL appears in the shell cache, returning its stamp.
_WAIT_FOR_CACHED_JS = """async ({ url, timeoutMs }) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const keys = (await caches.keys()).filter((k) => k.startsWith('snowdesk-shell-'));
    for (const key of keys) {
      const cache = await caches.open(key);
      const hit = await cache.match(url);
      if (hit) return { found: true, principal: hit.headers.get('X-SW-Principal') };
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return { found: false, principal: null };
}"""

# Polls ``meta:app`` until ``mutations.principal`` holds ``expected`` — the
# one row ``sw.js::_currentPrincipal`` consults for "who is signed in now".
# ``null`` is the value a signed-out session persists; the worker reads that,
# an empty value and an absent row alike as anonymous. A row that never
# arrives reports as ``<missing>``, so a failure separates "never written"
# from "written with the wrong value".
_WAIT_FOR_PRINCIPAL_JS = """async ({ expected, timeoutMs }) => {
  const deadline = Date.now() + timeoutMs;
  let seen = '<missing>';
  while (Date.now() < deadline) {
    if (typeof window.pwaDb === 'object') {
      const rows = await window.pwaDb.getAll('meta:app');
      const row = rows.find((r) => r.key === 'mutations.principal');
      if (row) {
        const value =
          row.value === undefined || row.value === '' ? null : row.value;
        seen = value === null ? 'null' : String(value);
        if (value === expected) return { settled: true, seen };
      }
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return { settled: false, seen };
}"""

_SIGN_OUT_JS = """() => {
  const form = document.querySelector('form[action$="/account/sign-out/"]');
  if (!form) throw new Error('sign-out form not found in the nav');
  form.submit();
}"""


def _visit_manage(signed_in_page: SignedInPage) -> str:
    """Load ``/account/manage/`` as the signed-in account; return its URL.

    Asserts the page rendered for the account rather than redirecting to
    sign-in, so a later cache read is known to be looking for authenticated
    HTML and not for a redirect.
    """
    page = signed_in_page.page
    manage_url = signed_in_page.live_server_url + MANAGE_URL_PATH
    page.goto(manage_url)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url, f"redirected to {page.url} — session not applied"
    assert signed_in_page.account.user.email in page.content()
    return manage_url


def _wait_for_cached(page: Page, url: str) -> dict[str, Any]:
    """Poll the shell cache for ``url``; return its ``{found, principal}``."""
    entry: dict[str, Any] = page.evaluate(
        _WAIT_FOR_CACHED_JS, {"url": url, "timeoutMs": _CACHE_POLL_MS}
    )
    return entry


def _probe(page: Page, url: str) -> dict[str, Any]:
    """Read the shell cache for ``url`` once; return its ``{found, principal}``."""
    entry: dict[str, Any] = page.evaluate(_PROBE_JS, url)
    return entry


def _wait_for_principal(page: Page, expected: str | None) -> None:
    """Block until the worker's "signed in now" row reads ``expected``.

    ``expected`` is an account uuid, or ``None`` for the anonymous value a
    signed-out session persists.
    """
    result: dict[str, Any] = page.evaluate(
        _WAIT_FOR_PRINCIPAL_JS,
        {"expected": expected, "timeoutMs": _PRINCIPAL_POLL_MS},
    )
    assert result["settled"] is True, (
        f"mutations.principal never settled on {expected!r} within "
        f"{_PRINCIPAL_POLL_MS}ms; last value seen: {result['seen']!r}"
    )


def _sign_out(signed_in_page: SignedInPage) -> None:
    """Submit the nav's sign-out form and wait for the redirect to land.

    The form lives inside the account disclosure menu, so submitting it
    directly avoids driving the menu open — these tests are about the cache,
    not the menu. The redirect lands on the sign-in page, whose own
    ``mutation_queue.js`` reconcile is what clears the stored principal.
    """
    page = signed_in_page.page
    with page.expect_navigation(timeout=10000):
        page.evaluate(_SIGN_OUT_JS)
    page.wait_for_load_state("load")


def _navigate_offline(page: Page, url: str) -> None:
    """Navigate to ``url`` with the page-side network cut, then restore it."""
    page.context.set_offline(True)
    try:
        page.goto(url)
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)


def _assert_offline_fallback(page: Page) -> None:
    """Assert the branded ``/static/offline.html`` is what got rendered."""
    heading = page.locator("h1", has_text="This page isn't available offline")
    assert heading.count() == 1, (
        f"expected the offline fallback; the page at {page.url} "
        f"has h1s: {page.locator('h1').all_inner_texts()!r}"
    )


@pytest.mark.django_db(transaction=True)
def test_manage_page_is_cached_and_stamped_for_its_account(
    signed_in_page: SignedInPage,
) -> None:
    """The manage page enters the shell cache, stamped with its account.

    Both halves carry weight. Being cached is the offline favourites roster
    working (``tests/e2e/test_favourites_offline.py``), which is why
    ``manage_view`` is not ``@never_cache``. Carrying the stamp is what lets
    the read side refuse the entry to anyone else — and pinning the stamp
    here proves the cache probe the two refusal cases below use reaches the
    right entry, so a broken probe cannot pass silently there.
    """
    manage_url = _visit_manage(signed_in_page)

    entry = _wait_for_cached(signed_in_page.page, manage_url)

    assert entry["found"] is True, (
        f"{MANAGE_URL_PATH} never reached the shell cache — the offline "
        "favourites roster (tests/e2e/test_favourites_offline.py) reads it "
        "from there"
    )
    assert entry["principal"] == str(signed_in_page.account.uuid), (
        "the cached account page carries no usable principal stamp, so the "
        f"offline read has nothing to reject on: {entry['principal']!r}"
    )


@pytest.mark.django_db(transaction=True)
def test_offline_manage_page_is_refused_after_sign_out(
    signed_in_page: SignedInPage,
) -> None:
    """A signed-out offline navigation gets the fallback, not the old page.

    The C1 leak itself, end to end. The entry stays in the cache across the
    sign-out — asserted after the navigation — so the stamp is the only
    thing between it and the next person on the device.
    """
    email = signed_in_page.account.user.email
    account_uuid = str(signed_in_page.account.uuid)
    page = signed_in_page.page

    manage_url = _visit_manage(signed_in_page)
    cached = _wait_for_cached(page, manage_url)
    assert cached["found"] is True, f"{MANAGE_URL_PATH} never reached the shell cache"
    assert cached["principal"] == account_uuid

    _sign_out(signed_in_page)
    _wait_for_principal(page, None)

    _navigate_offline(page, manage_url)

    assert email not in page.content(), (
        "the signed-out offline navigation rendered the previous user's email"
    )
    _assert_offline_fallback(page)

    # The entry survived the sign-out, still stamped for the account that
    # loaded it — so the refusal above is the stamp check doing its job, not
    # the cache having lost the page.
    after = _probe(page, manage_url)
    assert after["found"] is True, "the entry was evicted, so the guard went untested"
    assert after["principal"] == account_uuid


@pytest.mark.django_db(transaction=True)
def test_offline_manage_page_is_refused_for_a_second_account(
    signed_in_page: SignedInPage, django_db_blocker: Any
) -> None:
    """A second account on the same device cannot read the first one's page.

    The shared-device case a sign-out does not cover: the cache holds
    account one's manage page, account two is signed in now, and the offline
    read has to refuse on the stamp rather than on there being no session at
    all.

    Account two signs in the way ``signed_in_page`` signs in account one — a
    session cookie built by Django's test client (``_session_login``), which
    replaces the first cookie on the same browser context. The online load
    of ``/`` that follows is load-bearing: it is the load on which
    ``mutation_queue.js`` reconciles ``mutations.principal`` to account two,
    and the worker reads nothing else to decide who is signed in.
    """
    first_email = signed_in_page.account.user.email
    first_uuid = str(signed_in_page.account.uuid)
    page = signed_in_page.page

    manage_url = _visit_manage(signed_in_page)
    cached = _wait_for_cached(page, manage_url)
    assert cached["found"] is True, f"{MANAGE_URL_PATH} never reached the shell cache"
    assert cached["principal"] == first_uuid

    with django_db_blocker.unblock():
        second = AccountFactory.create()
    second_uuid = str(second.uuid)
    assert second_uuid != first_uuid

    _session_login(page.context, signed_in_page.live_server_url, second.user)
    page.goto(signed_in_page.live_server_url + "/")
    page.wait_for_load_state("load")
    _wait_for_principal(page, second_uuid)

    _navigate_offline(page, manage_url)

    assert first_email not in page.content(), (
        "the second account's offline navigation rendered the first account's email"
    )
    _assert_offline_fallback(page)

    after = _probe(page, manage_url)
    assert after["found"] is True, "the entry was evicted, so the guard went untested"
    assert after["principal"] == first_uuid
