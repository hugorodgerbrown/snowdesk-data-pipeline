"""
tests/e2e/test_offline_account_principal.py — C1 principal guard on the
shell cache (``docs/code-reviews/2026-08-03-js-review.md``).

``_networkFirst`` (``static/js/sw.js``) used to write every 2xx same-origin
navigation into the shell cache with nothing recording who it was rendered
for, and sign-out purged neither the cache nor the session's trace in it.
So: sign in, visit ``/account/manage/``, sign out, go offline, navigate
back — and the worker replayed the previous user's rendered page, email
address included.

The guard that ships for it is partitioning, not avoidance: every cached
navigation carries an ``X-SW-Principal`` header naming the account its HTML
was rendered for, and the offline read serves an entry only to the
principal signed in now.

``manage_view`` is deliberately NOT ``@never_cache``, even though it is the
page the leak was found on. The offline favourites roster
(``tests/e2e/test_favourites_offline.py``) repaints from the manage page
served out of the shell cache, so a ``no-store`` response would take a
shipped feature offline with it. ``change_email_view`` does carry
``@never_cache`` — nothing needs that one offline.

Both tests assert Cache-Storage membership directly rather than inferring
it from a network call that fails. ``page.context.set_offline(True)``
governs page-side network only — a request the service worker handles is
re-issued by the worker's own ``fetch()``, which reaches the live server
regardless of the offline flag, so "the fetch failed" proves nothing about
what is in the cache. The membership checks poll before concluding, because
the cache write is fire-and-forget.

That same limitation is why the serving path — a cached entry rejected
because its stamp does not match the principal signed in now — is covered
at unit level in ``tests/js/test_sw.js``, where the worker's ``fetch`` can
genuinely be made to fail. What these tests pin end to end are the two
values that check compares: the stamp written onto the entry, and the
principal the worker reads back after a sign-out.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.conftest import SignedInPage

# How long to keep polling for a URL to appear in the shell cache.
# ``_networkFirst``'s write is not awaited, so an immediate check can fail
# just by being early.
_CACHE_POLL_MS = 1500

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

# Reads ``mutations.principal`` out of ``meta:app`` — the one row
# ``sw.js::_currentPrincipal`` consults for "who is signed in now", written
# by the page's own ``_reconcilePrincipal()`` (static/js/mutation_queue.js).
# Returns null both when the row is absent and when it holds an empty value,
# which is how the worker reads an anonymous session.
_CURRENT_PRINCIPAL_JS = """async () => {
  if (typeof window.pwaDb !== 'object') return null;
  const rows = await window.pwaDb.getAll('meta:app');
  const row = rows.find((r) => r.key === 'mutations.principal');
  if (!row || row.value === null || row.value === undefined || row.value === '') {
    return null;
  }
  return String(row.value);
}"""

_SIGN_OUT_JS = """() => {
  const form = document.querySelector('form[action$="/account/sign-out/"]');
  if (!form) throw new Error('sign-out form not found in the nav');
  form.submit();
}"""


def _sign_out(signed_in_page: SignedInPage) -> None:
    """Submit the nav's sign-out form and wait for the redirect to land.

    The form lives inside the account disclosure menu, so submitting it
    directly avoids driving the menu open — this test is about the cache,
    not the menu.
    """
    page = signed_in_page.page
    with page.expect_navigation(timeout=10000):
        page.evaluate(_SIGN_OUT_JS)
    page.wait_for_load_state("load")


def test_account_page_is_cached_but_only_for_its_own_principal(
    signed_in_page: SignedInPage,
) -> None:
    """``/account/manage/`` is cached, stamped, and unmatchable after sign-out.

    The page stays cacheable on purpose — the offline favourites roster is
    built on it — so what stops the leak is the stamp. This pins the two
    values ``_networkFirst``'s offline read compares: the entry carries the
    signed-in account's uuid, and after a sign-out the principal the worker
    reads back is no longer that uuid, so no comparison can succeed.

    The rejection itself is unit-covered (``tests/js/test_sw.js``); it
    cannot be driven from here, because the worker re-issues the request off
    its own thread and ``set_offline`` does not reach it.
    """
    page = signed_in_page.page
    email = signed_in_page.account.user.email
    manage_url = signed_in_page.live_server_url + "/account/manage/"

    page.goto(manage_url)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url
    assert email in page.content()

    cached: dict[str, Any] = page.evaluate(
        _WAIT_FOR_CACHED_JS, {"url": manage_url, "timeoutMs": _CACHE_POLL_MS}
    )
    assert cached["found"] is True, (
        "/account/manage/ never reached the shell cache — the offline "
        "favourites roster (tests/e2e/test_favourites_offline.py) reads it "
        "from there"
    )
    assert cached["principal"] == str(signed_in_page.account.uuid), (
        "the cached account page carries no usable principal stamp, so the "
        "offline read has nothing to reject a different user against"
    )

    _sign_out(signed_in_page)

    assert page.evaluate(_CURRENT_PRINCIPAL_JS) != str(signed_in_page.account.uuid), (
        "the signed-out session still reports the previous account as the "
        "current principal, so the stamped entry would still match"
    )


def test_cached_navigation_is_stamped_with_its_principal(
    signed_in_page: SignedInPage,
) -> None:
    """A cached page carries the account its HTML was rendered for.

    Covers the write half of the guard end to end: the stamp comes from the
    response's own ``<meta name="pwa-user-id">``, so it tracks the session
    the HTML was rendered under — including back to ``anonymous`` after a
    sign-out, which is what makes the read-side rejection possible.
    """
    page = signed_in_page.page
    home_url = signed_in_page.live_server_url + "/"

    # The fixture's own first visit to / happened before the session cookie
    # was attached, so re-navigate to get a signed-in entry written.
    page.goto(home_url)
    page.wait_for_load_state("load")
    signed_in: dict[str, Any] = page.evaluate(
        _WAIT_FOR_CACHED_JS, {"url": home_url, "timeoutMs": _CACHE_POLL_MS}
    )
    assert signed_in["found"] is True
    assert signed_in["principal"] == str(signed_in_page.account.uuid)

    _sign_out(signed_in_page)

    page.goto(home_url)
    page.wait_for_load_state("load")
    page.wait_for_function(
        """(url) => caches.keys()
             .then((keys) => keys.filter((k) => k.startsWith('snowdesk-shell-')))
             .then(async (keys) => {
               for (const key of keys) {
                 const cache = await caches.open(key);
                 const hit = await cache.match(url);
                 if (hit && hit.headers.get('X-SW-Principal') === 'anonymous') return true;
               }
               return false;
             })""",
        arg=home_url,
        timeout=5000,
    )

    signed_out: dict[str, Any] = page.evaluate(_PROBE_JS, home_url)
    assert signed_out["principal"] == "anonymous"
