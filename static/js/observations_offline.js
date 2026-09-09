/*
 * static/js/observations_offline.js — the user's own field observations,
 * kept readable away from signal (SNOW-661).
 *
 * The map's field-observation panel lazy-loads its rows from
 * ``observations:list`` over HTMX, and ``sw.js`` classifies that endpoint
 * network-only. Offline the panel therefore opened onto a failure line —
 * "Your reports couldn't be loaded" — for reports the map was ALREADY
 * drawing as pins beside it, because SNOW-492 caches the community-reports
 * GeoJSON. The pins were there and the user's own list was not.
 *
 * This is the §12.6 relaxation SNOW-418 established for favourites, applied
 * to that list: cache the response, and render from the cache when the
 * request fails. An observation needs no staleness horizon of the kind a
 * danger rating does — it is a record of something that happened, not a
 * forecast that expires — so the cached rows render as themselves, with a
 * line saying they came from the cache and an age that keeps ticking
 * (``relative_time.js`` recomputes each ``<time datetime>`` from the
 * instant, which needs no network at all).
 *
 * Why the RENDERED BODY rather than a record per observation
 * ----------------------------------------------------------
 * The scoping comment asked for a JSON record per observation, rebuilt into
 * rows in JavaScript — the shape ``favourites_offline.js`` uses. Two things
 * make that the wrong shape here. SNOW-886 put a region name, a
 * ``<time datetime>`` element and a what3words line into the row's meta
 * line, all server-translated; rebuilding it would mean assembling a
 * translated, markup-bearing sentence in JavaScript, which is precisely the
 * class of string ``i18n-lint`` exists to catch. And SNOW-879's
 * ``panel_rows_cache.js`` already holds this endpoint's exact response body
 * — in memory, for one page load. Persisting the same bytes keeps every
 * translation, region name and timestamp verbatim, and leaves this module
 * with no prose of its own: the one line the user reads offline is
 * ``report.js``'s, from the template's strings block.
 *
 * Storage only. This module writes and reads; ``report.js`` decides when to
 * paint, because it owns everything else the user sees in that panel.
 *
 * Row shape in ``data:panel_rows`` (``db.js`` schema v6):
 *
 *     { key: 'observations', body: '<the rendered rows>', cached_at, principal }
 *
 * ``principal`` partitions the row by the signed-in account, exactly as
 * SNOW-493 does for the account-specific overlays: these are the user's own
 * reports, and a row cached under one account must never repaint into
 * another's session on a shared browser. A row whose principal does not
 * match reads back as ``null``, and so does one written before principals
 * existed.
 *
 * No exports beyond ``window.pwaObservationsOffline``.
 */

(function observationsOfflineInit() {
  'use strict';

  const STORE = 'data:panel_rows';
  const KEY = 'observations';

  /**
   * True when ``window.pwaDb`` is present and the app is not in the terminal
   * Reset Required state. Every DB access below is guarded by this first,
   * mirroring favourites_offline.js and map_overlay_offline_cache.js.
   *
   * @returns {boolean}
   */
  function dbReady() {
    return typeof window.pwaDb === 'object' && !window.pwaDb.isResetRequired();
  }

  /**
   * The current principal — ``<meta name="pwa-user-id">`` read through
   * ``window.pwaDb.context().user_id``, with an empty string or a missing
   * value normalised to ``null`` (anonymous). Mirrors
   * ``map_overlay_offline_cache.js``'s ``_currentPrincipal``.
   *
   * @returns {string|null}
   */
  function currentPrincipal() {
    try {
      if (typeof window.pwaDb !== 'object') return null;
      const userId = window.pwaDb.context().user_id;
      return userId === undefined || userId === '' ? null : userId;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Persist the panel's rendered rows.
   *
   * Best effort throughout: a failed write leaves the panel exactly as it
   * behaved before this module existed, and must never break the swap it
   * rides on.
   *
   * @param {string} body The response body, verbatim.
   * @returns {Promise<void>}
   */
  async function write(body) {
    if (!dbReady() || typeof body !== 'string') return;
    try {
      await window.pwaDb.put(STORE, {
        key: KEY,
        body: body,
        cached_at: new Date().toISOString(),
        principal: currentPrincipal(),
      });
    } catch (_e) {
      // Non-fatal — the cache is best effort.
    }
  }

  /**
   * Read the cached rows back, or ``null`` when there is nothing to paint.
   *
   * Null covers four cases the caller cannot tell apart and does not need
   * to — the DB is unavailable, nothing has been cached, the row belongs to
   * a different account, or the read threw. All of them mean the same thing
   * to the panel: draw the failure line.
   *
   * @returns {Promise<{body: string, cached_at: string}|null>}
   */
  async function read() {
    if (!dbReady()) return null;
    try {
      const record = await window.pwaDb.get(STORE, KEY);
      if (!record || typeof record.body !== 'string') return null;
      // A row with no principal at all was written before this partitioning
      // existed, and never matches — the same rule SNOW-493 applies to the
      // account-specific overlay rows.
      if ((record.principal ?? null) !== currentPrincipal()) return null;
      return record;
    } catch (_e) {
      return null;
    }
  }

  // Delegated from document.body because htmx raises the lifecycle events
  // for an ``htmx.ajax`` call with no source element against document.body —
  // the same bind point, for the same reason, as panel_rows_cache.js and
  // each panel's own error handlers.
  document.body?.addEventListener('htmx:beforeSwap', function (ev) {
    const detail = ev.detail;
    if (!detail || !detail.target) return;
    // SNOW-722's ownership lesson: this listener sees EVERY swap on the
    // page, so it writes only for the container it owns. Matching on the
    // target rather than on the request path means the routes and
    // favourites panels — whose list endpoints look much alike — cannot be
    // mistaken for this one.
    if (!detail.target.matches || !detail.target.matches('[data-report-rows]')) {
      return;
    }
    // An error response says nothing about the rows; caching it would serve
    // the failure back as content on the next open.
    if (detail.isError) return;
    write(detail.serverResponse).catch(function () {
      // Swallowed — see write().
    });
  });

  Object.defineProperty(window, 'pwaObservationsOffline', {
    value: Object.freeze({ read: read, write: write }),
    writable: false,
    configurable: false,
  });
}());
