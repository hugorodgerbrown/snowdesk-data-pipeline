/*
 * static/js/pwa_client_version.js — Stamp X-Client-Version on same-origin
 * client requests (SNOW-388).
 *
 * Five server-side ``emit_server_signal`` call sites
 * (``public/api.py::version``, ``public/api.py::sw_config``,
 * ``subscriptions/push_views.py::push_test``,
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
 * LOAD ORDER IS LOAD-BEARING — THIS SCRIPT MUST LOAD LAST.
 * ``pwa_offline.js`` and ``pwa_version_check.js`` both already
 * monkey-patch ``window.fetch`` (to watch response headers, not to alter
 * requests). Because each of those wrappers captures ``window.fetch`` at
 * the time it runs and reassigns ``window.fetch`` to its own wrapper, the
 * LAST script to install a wrapper becomes the OUTERMOST one — the first
 * to see a caller's request and the last to see the real response. This
 * script must load after both of them in
 * ``public/templates/public/base.html`` so its request-mutating logic
 * runs before delegating down through their (request-preserving)
 * wrappers to the native ``fetch``. If a future refactor moves this
 * script earlier in the load order, the header may be silently dropped —
 * do not reorder without re-reading this comment.
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
