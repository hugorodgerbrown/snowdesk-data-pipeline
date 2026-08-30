/*
 * static/js/share.js — the one "hand this to someone" helper (SNOW-764).
 *
 * Publishes ``window.pwaShare`` with three functions:
 *
 *   shareOrCopy(url, title)  open the native share sheet, or fall back to
 *                            the clipboard.
 *   createShare(url, csrf)   POST to a share-minting endpoint and resolve
 *                            with the link it answers.
 *   claim(url, csrf)         POST to a claim endpoint and resolve with the
 *                            HTML fragment it answers.
 *
 * WHY THIS FILE EXISTS. ``shareOrCopy`` was written inline in
 * ``public/bulletin.html`` for SNOW-217 and has been the only sharing
 * behaviour on the site since. SNOW-764 gives a route row a Share control
 * that wants exactly the same three branches — native sheet, user
 * cancelled, platform refused — and copying thirty lines of it into
 * ``routes.js`` would leave two implementations of a fiddly API to keep in
 * step. So the helper is lifted here verbatim, the bulletin page loads it,
 * and its inline copy is gone.
 *
 * THE THREE BRANCHES, and why none of them can be dropped:
 *
 *   * the platform can share — hand it the payload and stop. Anything
 *     further would be a second action the user did not ask for.
 *   * the user CANCELLED the sheet (``AbortError``) — do nothing. Copying
 *     to the clipboard here is the classic bug: they closed the sheet
 *     because they changed their mind, and a "link copied" toast tells
 *     them the thing they just declined happened anyway.
 *   * anything else rejected — fall back to the clipboard. Chrome on
 *     desktop exposes ``navigator.share`` and may reject when the platform
 *     cannot actually show a sheet (the page is not an installed PWA), so
 *     a share that silently no-ops is the failure this branch prevents.
 *
 * ``canShare`` is consulted where it exists, because a platform that
 * already knows it cannot take this payload should not be asked. Where it
 * does not exist the call is attempted and the rejection branch above
 * catches the refusal — feature detection, never user-agent sniffing.
 *
 * NO USER-FACING STRINGS. This module writes nothing to the DOM: the
 * caller owns its own toast and reads its own copy from a strings
 * <template> (docs/i18n.md). ``title`` is passed in for the same reason —
 * it is the page's or the row's, not this module's.
 *
 * NO TELEMETRY EITHER. What is worth counting differs per surface (a
 * bulletin share and a route share are different events), so the caller
 * emits its own after the promise settles.
 *
 * Dependency-free and DOM-light by design, like i18n_strings.js: every
 * function takes plain values and the platform APIs are read off
 * ``window``/``navigator`` at call time rather than captured at parse
 * time, so a test can substitute either (tests/js/test_share.js).
 */

(function () {
  'use strict';

  /**
   * Copy a URL to the clipboard.
   *
   * Resolves either way. A clipboard write can be refused — an insecure
   * origin, a permissions policy, a browser that has no Clipboard API at
   * all — and the caller's next move is the same in every one of those
   * cases: the share did not happen, and there is nothing further to try.
   * The boolean says which, so a caller can decide whether to show its
   * "link copied" toast.
   *
   * @param {string} url The URL to copy.
   * @returns {Promise<boolean>} True when the text reached the clipboard.
   */
  function copyToClipboard(url) {
    const nav = window.navigator;
    if (!nav || !nav.clipboard || typeof nav.clipboard.writeText !== 'function') {
      return Promise.resolve(false);
    }
    return nav.clipboard
      .writeText(url)
      .then(function () {
        return true;
      })
      .catch(function () {
        return false;
      });
  }

  /**
   * Hand a URL to the native share sheet, or fall back to the clipboard.
   *
   * See this file's header for why there are three branches and why the
   * cancellation one must do nothing.
   *
   * @param {string} url The URL to share.
   * @param {string} [title] Sheet title. Defaults to the document's own.
   * @returns {Promise<string>} How it ended: ``'shared'`` (the platform
   *   took it), ``'cancelled'`` (the user dismissed the sheet),
   *   ``'copied'`` (the clipboard has it), or ``'failed'`` (neither
   *   worked). The caller decides what, if anything, to say about each.
   */
  function shareOrCopy(url, title) {
    const nav = window.navigator;
    const payload = { url: url, title: title || document.title };
    const canShare =
      nav &&
      typeof nav.share === 'function' &&
      (typeof nav.canShare !== 'function' || nav.canShare(payload));

    if (!canShare) {
      return copyToClipboard(url).then(function (copied) {
        return copied ? 'copied' : 'failed';
      });
    }

    return nav
      .share(payload)
      .then(function () {
        return 'shared';
      })
      .catch(function (err) {
        // The user closed the sheet. They declined; do not then do the
        // thing they declined by another route.
        if (err && err.name === 'AbortError') return 'cancelled';
        return copyToClipboard(url).then(function (copied) {
          return copied ? 'copied' : 'failed';
        });
      });
  }

  /**
   * POST to a share-minting endpoint and resolve with the link.
   *
   * Both of this project's minting endpoints answer the same JSON shape —
   * ``{"url": "…"}`` from ``apps.public.api.share_create`` and from
   * ``apps.routes.views.route_share_create`` — which is what lets one
   * helper serve both.
   *
   * Rejects rather than resolving with null on a non-2xx: the caller has
   * to say something different for a failure than for a success, and a
   * null that flows on into ``shareOrCopy`` would open a share sheet for
   * the string "null".
   *
   * @param {string} url The minting endpoint.
   * @param {string} csrfToken The CSRF token for the POST.
   * @returns {Promise<string>} The absolute share URL.
   */
  function createShare(url, csrfToken) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
    })
      .then(function (resp) {
        if (!resp.ok) return Promise.reject(resp);
        return resp.json();
      })
      .then(function (data) {
        if (!data || !data.url) return Promise.reject(data);
        return data.url;
      });
  }

  /**
   * POST to a claim endpoint and resolve with the fragment it answers.
   *
   * ``HX-Request`` is sent by hand because the claim endpoint is
   * ``@require_htmx`` and this is a plain fetch, exactly as
   * ``routes.js``'s upload does for ``routes:create``. CSRF rides in the
   * header plus the same-origin session cookie (fetch defaults to
   * ``credentials: 'same-origin'``).
   *
   * Rejects with the RESPONSE on a non-2xx rather than with an error, so
   * the caller can branch on ``status`` — a 409 at the route cap needs a
   * different line from a 404 on a link that has expired.
   *
   * @param {string} url The claim endpoint.
   * @param {string} csrfToken The CSRF token for the POST.
   * @returns {Promise<string>} The rendered row's HTML.
   */
  function claim(url, csrfToken) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'HX-Request': 'true',
        'X-CSRFToken': csrfToken,
      },
    }).then(function (resp) {
      if (!resp.ok) return Promise.reject(resp);
      return resp.text();
    });
  }

  // Frozen, like every other window.pwa* bridge in this tree: the surface
  // is a contract other modules read, not a namespace they extend.
  window.pwaShare = Object.freeze({
    shareOrCopy: shareOrCopy,
    createShare: createShare,
    claim: claim,
  });
}());
