/*
 * static/js/pwa_offline.js — Connection-state + freshness UI (SNOW-377).
 *
 * Ships spec §3.5, §10.1, §10.2, §10.4, §10.7 non-negotiables:
 *
 *   (1) Persistent offline banner — top-anchored, revealed when
 *       ``navigator.onLine === false`` or when a fetch just failed,
 *       hidden on ``online``. Shows the freshness of the most recent
 *       successful response so the user can judge whether the cached
 *       data is trustworthy right now.
 *   (2) Freshness update — every ``X-Data-Generated-At`` header seen
 *       (fetch or HTMX) updates the banner's timestamp so the user sees
 *       the same number the page-level indicator did on last refresh.
 *   (3) Network-required buttons — any element carrying
 *       ``data-network-required`` is set ``disabled`` when offline and
 *       re-enabled when back online. Non-button elements get an
 *       ``aria-disabled="true"`` + ``pointer-events: none`` fallback.
 *
 * Two clocks (SNOW-482)
 * ----------------------
 * The banner conflated two different facts into one in-memory value:
 * when the *device last synced* with the server, and when the *server
 * generated the data* it last returned. These are now tracked and
 * persisted separately:
 *
 *   - ``syncLastAt`` — wall-clock time of the most recent successful
 *     same-origin response that did NOT carry ``X-SW-Cache: hit``
 *     (i.e. a real network round-trip, not a Cache-Storage replay
 *     served by ``static/js/sw.js``). Persisted to IndexedDB
 *     ``meta:app`` under key ``sync.last_at``.
 *   - ``freshnessLastGeneratedAt`` — newest ``X-Data-Generated-At``
 *     header seen, regardless of whether it came from the network or
 *     the cache (a cache-served response still tells the user how old
 *     the data it's showing is). Persisted under key
 *     ``freshness.last_generated_at``.
 *
 * Both are read back from ``meta:app`` on init, before the first
 * ``renderBanner`` call, so a cold offline launch shows the real
 * last-known values rather than resetting to blank.
 *
 * Qualifying requests — ``/api/*`` and other non-static-asset
 * same-origin responses (HTML partials/navigations included) — also
 * append a row to the ``log:sync`` IndexedDB store (SNOW-482), read
 * out by the manage-page sync-log panel behind the ``sync_log`` waffle
 * flag.
 *
 * Deferred to SNOW-375 / follow-ups:
 *   * Pull-to-refresh explicit-network path.
 *   * "Updated HH:MM" post-refresh toast.
 */

(function () {
  'use strict';

  const BANNER_ID = 'pwa-offline-banner';
  const NETWORK_ATTR = 'data-network-required';

  // SNOW-482: meta:app keys the two clocks are persisted under.
  const SYNC_LAST_AT_KEY = 'sync.last_at';
  const FRESHNESS_LAST_GENERATED_AT_KEY = 'freshness.last_generated_at';

  // SNOW-482: cadence at which the banner re-renders its relative
  // "last synced" phrase while shown, so it counts up live rather than
  // freezing at the value captured when the banner appeared.
  const FRESHNESS_TICK_MS = 30000;

  // Extensions treated as static-asset requests for the purposes of the
  // sync log — mirrors static/js/sw.js's STATIC_SHELL_EXTENSIONS. These
  // never represent a meaningful "sync" from the user's point of view.
  const STATIC_ASSET_EXTENSIONS = new Set([
    '.css',
    '.js',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.ico',
    '.woff',
    '.woff2',
    '.webmanifest',
  ]);

  // In-memory ledger of the two freshness clocks, hydrated from
  // IndexedDB on init (see _hydratePersistedClocks) and kept in sync
  // with the persisted copy on every qualifying response.
  let syncLastAt = null;
  let freshnessLastGeneratedAt = null;

  /**
   * Coerce a Date / ISO string to a valid Date, or null on failure so
   * callers can fall back rather than render "Invalid Date".
   *
   * @param {string | Date | null} value
   * @returns {Date | null}
   */
  function toDate(value) {
    if (!value) return null;
    const d = value instanceof Date ? value : new Date(value);
    return Number.isNaN(d.valueOf()) ? null : d;
  }

  /**
   * Format the sync clock as a naturalistic relative phrase — e.g.
   * "6 minutes ago", "2 hours ago", "now" — using the largest sensible
   * unit. Locale-aware via ``Intl.RelativeTimeFormat`` so it localises
   * for free once the UI gains other languages.
   *
   * @param {string | Date | null} value
   * @returns {string}
   */
  function formatRelative(value) {
    const d = toDate(value);
    if (!d) return '';
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
    const diffSeconds = Math.round((d.getTime() - Date.now()) / 1000);
    const units = [
      ['day', 86400],
      ['hour', 3600],
      ['minute', 60],
    ];
    for (const [unit, secs] of units) {
      if (Math.abs(diffSeconds) >= secs) {
        return rtf.format(Math.round(diffSeconds / secs), unit);
      }
    }
    return rtf.format(Math.round(diffSeconds), 'second');
  }

  /**
   * Fill the banner summary's "last synced" span with the sync clock as
   * a relative phrase, degrading to an em dash until the first sync is
   * known.
   *
   * @param {HTMLElement} banner
   * @returns {void}
   */
  function renderFreshnessCells(banner) {
    const syncedCell = banner.querySelector('[data-role="synced-at"]');
    if (syncedCell) syncedCell.textContent = formatRelative(syncLastAt) || '—';
  }

  // Re-render the "last synced" phrase on a timer while the banner is
  // shown, so an open banner counts up ("6 minutes ago" → "7 minutes
  // ago") rather than freezing. Started when revealed, cleared when
  // hidden; also self-clears if it wakes to find the banner gone.
  let freshnessTicker = null;

  /**
   * Start the freshness re-render timer, if not already running.
   *
   * @returns {void}
   */
  function startFreshnessTicker() {
    if (freshnessTicker !== null) return;
    freshnessTicker = window.setInterval(() => {
      const banner = document.getElementById(BANNER_ID);
      if (!banner || banner.classList.contains('hidden')) {
        stopFreshnessTicker();
        return;
      }
      renderFreshnessCells(banner);
    }, FRESHNESS_TICK_MS);
  }

  /**
   * Stop the freshness re-render timer, if running.
   *
   * @returns {void}
   */
  function stopFreshnessTicker() {
    if (freshnessTicker === null) return;
    window.clearInterval(freshnessTicker);
    freshnessTicker = null;
  }

  /**
   * Reveal / hide the offline banner and refresh its "last synced"
   * phrase. Idempotent — safe to call on every online/offline
   * transition. Drives the re-render ticker alongside visibility.
   *
   * @param {boolean} online
   */
  function renderBanner(online) {
    const banner = document.getElementById(BANNER_ID);
    if (!banner) return;
    if (online) {
      banner.classList.add('hidden');
      stopFreshnessTicker();
      return;
    }
    banner.classList.remove('hidden');
    renderFreshnessCells(banner);
    startFreshnessTicker();
  }

  /**
   * Toggle the disabled state of every element carrying
   * ``data-network-required``. Direct matches use the native
   * ``disabled`` property where available; matched containers (forms)
   * cascade the disabled state to their submit-capable children so a
   * ``<form data-network-required>`` blocks its Submit / Add / Delete
   * buttons without having to tag each one individually.
   *
   * Non-disable-able elements (``<a>``, ``<div>``) fall back to
   * ``aria-disabled="true"`` + ``pointer-events: none``.
   *
   * @param {boolean} online
   */
  function syncNetworkRequired(online) {
    const nodes = document.querySelectorAll(`[${NETWORK_ATTR}]`);
    nodes.forEach((node) => applyDisabled(node, !online));
    document
      .querySelectorAll(
        `[${NETWORK_ATTR}] button, [${NETWORK_ATTR}] input[type="submit"], [${NETWORK_ATTR}] input[type="button"]`,
      )
      .forEach((child) => applyDisabled(child, !online));
  }

  /**
   * Apply / remove the disabled state on a single element without
   * clobbering a pre-existing ``disabled`` attribute set for other
   * reasons (form validity, etc.). We tag our own mutations with
   * ``data-was-disabled-offline`` so the reverse transition only
   * un-disables what we disabled.
   *
   * @param {Element} node
   * @param {boolean} disabled
   */
  function applyDisabled(node, disabled) {
    if (disabled) {
      if ('disabled' in node && !node.disabled) {
        node.disabled = true;
        node.setAttribute('data-was-disabled-offline', '1');
      }
      node.setAttribute('aria-disabled', 'true');
      node.style.pointerEvents = 'none';
    } else {
      if (
        'disabled' in node &&
        node.getAttribute('data-was-disabled-offline') === '1'
      ) {
        node.disabled = false;
      }
      node.removeAttribute('aria-disabled');
      node.style.removeProperty('pointer-events');
      node.removeAttribute('data-was-disabled-offline');
    }
  }

  /**
   * Best-effort persistence of one ``meta:app`` key. Never throws —
   * IndexedDB unavailability (private mode, Reset Required) must not
   * break the in-memory banner update.
   *
   * @param {string} key
   * @param {string} value
   */
  function persistMeta(key, value) {
    if (!window.pwaDb || typeof window.pwaDb.put !== 'function') return;
    try {
      window.pwaDb.put('meta:app', { key, value }).catch(() => {});
    } catch (_err) {
      // Ignore — persistence is best-effort.
    }
  }

  /**
   * Whether a same-origin pathname is a static-asset request that
   * should never be recorded as a "sync" (CSS/JS/images/fonts/the
   * webmanifest, and anything under ``/static/``).
   *
   * @param {string} pathname
   * @returns {boolean}
   */
  function isStaticAssetPath(pathname) {
    if (pathname.startsWith('/static/')) return true;
    const dot = pathname.lastIndexOf('.');
    if (dot === -1) return false;
    return STATIC_ASSET_EXTENSIONS.has(pathname.slice(dot).toLowerCase());
  }

  /**
   * Best-effort append to the ``log:sync`` IndexedDB store. Never
   * throws.
   *
   * @param {Date} at
   * @param {string} pathname
   */
  function appendSyncLogEntry(at, pathname) {
    if (!window.pwaDb || typeof window.pwaDb.appendSyncLog !== 'function') return;
    try {
      window.pwaDb.appendSyncLog({ at: at.toISOString(), path: pathname }).catch(() => {});
    } catch (_err) {
      // Ignore — the sync log is a diagnostic nice-to-have.
    }
  }

  /**
   * Called whenever a response header set + resolved URL is available
   * (fetch or HTMX). Reads ``X-SW-Cache`` to tell a Cache-Storage
   * replay apart from a real server round-trip: only an un-stamped
   * same-origin response advances ``syncLastAt`` / persists
   * ``sync.last_at`` / appends a ``log:sync`` row (for qualifying,
   * non-static-asset paths). ``X-Data-Generated-At`` is always
   * absorbed regardless of cache-hit status — a cache-served response
   * still tells the user how old the data it's showing is.
   *
   * @param {(name: string) => string | null} getHeader
   * @param {string} responseUrl
   */
  function absorbFreshness(getHeader, responseUrl) {
    const now = new Date();
    const cacheHit = !!getHeader('X-SW-Cache');

    let sameOrigin = false;
    let pathname = '';
    try {
      const parsed = new URL(responseUrl, window.location.href);
      sameOrigin = parsed.origin === window.location.origin;
      pathname = parsed.pathname;
    } catch (_err) {
      sameOrigin = false;
    }

    if (!cacheHit && sameOrigin) {
      syncLastAt = now;
      persistMeta(SYNC_LAST_AT_KEY, now.toISOString());
      if (!isStaticAssetPath(pathname)) {
        appendSyncLogEntry(now, pathname);
      }
    }

    const generated = getHeader('X-Data-Generated-At');
    if (generated) {
      const parsed = new Date(generated);
      if (!Number.isNaN(parsed.valueOf())) {
        freshnessLastGeneratedAt = parsed;
        persistMeta(FRESHNESS_LAST_GENERATED_AT_KEY, parsed.toISOString());
      }
    }

    // If the banner is already open, refresh its label live so the user
    // sees the clocks update when a fresh(er) response arrives.
    if (!navigator.onLine) renderBanner(false);
  }

  /**
   * Wrap ``window.fetch`` so every response participates in the
   * freshness ledger and every network failure flips the banner.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const original = window.fetch.bind(window);
    window.fetch = async function (...args) {
      try {
        const response = await original(...args);
        try {
          absorbFreshness((name) => response.headers.get(name), response.url);
        } catch (_err) {
          // Never break the caller on a header quirk.
        }
        return response;
      } catch (err) {
        // Network failure — reveal the banner. Rethrow so callers can
        // still handle the failure themselves.
        renderBanner(false);
        throw err;
      }
    };
  }

  /**
   * Hook the HTMX post-response event so XHR-driven traffic also feeds
   * the freshness ledger, and the ``htmx:sendError`` event so a failed
   * mutation surfaces the banner immediately.
   */
  function wrapHtmx() {
    document.body?.addEventListener('htmx:afterOnLoad', (evt) => {
      const xhr = evt?.detail?.xhr;
      if (!xhr || typeof xhr.getResponseHeader !== 'function') return;
      try {
        absorbFreshness((name) => xhr.getResponseHeader(name), xhr.responseURL || '');
      } catch (_err) {
        // Ignore.
      }
    });
    document.body?.addEventListener('htmx:sendError', () => {
      renderBanner(false);
    });
  }

  /**
   * Bind ``online`` / ``offline`` events on window so the banner and
   * network-required elements track the connection state without
   * requiring a page reload.
   */
  function bindConnectionEvents() {
    window.addEventListener('online', () => {
      renderBanner(true);
      syncNetworkRequired(true);
    });
    window.addEventListener('offline', () => {
      renderBanner(false);
      syncNetworkRequired(false);
    });
  }

  /**
   * SNOW-482: read both persisted clocks back from ``meta:app`` before
   * the first ``renderBanner`` call, so a cold offline launch shows the
   * real last-known values instead of resetting to blank. Guarded on
   * ``window.pwaDb`` presence (it loads before this script — see
   * ``base.html``) and never throws — a read failure just leaves the
   * clocks unset, same as before this ticket.
   */
  async function hydratePersistedClocks() {
    if (!window.pwaDb || typeof window.pwaDb.get !== 'function') return;
    try {
      const [syncRow, freshnessRow] = await Promise.all([
        window.pwaDb.get('meta:app', SYNC_LAST_AT_KEY),
        window.pwaDb.get('meta:app', FRESHNESS_LAST_GENERATED_AT_KEY),
      ]);
      if (syncRow && syncRow.value) {
        const parsed = new Date(syncRow.value);
        if (!Number.isNaN(parsed.valueOf())) syncLastAt = parsed;
      }
      if (freshnessRow && freshnessRow.value) {
        const parsed = new Date(freshnessRow.value);
        if (!Number.isNaN(parsed.valueOf())) freshnessLastGeneratedAt = parsed;
      }
    } catch (_err) {
      // Best-effort — the banner falls back to "no data yet" copy.
    }
  }

  /**
   * Prime the initial state. If the page loaded while offline (unlikely
   * via the browser — offline navigations normally show the browser's
   * own error page — but possible via the SW cache), we want the banner
   * up immediately, showing persisted clocks rather than blanks.
   */
  async function init() {
    bindConnectionEvents();
    wrapFetch();
    wrapHtmx();
    await hydratePersistedClocks();
    renderBanner(navigator.onLine);
    syncNetworkRequired(navigator.onLine);
  }

  init();
})();
