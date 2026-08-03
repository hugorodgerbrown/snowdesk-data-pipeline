"""
tests/e2e/test_offline_account_principal.py — C1 principal guard on the
shell cache (``docs/code-reviews/2026-08-03-js-review.md``).

``_networkFirst`` (``static/js/sw.js``) used to write every 2xx same-origin
navigation into the shell cache with nothing recording who it was rendered
for, and sign-out purged neither the cache nor the session's trace in it.
So: sign in, visit ``/account/manage/``, sign out, go offline, navigate
back — and the worker replayed the previous user's rendered page, email
address included.

Two guards ship for it, one test each:

  * ``manage_view`` is ``@never_cache`` (``apps/accounts/views.py``) and
    ``_networkFirst`` skips ``cache.put`` for a ``Cache-Control: no-store``
    response, so the page never enters the cache at all.
  * Every cached navigation carries an ``X-SW-Principal`` header naming the
    account its HTML was rendered for, and the offline read serves an entry
    only to that same principal.

Both tests assert Cache-Storage membership directly rather than inferring
it from a network call that fails. ``page.context.set_offline(True)``
governs page-side network only — a request the service worker handles is
re-issued by the worker's own ``fetch()``, which reaches the live server
regardless of the offline flag, so "the fetch failed" proves nothing about
what is in the cache. The absence check polls before concluding, because
the cache write is fire-and-forget, and it runs alongside a positive
control (``/`` IS cached in the same session) so a broken probe or a wrong
URL cannot pass silently.

The serving path itself — a cached entry rejected because its stamp does
not match the principal signed in now — is covered at unit level in
``tests/js/test_sw.js``, where the worker's ``fetch`` can genuinely be made
to fail.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.conftest import SignedInPage

# How long to keep polling before concluding a URL is absent from the shell
# cache. ``_networkFirst``'s write is not awaited, so an immediate check can
# pass just by being early.
_ABSENCE_POLL_MS = 1500

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

# Polls for the whole window, failing the moment the URL turns up.
_ASSERT_NEVER_CACHED_JS = """async ({ url, timeoutMs }) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const keys = (await caches.keys()).filter((k) => k.startsWith('snowdesk-shell-'));
    for (const key of keys) {
      const cache = await caches.open(key);
      if (await cache.match(url)) return { found: true, cacheKey: key };
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  return { found: false, cacheKey: null };
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


def test_account_page_never_enters_the_shell_cache(
    signed_in_page: SignedInPage,
) -> None:
    """``/account/manage/`` is never cached, so an offline visit cannot leak it.

    The positive control (``/`` cached in the same session) is what makes
    the absence assertion mean something: without it a broken probe or a
    worker that stopped caching entirely would pass.
    """
    page = signed_in_page.page
    email = signed_in_page.account.user.email
    manage_url = signed_in_page.live_server_url + "/account/manage/"

    page.goto(manage_url)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url
    assert email in page.content()

    # Positive control: the home shell IS cached under the same worker.
    page.goto(signed_in_page.live_server_url + "/")
    page.wait_for_load_state("load")
    control: dict[str, Any] = page.evaluate(
        _WAIT_FOR_CACHED_JS,
        {"url": signed_in_page.live_server_url + "/", "timeoutMs": _ABSENCE_POLL_MS},
    )
    assert control["found"] is True, (
        "positive control failed — the shell cache holds no entry for /, so the "
        "absence assertion below would pass for the wrong reason"
    )

    absent: dict[str, Any] = page.evaluate(
        _ASSERT_NEVER_CACHED_JS,
        {"url": manage_url, "timeoutMs": _ABSENCE_POLL_MS},
    )
    assert absent["found"] is False, (
        f"/account/manage/ was written to the shell cache ({absent['cacheKey']}) "
        "despite Cache-Control: no-store"
    )

    _sign_out(signed_in_page)

    page.context.set_offline(True)
    try:
        page.goto(manage_url)
        page.wait_for_load_state("load")
    finally:
        page.context.set_offline(False)

    assert email not in page.content(), (
        "the signed-out offline navigation rendered the previous user's email"
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
        _WAIT_FOR_CACHED_JS, {"url": home_url, "timeoutMs": _ABSENCE_POLL_MS}
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
