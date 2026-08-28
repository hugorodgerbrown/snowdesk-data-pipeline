/*
 * static/js/pwa_offline.js — Connection-state + freshness UI (SNOW-377).
 *
 * Ships spec §3.5, §10.1, §10.2, §10.4, §10.7 non-negotiables:
 *
 *   (1) Persistent offline banner — top-anchored, revealed when
 *       ``navigator.onLine === false`` or when a fetch just failed
 *       (an ``AbortError`` is a caller cancelling its own request, not
 *       a connectivity failure, so it is excluded), hidden on
 *       ``online`` and on the next successful same-origin response
 *       received while ``navigator.onLine`` is true. Shows the
 *       freshness of the most recent successful response so the user
 *       can judge whether the cached data is trustworthy right now.
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
 *   - ``syncLastAt`` — wall-clock time of the most recent *successful*
 *     (2xx) same-origin response that is neither cache-served
 *     (``X-SW-Cache`` absent) nor a synthesized service-worker fallback
 *     (a non-empty resolved URL — see SNOW-490: an offline cache miss
 *     in ``static/js/sw.js`` resolves to a synthesized 504 with
 *     ``url === ''``, which must not be mistaken for a real
 *     round-trip). Persisted to IndexedDB ``meta:app`` under key
 *     ``sync.last_at``.
 * It is read back from ``meta:app`` on init, before the first
 * ``renderBanner`` call, so a cold offline launch shows the real
 * last-known value rather than resetting to blank.
 *
 * SNOW-615: there was a second clock here, ``freshnessLastGeneratedAt``,
 * tracking the newest ``X-Data-Generated-At`` header. It was declared,
 * assigned on every qualifying response, persisted, and hydrated on every
 * page boot — and never read. The banner has one ``data-role="synced-at"``
 * cell, filled from the clock above; the template's own comment said so
 * while this header claimed both reached the UI. Deleted rather than
 * wired in: nothing had asked for a second timestamp in three tickets'
 * worth of banner work, and a write-only clock costs a put per response
 * and a read per boot to misdirect the next reader.
 *
 * Qualifying requests — ``/api/*`` and other non-static-asset
 * same-origin responses (HTML partials/navigations included) — also
 * append a row to the ``log:sync`` IndexedDB store (SNOW-482), read
 * out by the manage-page sync-log panel behind the ``sync_log`` waffle
 * flag.
 *
 * Network mode (SNOW-742)
 * ------------------------
 * The banner used to key entirely off ``navigator.onLine`` plus "did a fetch
 * just fail". Neither can see the state this ticket added: the service worker
 * has LATCHED offline — stopped calling the network at all after three
 * consecutive read timeouts — while ``navigator.onLine`` is still true. That
 * combination is the Underground exactly: the radio is attached, so the
 * platform reports online; there is no route, so nothing completes.
 *
 * So the banner has two states rather than one, and they make different
 * promises. "Offline — last synced" means requests are still going out and the
 * app will update as soon as one lands. "Offline mode — last synced" means it
 * has stopped asking and is serving downloaded data only. Showing the first
 * while the second is true would be a lie about avalanche data, which is why
 * the reveal rule is ``!online || latched`` rather than ``!online``.
 *
 * The worker owns the mode; ``networkMode`` here is a mirror, kept in step by
 * the ``network-mode`` message in both directions. It is persisted to
 * ``meta:app`` under ``network.mode`` and re-asserted to the worker on boot —
 * a worker terminated while idle comes back in ``'auto'`` having forgotten the
 * latch, and re-asserting is what restores it without putting an IndexedDB
 * read on the worker's own fetch path.
 *
 * Every user-facing string for both states is rendered by
 * ``includes/_offline_banner.html`` and toggled here by ``hidden``. Setting the
 * text from JavaScript would ship English to every locale — ``makemessages``
 * never scans ``static/js`` — which is what ``bin/i18n-lint`` fails on.
 *
 * Deferred to SNOW-375 / follow-ups:
 *   * Pull-to-refresh explicit-network path.
 *   * "Updated HH:MM" post-refresh toast.
 */

(function () {
  'use strict';

  const BANNER_ID = 'pwa-offline-banner';
  const NETWORK_ATTR = 'data-network-required';

  // SNOW-742: the meta:app key the network mode is persisted under, and the
  // mode itself as this page last heard it from the worker.
  //
  // The worker owns the mode; this is a mirror, for two jobs. It decides which
  // banner variant to show, and it is re-asserted to the worker on boot — a
  // worker that was terminated while idle comes back in 'auto' with no memory
  // of the latch, and re-asserting is what restores it without putting an
  // IndexedDB read on the worker's fetch path. See sw.js's
  // ``_publishNetworkMode``.
  const NETWORK_MODE_KEY = 'network.mode';
  let networkMode = 'auto';

  // SNOW-482: the meta:app key the last-sync clock is persisted under.
  // A sibling ``freshness.last_generated_at`` key went with the write-only
  // clock SNOW-615 removed. Rows already written under it on devices in
  // the field are simply never read again — harmless, and cheaper than a
  // migration to delete one small row.
  const SYNC_LAST_AT_KEY = 'sync.last_at';

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

  // Same-origin paths that are the page talking to itself rather than
  // fetching anything the user asked for, so they never belong in the
  // sync log. static/js/telemetry.js flushes its buffer on a 30s cadence
  // and on every lifecycle event, which swamped the panel with rows
  // describing nothing the reader did. Listed with and without the
  // trailing slash because the receiver is mounted without one and
  // APPEND_SLASH can resolve either spelling.
  const SYNC_LOG_EXCLUDED_PATHS = new Set(['/api/telemetry', '/api/telemetry/']);

  // In-memory copy of the last-sync clock, hydrated from IndexedDB on
  // init (see hydratePersistedClocks) and kept in sync with the persisted
  // copy on every qualifying response.
  let syncLastAt = null;

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
    // SNOW-742: a latched app keeps the banner up even though
    // ``navigator.onLine`` may well be true — on the Underground it stays true
    // throughout, which is the whole reason the latch exists. Hiding the
    // banner there would leave the user reading cached avalanche ratings with
    // nothing on screen saying so.
    // Update the variant before deciding on visibility, not after: leaving a
    // hidden banner holding the previous mode's message means the next reveal
    // shows the wrong one for a frame, and the banner is revealed by a network
    // failure at an arbitrary later moment.
    renderNetworkMode(banner);
    if (online && networkMode !== 'offline') {
      banner.classList.add('hidden');
      stopFreshnessTicker();
      return;
    }
    banner.classList.remove('hidden');
    renderFreshnessCells(banner);
    startFreshnessTicker();
  }

  /**
   * SNOW-742: show the message, explanation and control that match the current
   * network mode. Both variants of each are rendered server-side by
   * ``includes/_offline_banner.html`` and toggled here, so no user-facing
   * string is ever built in JavaScript (docs/i18n.md).
   *
   * @param {HTMLElement} banner
   */
  function renderNetworkMode(banner) {
    const latched = networkMode === 'offline';
    const toggle = (role, shown) => {
      const el = banner.querySelector(`[data-role="${role}"]`);
      if (el) el.classList.toggle('hidden', !shown);
    };
    toggle('offline-message', !latched);
    toggle('latched-message', latched);
    toggle('offline-explainer', !latched);
    toggle('latched-explainer', latched);
    // The two controls are mutually exclusive, and each is only offered in the
    // state where it does something: "try reconnecting" while latched, "stay
    // offline" while merely struggling.
    toggle('reconnect', latched);
    toggle('stay-offline', !latched);
  }

  /**
   * Ask the service worker to change mode, and persist the request so a
   * restarted worker can be told about it again on the next boot.
   *
   * @param {'auto'|'offline'} mode
   */
  function requestNetworkMode(mode) {
    networkMode = mode;
    persistMeta(NETWORK_MODE_KEY, mode);
    renderBanner(navigator.onLine);
    try {
      navigator.serviceWorker?.controller?.postMessage({ type: 'network-mode', mode });
    } catch (_err) {
      // No controller yet (first load before activation), or messaging
      // unavailable. The persisted row is re-asserted on the next boot, so the
      // user's choice is not lost — it just takes effect a load later.
    }
  }

  /**
   * Bind the banner's two mode controls, and listen for the worker announcing
   * a mode change it made on its own (the latch tripping, or a probe finding
   * a route again).
   */
  function bindNetworkModeControls() {
    const banner = document.getElementById(BANNER_ID);
    if (banner) {
      banner.querySelector('[data-role="reconnect"]')?.addEventListener('click', () => {
        requestNetworkMode('auto');
      });
      banner.querySelector('[data-role="stay-offline"]')?.addEventListener('click', () => {
        requestNetworkMode('offline');
      });
    }
    // ``navigator.serviceWorker``'s message queue is disabled until something
    // enables it — setting ``onmessage``, or calling this. An
    // ``addEventListener`` listener alone does NOT enable it, so a message the
    // worker posts before the queue opens is simply never delivered. In
    // practice the queue opens at document load and sw_register.js has always
    // relied on that, but the latch can trip during the initial tile burst,
    // which is close enough to that boundary to be worth removing the
    // question. Idempotent, and a no-op once already enabled.
    try {
      navigator.serviceWorker?.startMessages?.();
    } catch (_err) {
      // Not available (or no container at all) — fall back to the implicit
      // load-time enablement, which is what the rest of the app already uses.
    }
    navigator.serviceWorker?.addEventListener('message', (event) => {
      if (!event.data || event.data.type !== 'network-mode') return;
      networkMode = event.data.mode === 'offline' ? 'offline' : 'auto';
      persistMeta(NETWORK_MODE_KEY, networkMode);
      renderBanner(navigator.onLine);
      syncNetworkRequired(navigator.onLine && networkMode !== 'offline');
    });
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
   * Whether a same-origin pathname is worth recording as a "sync" — a
   * request the reader would recognise as the app fetching something,
   * rather than a static asset or the page's own background chatter.
   *
   * @param {string} pathname
   * @returns {boolean}
   */
  function isLoggableSyncPath(pathname) {
    if (SYNC_LOG_EXCLUDED_PATHS.has(pathname)) return false;
    return !isStaticAssetPath(pathname);
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
   * Called whenever a response header set + resolved URL + status is
   * available (fetch or HTMX). ``syncLastAt`` / ``sync.last_at`` /
   * the ``log:sync`` row (for paths ``isLoggableSyncPath`` accepts) only
   * advance for a response that is: successful (2xx status), un-stamped
   * (no ``X-SW-Cache`` header — i.e. neither a Cache-Storage replay
   * (``X-SW-Cache: hit``) nor a synthesized offline fallback
   * (``X-SW-Cache: miss``) served by ``static/js/sw.js``), and
   * same-origin with a *non-empty* resolved URL. The empty-URL check matters because a synthesized
   * service-worker fallback (offline cache miss — see SNOW-490) has
   * ``url === ''``, which would otherwise resolve against
   * ``location.href`` and be misclassified as a same-origin success.
   * ``X-Data-Generated-At`` is always absorbed regardless of cache-hit
   * status — a cache-served response still tells the user how old the
   * data it's showing is.
   *
   * That same qualifying-response condition doubles as the banner's
   * ``online``-independent recovery path — see the call to
   * ``renderBanner(true)`` below.
   *
   * @param {(name: string) => string | null} getHeader
   * @param {string} responseUrl
   * @param {number} status
   */
  function absorbFreshness(getHeader, responseUrl, status) {
    const now = new Date();
    const cacheHit = !!getHeader('X-SW-Cache');
    const successful = status >= 200 && status < 300;

    let sameOrigin = false;
    let pathname = '';
    if (responseUrl) {
      try {
        const parsed = new URL(responseUrl, window.location.href);
        sameOrigin = parsed.origin === window.location.origin;
        pathname = parsed.pathname;
      } catch (_err) {
        sameOrigin = false;
      }
    }

    if (successful && !cacheHit && sameOrigin) {
      syncLastAt = now;
      persistMeta(SYNC_LAST_AT_KEY, now.toISOString());
      if (isLoggableSyncPath(pathname)) {
        appendSyncLogEntry(now, pathname);
      }
      // A real same-origin round-trip proves the connection works, so it
      // is also the banner's recovery path. Without this the only hide
      // path is the ``online`` event, which never fires when
      // connectivity never actually changed — a single failed request on
      // an online page would pin the banner open for the life of that
      // page.
      if (navigator.onLine) renderBanner(true);
    }

    // If the banner is already open, refresh its label live so the user
    // sees the clocks update when a fresh(er) response arrives.
    if (!navigator.onLine) renderBanner(false);
  }

  /**
   * Wrap ``window.fetch`` so every response participates in the
   * freshness ledger and every network failure flips the banner. An
   * ``AbortError`` is excluded — see the ``catch`` below.
   */
  function wrapFetch() {
    if (typeof window.fetch !== 'function') return;
    const original = window.fetch.bind(window);
    window.fetch = async function (...args) {
      try {
        const response = await original(...args);
        try {
          absorbFreshness(
            (name) => response.headers.get(name),
            response.url,
            response.status,
          );
        } catch (_err) {
          // Never break the caller on a header quirk.
        }
        return response;
      } catch (err) {
        // An abort is the caller cancelling its own request, not a
        // connectivity failure, so it must not reveal the banner. The
        // sign-in page hits this on every visit: the WebAuthn
        // conditional ceremony starts on email-input focus
        // (static/js/passkey.js) and is aborted on the first keystroke,
        // which on a fully-online page would otherwise pin the banner
        // open — nothing would fire ``online`` to hide it again.
        if (err && err.name === 'AbortError') throw err;
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
        absorbFreshness(
          (name) => xhr.getResponseHeader(name),
          xhr.responseURL || '',
          xhr.status,
        );
      } catch (_err) {
        // Ignore.
      }
    });
    // No ``AbortError`` guard needed here, unlike the fetch wrapper:
    // htmx raises ``htmx:sendError`` from ``xhr.onerror`` only, and
    // routes a cancelled request to a separate ``htmx:sendAbort`` event
    // we do not listen for. The recovery path is the ``afterOnLoad``
    // handler above, whose ``absorbFreshness`` call re-hides the banner
    // on the next successful same-origin response.
    document.body?.addEventListener('htmx:sendError', () => {
      renderBanner(false);
    });
  }

  /**
   * Broadcast the connection state to any listener that needs to react
   * beyond the blunt ``data-network-required`` disable — chiefly the map's
   * layers menu (map_layer_sync_status.js), which gates each row against
   * *cache* state (offline + uncached ⟹ disabled + red dot) rather than
   * disabling everything wholesale. A single event keeps every consumer off
   * its own ``navigator.onLine`` poll and in lockstep with the banner.
   *
   * @param {boolean} online
   */
  function broadcastConnectivity(online) {
    document.dispatchEvent(
      new CustomEvent('snowdesk:connectivity-changed', { detail: { online } }),
    );
  }

  /**
   * Bind ``online`` / ``offline`` events on window so the banner and
   * network-required elements track the connection state without
   * requiring a page reload.
   */
  function bindConnectionEvents() {
    window.addEventListener('online', () => {
      // SNOW-742: an interface coming back is the strongest signal there is
      // that the latch should lift, and it beats waiting out the worker's own
      // backoff (up to five minutes). If the route is still dead, three
      // bounded reads re-latch within about nine seconds.
      if (networkMode === 'offline') requestNetworkMode('auto');
      renderBanner(true);
      syncNetworkRequired(true);
      broadcastConnectivity(true);
    });
    window.addEventListener('offline', () => {
      renderBanner(false);
      syncNetworkRequired(false);
      broadcastConnectivity(false);
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
      const syncRow = await window.pwaDb.get('meta:app', SYNC_LAST_AT_KEY);
      if (syncRow && syncRow.value) {
        const parsed = new Date(syncRow.value);
        if (!Number.isNaN(parsed.valueOf())) syncLastAt = parsed;
      }
    } catch (_err) {
      // Best-effort — the banner falls back to "no data yet" copy.
    }
    // SNOW-742: and the network mode, which is re-asserted to the worker by
    // ``init`` below. Read separately from the clock above so one failing row
    // can't cost the other.
    try {
      const modeRow = await window.pwaDb.get('meta:app', NETWORK_MODE_KEY);
      if (modeRow && modeRow.value === 'offline') networkMode = 'offline';
    } catch (_err) {
      // Best-effort — an unread mode simply starts in 'auto', and the latch
      // re-trips within about nine seconds if the radio really is dead.
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
    // SNOW-742: bound BEFORE the IndexedDB read below, not after. The worker
    // can latch during the page's own initial request burst, and a listener
    // attached behind an await would miss the announcement — leaving the app
    // latched with the banner still claiming it is merely struggling.
    bindNetworkModeControls();
    await hydratePersistedClocks();
    // SNOW-742: re-assert the persisted mode to the worker. A worker
    // terminated while idle comes back in 'auto' having forgotten the latch;
    // this is what restores it, and it is why the worker never has to read
    // IndexedDB on its own fetch path.
    if (networkMode === 'offline') requestNetworkMode('offline');
    renderBanner(navigator.onLine);
    syncNetworkRequired(navigator.onLine && networkMode !== 'offline');
    // Prime consumers with the initial state so a page that loaded offline
    // (via the SW cache) gets its cache-aware gating applied at boot, not
    // only on the next transition.
    broadcastConnectivity(navigator.onLine);
  }

  init();
})();
