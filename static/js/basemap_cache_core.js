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
 *   trimCache(cache, max, options)
 *     Trim ``cache`` down to at most ``max`` entries, oldest first
 *     (``Cache.keys()`` returns insertion order) — an LRU-by-insertion-order
 *     approximation with no per-entry timestamp bookkeeping. SNOW-1060:
 *     ``options.isEvictable`` narrows the count and the eviction to the
 *     entries it accepts (the passive basemap cache passes
 *     ``isTileEntryURL``, so the style documents are never trimmed), and
 *     ``options.maxOther`` bounds the entries it rejects.
 *   isTileEntryURL(url)
 *     SNOW-1060: whether ``url`` names a tile rather than a style document
 *     (style JSON, TileJSON, sprite, glyph range). The same definition as
 *     basemap_download_core.js's — see the comment above it here for why
 *     there are two copies.
 *   shouldPersist(url, response, immutableOnlyPaths)
 *     SNOW-526: false for a path in ``immutableOnlyPaths`` whose response
 *     doesn't carry an ``immutable`` ``Cache-Control`` token, true otherwise.
 *     Keeps the settled/unsettled date rule out of the worker entirely — the
 *     server already says which it is (``public/api.py``'s
 *     ``bulletin_groupings_geojson``), so the worker just reads the response
 *     rather than re-deriving the date arithmetic (see
 *     docs/decisions/date-aware-cache-policy.md).
 *   runPool(items, limit, worker)
 *     SNOW-568: bounded-concurrency replacement for ``Promise.all(items.map(…))``
 *     — at most ``limit`` calls to ``worker`` are ever in flight.
 *   classifyFailure(err)
 *     SNOW-568: ``'quota' | 'network' | 'other'`` for a caught warm-cache error.
 *   worseReason(a, b)
 *     SNOW-568: the more actionable of two failure reasons.
 *   responseBytes(response)
 *     SNOW-586: the byte size of a fetched ``Response`` — the
 *     ``Content-Length`` header when present (fast path, no body read),
 *     else the size of its cloned blob, else ``0`` for anything
 *     unusable. ``_warmCache`` (sw.js) sums these as it writes a pinned
 *     download so the page can record the run's on-disk size against the
 *     new standing byte budget (``planEviction`` in
 *     basemap_download_core.js) — replacing the old entry-COUNT cap,
 *     which spoke a different unit to ``DOWNLOAD_CEILING_MB``.
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

  // SNOW-1060: the tail every tile URL the basemap styles emit has, and
  // that no document they emit has — ``/{z}/{x}/{y}.{ext}``, three numeric
  // path segments and a tile extension.
  //
  // DUPLICATED in basemap_download_core.js on purpose. The pages (map.html,
  // offline.html) load the download core and never this one; only the
  // service worker loads this file, via ``importScripts``. Sharing one
  // definition would mean adding a script to the map shell, its precache
  // and the offline audit's page-dependency check — more risk than the
  // bug warranted. tests/js/test_tile_entry_parity.js runs one truth table
  // against both copies, so drift fails the build.
  //
  // Host-independent and path-only on purpose: the hosts rotate,
  // OpenFreeMap's tileset path carries a dated build id, and a tile may
  // arrive with a query string or an API key appended. What it must get
  // right is the NEAR MISSES, which all fail on the numeric triple:
  //
  //   /fonts/Noto%20Sans%20Bold/0-255.pbf   glyph range — ``0-255`` is one
  //                                         segment, not three
  //   /sprites/ofm_f384/ofm@2x.png          sprite — ``ofm@2x`` is not a number
  //   /styles/liberty                       style document — no extension
  //   /planet, /basemapvectorneu/root.json  TileJSON
  const TILE_ENTRY_PATH = /\/\d+\/\d+\/\d+\.(?:pbf|mvt|png|jpg|jpeg)$/i;

  /**
   * Whether ``url`` names a TILE rather than one of the documents that
   * draw it (SNOW-1060; the definition is SNOW-929's).
   *
   * Decided from the PATH alone, so a query string, a fragment or an API
   * key never changes the answer — see ``TILE_ENTRY_PATH`` above.
   *
   * @param {string} url A cache entry's url, absolute or relative.
   * @returns {boolean} ``false`` for a non-string, an unparseable url, or
   *   anything without the numeric-triple tail — "not provably a tile".
   */
  function isTileEntryURL(url) {
    if (typeof url !== 'string' || !url) return false;
    try {
      // A base is supplied so a relative entry still parses to a path;
      // its host is never read, and an absolute url ignores it outright.
      return TILE_ENTRY_PATH.test(new URL(url, 'https://snowdesk.info').pathname);
    } catch (_e) {
      return false;
    }
  }

  /**
   * Trim ``cache`` down to at most ``max`` entries, oldest first.
   *
   * With no ``options`` every entry counts and every entry is evictable —
   * the plain insertion-order FIFO SNOW-484 shipped.
   *
   * SNOW-1060: with ``options.isEvictable``, only the entries it accepts
   * (by url) count towards ``max``, and only they are ever deleted for it.
   * The passive basemap cache passes ``isTileEntryURL``. MapLibre fetches
   * the style JSON, TileJSON and sprite once per map load, so under a
   * whole-cache FIFO a session that panned through more than ``max`` new
   * tiles trimmed away the documents every tile depends on — nothing
   * changed on screen, but the next offline load was blank. Counting
   * tiles only keeps the documents however long the session runs.
   *
   * ``options.maxOther`` is the backstop for what that exempts. An
   * unrecognised tile format (``.webp``, say, if a provider changes its
   * style) would be classed as a document and would otherwise grow
   * without bound; when the non-evictable entries exceed ``maxOther``,
   * the oldest of them are deleted down to it. Ignored unless it is a
   * finite number.
   *
   * ``Cache.keys()`` yields ``Request`` objects; a string key (as a test
   * fake may use) is read as its own url.
   *
   * @param {Cache} cache
   * @param {number} max
   * @param {{isEvictable?: (url: string) => boolean, maxOther?: number}} [options]
   * @returns {Promise<void>}
   */
  async function trimCache(cache, max, options) {
    const keys = await cache.keys();
    const isEvictable = options && options.isEvictable;
    if (typeof isEvictable !== 'function') {
      const excess = keys.length - max;
      if (excess <= 0) return;
      await Promise.all(keys.slice(0, excess).map((key) => cache.delete(key)));
      return;
    }
    const evictable = [];
    const other = [];
    for (const key of keys) {
      const url = typeof key === 'string' ? key : key && key.url;
      (isEvictable(url) ? evictable : other).push(key);
    }
    const doomed = [];
    if (evictable.length > max) {
      doomed.push(...evictable.slice(0, evictable.length - max));
    }
    const maxOther = options.maxOther;
    if (Number.isFinite(maxOther) && other.length > maxOther) {
      doomed.push(...other.slice(0, other.length - maxOther));
    }
    if (doomed.length === 0) return;
    await Promise.all(doomed.map((key) => cache.delete(key)));
  }

  /**
   * SNOW-526: decide whether a same-origin response should be written to
   * the shell cache for offline replay.
   *
   * Only ``immutableOnlyPaths`` entries are gated — every other path keeps
   * persisting unconditionally, matching pre-SNOW-526 behaviour. For a
   * gated path, the response must declare itself ``immutable`` via its
   * ``Cache-Control`` header (case-insensitive token match, tolerant of
   * the surrounding ``public, max-age=604800, immutable`` directive list);
   * a settled-date response satisfies that, an unsettled one (still
   * ``max-age=300``, no ``immutable``) does not, so it is never written and
   * a later offline ``cache.match`` simply misses.
   *
   * @param {URL} url
   * @param {Response} response
   * @param {Set<string>} immutableOnlyPaths Same-origin paths that must
   *   only be persisted when the response is marked ``immutable``.
   * @returns {boolean}
   */
  function shouldPersist(url, response, immutableOnlyPaths) {
    if (!immutableOnlyPaths.has(url.pathname)) return true;
    const cacheControl = (response.headers.get('Cache-Control') || '').toLowerCase();
    return cacheControl
      .split(',')
      .map((token) => token.trim())
      .includes('immutable');
  }

  /**
   * SNOW-568: run ``worker`` over every entry of ``items`` with at most
   * ``limit`` calls in flight at once.
   *
   * The unbounded ``Promise.all(items.map(worker))`` this replaces issued
   * every fetch of a warm-cache run in a single tick. A full-ceiling
   * custom-area download is up to 4096 tiles (``DOWNLOAD_CEILING_MB`` /
   * ``WORST_CASE_BYTES_PER_TILE``), and Chrome's net stack starts
   * rejecting requests with ``ERR_INSUFFICIENT_RESOURCES`` well below
   * that — a request-budget exhaustion, not a storage-quota failure, so
   * every tile failed and nothing was cached.
   *
   * ``limit`` workers share one cursor rather than the list being sliced
   * into ``limit`` fixed chunks: a chunk containing several slow tiles
   * would otherwise leave the other workers idle once they had finished
   * their own. Ordering is not preserved and callers must not rely on it
   * (``_warmCache``'s only ordered output is its progress counter, which
   * counts settled URLs, not positions).
   *
   * ``worker`` is expected to handle its own failures — a rejection
   * propagates out of ``runPool`` and abandons the remaining items, which
   * is why ``_warmCache``'s worker catches everything itself.
   *
   * @param {Array<*>} items
   * @param {number} limit Maximum concurrent ``worker`` calls. Values
   *   below 1 are treated as 1 (never zero workers, which would hang).
   * @param {(item: *, index: number) => Promise<*>} worker
   * @returns {Promise<void>}
   */
  async function runPool(items, limit, worker) {
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) return;
    const width = Math.max(1, Math.min(Math.floor(limit) || 1, list.length));
    let cursor = 0;
    const drain = async () => {
      while (cursor < list.length) {
        const index = cursor;
        cursor += 1;
        await worker(list[index], index);
      }
    };
    await Promise.all(Array.from({ length: width }, drain));
  }

  /**
   * SNOW-568: classify a caught warm-cache error into the reason the user
   * is shown.
   *
   * ``quota`` is the only one with a distinct remedy (free space, or pick
   * a smaller area) and the only one that will not succeed on retry, so
   * it is separated from every other failure. ``QuotaExceededError`` is
   * matched by ``name`` rather than ``instanceof DOMException`` — the
   * error crosses no realm boundary here, but matching the name also
   * catches the (spec-sanctioned) plain-object shape some engines throw,
   * and keeps this function testable without a DOMException constructor.
   *
   * A ``fetch`` that cannot reach the network rejects with a ``TypeError``
   * — that covers offline, DNS failure, CORS rejection, and the
   * ``ERR_INSUFFICIENT_RESOURCES`` burst this ticket fixes.
   *
   * @param {*} err
   * @returns {'quota'|'network'|'other'}
   */
  function classifyFailure(err) {
    if (!err) return 'other';
    if (err.name === 'QuotaExceededError') return 'quota';
    if (err.name === 'TypeError') return 'network';
    return 'other';
  }

  // Most actionable first — worseReason keeps whichever of two reasons
  // appears earlier here, so a run that hit one quota failure among a
  // thousand network failures still tells the user about the quota.
  const REASON_PRECEDENCE = ['quota', 'network', 'other'];

  /**
   * SNOW-568: the more actionable of two failure reasons, for accumulating
   * a single reason across a whole warm-cache run.
   *
   * @param {string|null|undefined} a
   * @param {string|null|undefined} b
   * @returns {string|null} ``null`` when neither is a known reason.
   */
  function worseReason(a, b) {
    const rankA = REASON_PRECEDENCE.indexOf(a);
    const rankB = REASON_PRECEDENCE.indexOf(b);
    if (rankA === -1 && rankB === -1) return null;
    if (rankA === -1) return b;
    if (rankB === -1) return a;
    return rankA <= rankB ? a : b;
  }

  /**
   * SNOW-586: the byte size of a fetched ``Response``.
   *
   * ``Content-Length`` is read first — a fast path with no body read,
   * present on every tile/sprite/style response this project fetches —
   * falling back to the size of a cloned blob when the header is absent
   * (never the ORIGINAL response: reading its body would make it
   * unreadable to the ``cache.put`` call the caller makes with its own
   * clone). ``0`` for a falsy ``response`` or one whose blob read itself
   * throws (an already-consumed/errored body) — a caller summing these
   * across a run must never let one unreadable response poison the total
   * with ``NaN``.
   *
   * Note the header is the response's COMPRESSED size for a gzipped
   * tile, so a run's summed total slightly under-reads its true on-disk
   * size — acceptable for a standing budget, and the blob fallback (which
   * measures the decompressed bytes ``cache.put`` actually stores) is
   * exact when the header is absent.
   *
   * @param {Response} response
   * @returns {Promise<number>}
   */
  async function responseBytes(response) {
    if (!response) return 0;
    try {
      const header = response.headers && response.headers.get && response.headers.get('Content-Length');
      if (header !== null && header !== undefined) {
        const n = Number(header);
        if (Number.isFinite(n) && n >= 0) return n;
      }
    } catch (_e) {
      // Fall through to the blob fallback below.
    }
    try {
      const blob = await response.clone().blob();
      return blob.size;
    } catch (_e) {
      return 0;
    }
  }

  self.pwaBasemapCacheCore = Object.freeze({
    classifySync: classifySync,
    isBasemapOrigin: isBasemapOrigin,
    trimCache: trimCache,
    isTileEntryURL: isTileEntryURL,
    shouldPersist: shouldPersist,
    runPool: runPool,
    classifyFailure: classifyFailure,
    worseReason: worseReason,
    responseBytes: responseBytes,
  });
})();
