/*
 * static/js/pwa_client_version.js — Stamp X-Client-Version on same-origin
 * client requests (SNOW-388).
 *
 * Four server-side ``emit_server_signal`` call sites (five signals — push
 * emits both ``.sent`` and ``.gone_410`` from one call site)
 * (``public/api.py::version``, ``public/api.py::sw_config``,
 * ``accounts/push_views.py::push_test``,
 * ``core/idempotency.py::IdempotencyMiddleware``) read ``client_version``
 * from the ``X-Client-Version`` request header. Until this script, no
 * client sent it, so the property landed in PostHog as ``""``. This file
 * is the client-side half: it stamps the header on every same-origin
 * request the page issues, whether via ``fetch`` or HTMX.
 *
 * ONE CENTRAL WRAPPER, NOT ~25 CALL-SITE EDITS. The frontend makes
 * same-origin ``fetch()`` calls from ``sw_register.js``, ``telemetry.js``,
 * ``map.js``, ``map_edit_resorts.js``, ``push_demo.js``, ``passkey.js``,
 * and every HTMX-driven partial swap. Editing each call site individually
 * would be repetitive and easy to miss on new additions — a single
 * monkey-patch plus one HTMX listener covers all of them.
 *
 * LOAD ORDER IS LOAD-BEARING — THIS SCRIPT MUST LOAD FIRST (right after
 * ``db.js``, before ``sw_register.js``, ``telemetry.js``,
 * ``mutation_queue.js``, ``pwa_version_check.js``, and ``pwa_offline.js``).
 *
 * ``sw_register.js`` dispatches a ``fetch('/api/sw-config')`` SYNCHRONOUSLY
 * at its own IIFE-load (``fetchSwConfig()``, called immediately, not from
 * an event handler) — there is no user interaction or async tick between
 * script-parse and that call. Whichever wrapper is installed on
 * ``window.fetch`` at that moment is the one this pre-register fetch goes
 * through; any wrapper installed AFTER ``sw_register.js`` has executed
 * simply never sees it, so this script must have patched ``window.fetch``
 * before that script's script tag runs.
 *
 * Because each wrapper captures ``window.fetch`` at the time it runs and
 * reassigns ``window.fetch`` to its own version, the FIRST script to
 * install a wrapper becomes the INNERMOST one — the last to see a
 * caller's request before it reaches the native ``fetch``, and the first
 * to see the real response on the way back out. Loading first therefore
 * means every later wrapper's calls to "the current ``window.fetch``"
 * resolve, through the composed chain, down to this one — so our header
 * injection still runs on every request, including those issued by
 * scripts that load after us.
 *
 * This composition is only safe because ``pwa_offline.js`` and
 * ``pwa_version_check.js`` (both of which patch ``window.fetch`` after
 * this script) are PURE RESPONSE-SIDE OBSERVERS — they read response
 * headers and rethrow/return the response unchanged; neither reads nor
 * mutates the outbound request or its headers. If a future script needs
 * to mutate the outbound request (add/remove a header, rewrite the URL),
 * it must load BEFORE this one, or it will silently lose to whatever
 * ``fetch(..)`` call this wrapper already dispatched downstream. Do not
 * reorder without re-reading this comment and re-auditing every wrapper
 * between ``db.js`` and this script's tag in
 * ``public/templates/public/base.html``.
 *
 * SCOPE — deliberately excluded:
 *   * Service-worker-side fetch wrapping (``sw.js``) — every signal this
 *     ticket targets fires from a JS-initiated request already covered
 *     by this page-side wrapper; ``sw.js`` passes ``event.request``
 *     through unchanged, so headers set here survive into the network
 *     unmodified.
 *   * ``navigator.sendBeacon`` — cannot carry custom headers by design.
 *     ``telemetry.js``'s critical-event beacon path already carries
 *     ``client_version`` inside the envelope body instead.
 *   * Third-party requests (MapLibre tile/style URLs, basemap fetches) —
 *     filtered out by the same-origin check below.
 *
 * Public surface: none. This is a side-effecting IIFE, matching
 * ``pwa_offline.js`` / ``pwa_version_check.js``.
 */

(function () {
  'use strict';

  const HEADER = 'X-Client-Version';

  const CLIENT_VERSION = (() => {
    const el = document.querySelector('meta[name="pwa-app-version"]');
    return el ? (el.getAttribute('content') || '').trim() : '';
  })();

  // No version baked into the shell (stale template, or the meta tag was
  // removed) — nothing meaningful to add. Matches the fail-safe pattern
  // in pwa_version_check.js's own meta-tag guard.
  if (!CLIENT_VERSION) return;

  /**
   * True when ``url`` resolves to the same origin as the current page.
   * Relative URLs (the common case — HTMX paths, same-origin fetches)
   * always pass; a malformed absolute URL fails closed (treated as
   * cross-origin, so it is left untouched).
   *
   * @param {string} url
   * @returns {boolean}
   */
  function isSameOrigin(url) {
    try {
      return new URL(url, location.href).origin === location.origin;
    } catch (_err) {
      return false;
    }
  }

  /**
   * Wrap ``window.fetch`` so every same-origin call carries
   * ``X-Client-Version``. Handles both call shapes — ``fetch(url, init)``
   * and ``fetch(request)`` — without clobbering a header a caller already
   * set explicitly. ``Request.headers`` is immutable, so the ``Request``
   * shape is rebuilt via ``new Request(orig, { headers })`` rather than
   * mutated in place.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (resource, init) {
      if (resource instanceof Request) {
        if (isSameOrigin(resource.url) && !resource.headers.has(HEADER)) {
          const headers = new Headers(resource.headers);
          headers.set(HEADER, CLIENT_VERSION);
          // Assumption: no Snowdesk call site does fetch(requestObj, init)
          // with its own init.headers — per the fetch spec, a second-arg
          // init.headers would take priority over the rebuilt Request's
          // headers and silently drop our injection. Revisit if that shape
          // is ever introduced.
          return nativeFetch(new Request(resource, { headers }), init);
        }
        return nativeFetch(resource, init);
      }
      if (isSameOrigin(resource)) {
        const headers = new Headers((init && init.headers) || {});
        if (!headers.has(HEADER)) headers.set(HEADER, CLIENT_VERSION);
        return nativeFetch(resource, Object.assign({}, init, { headers }));
      }
      return nativeFetch(resource, init);
    };
  }

  /**
   * Stamp same-origin HTMX requests via ``htmx:configRequest`` — HTMX
   * issues its own ``XMLHttpRequest`` rather than calling ``fetch``, so
   * the wrapper above never sees these.
   *
   * @param {CustomEvent} evt
   */
  function onHtmxConfigRequest(evt) {
    const path = evt.detail && evt.detail.path;
    if (!path || !isSameOrigin(path)) return;
    if (!evt.detail.headers[HEADER]) evt.detail.headers[HEADER] = CLIENT_VERSION;
  }

  wrapFetch();
  document.body?.addEventListener('htmx:configRequest', onHtmxConfigRequest);
})();
