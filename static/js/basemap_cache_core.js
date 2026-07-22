/*
 * static/js/basemap_cache_core.js — Pure/cache-injected service-worker
 * caching helpers (SNOW-496).
 *
 * Extracted from static/js/sw.js's ``_classifySync``, the origin-check half
 * of ``_classifyCrossOriginGet``, and ``_trimCache`` so they can be
 * unit-tested directly (see tests/js/test_basemap_cache_core.js) — a real
 * service worker can only be driven end-to-end in Playwright, which makes
 * this classification/eviction logic slow and indirect to exercise there.
 * ``sw.js`` itself is otherwise unchanged: each extracted definition is
 * replaced with a thin local delegator (with an inline literal fallback,
 * mirroring the existing ``self.pwaMutationQueueCore ||`` idiom) that
 * forwards the same closure values here — every call site and caching
 * decision is untouched.
 *
 * Attached to ``self`` (not ``window``) — this file is loaded via
 * ``importScripts`` from a service worker, which has no ``window``.
 *
 * Deliberately dependency-free / side-effect-free: every export is a pure
 * function of its arguments (``trimCache`` mutates the ``Cache`` object it's
 * handed, but takes no implicit state) — no ``self.location``,
 * ``_basemapOrigins``, or IndexedDB reads happen in this file; those stay in
 * sw.js's own closures and are passed in explicitly.
 *
 * Public API — attached to ``self.pwaBasemapCacheCore``:
 *
 *   classifySync(request, url, selfOrigin, staticPaths, staticShellExtensions)
 *     Synchronous portion of fetch-strategy classification: the
 *     ``method !== GET`` short-circuit, and every same-origin case.
 *     Returns ``'static' | 'navigate' | 'network' | null`` — ``null`` means
 *     "a cross-origin GET; the caller must decide separately" (deciding that
 *     case needs the async basemap-origin hydration, which stays in sw.js).
 *   isBasemapOrigin(url, originsSet)
 *     True when ``url.origin`` is a member of ``originsSet`` — the allowlist
 *     check half of ``_classifyCrossOriginGet``; the async hydration that
 *     populates ``originsSet`` stays in sw.js.
 *   trimCache(cache, max)
 *     Trim ``cache`` down to at most ``max`` entries, oldest first
 *     (``Cache.keys()`` returns insertion order) — an LRU-by-insertion-order
 *     approximation with no per-entry timestamp bookkeeping.
 */

(function () {
  'use strict';

  /**
   * Synchronous portion of fetch-strategy classification.
   *
   * @param {Request} request
   * @param {URL} url
   * @param {string} selfOrigin The worker's own origin (``self.location.origin``).
   * @param {Set<string>} staticPaths Same-origin paths safe to serve
   *   stale-while-revalidate regardless of extension.
   * @param {Set<string>} staticShellExtensions Same-origin file extensions
   *   (lowercased, with leading dot) that count as static shell assets.
   * @returns {'static'|'navigate'|'network'|null} ``null`` means "cross-origin
   *   GET — the caller must decide separately".
   */
  function classifySync(request, url, selfOrigin, staticPaths, staticShellExtensions) {
    if (request.method !== 'GET') return 'network';
    if (url.origin !== selfOrigin) return null;

    if (request.mode === 'navigate' || request.destination === 'document') {
      return 'navigate';
    }

    if (staticPaths.has(url.pathname)) return 'static';

    const dot = url.pathname.lastIndexOf('.');
    if (dot !== -1) {
      const ext = url.pathname.slice(dot).toLowerCase();
      if (staticShellExtensions.has(ext)) return 'static';
    }

    return 'network';
  }

  /**
   * True when ``url.origin`` is a member of ``originsSet``.
   *
   * @param {URL} url
   * @param {Set<string>} originsSet
   * @returns {boolean}
   */
  function isBasemapOrigin(url, originsSet) {
    return originsSet.has(url.origin);
  }

  /**
   * Trim ``cache`` down to at most ``max`` entries, oldest first.
   *
   * @param {Cache} cache
   * @param {number} max
   * @returns {Promise<void>}
   */
  async function trimCache(cache, max) {
    const keys = await cache.keys();
    const excess = keys.length - max;
    if (excess <= 0) return;
    await Promise.all(keys.slice(0, excess).map((key) => cache.delete(key)));
  }

  self.pwaBasemapCacheCore = Object.freeze({
    classifySync: classifySync,
    isBasemapOrigin: isBasemapOrigin,
    trimCache: trimCache,
  });
})();
