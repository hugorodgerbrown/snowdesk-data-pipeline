/*
 * static/js/routes_offline.js — the user's own saved routes, kept readable
 * away from signal (SNOW-950).
 *
 * The map's routes panel lazy-loads its rows from ``routes:list`` over
 * HTMX, and ``sw.js`` classifies that endpoint network-only. Offline the
 * panel therefore opened onto a failure line — "Your routes couldn't be
 * loaded" — for routes the map was ALREADY drawing as lines beside it,
 * because SNOW-687 caches the routes GeoJSON in ``data:map_overlays``. The
 * lines were there and the user's own list was not, and the offline content
 * report answered Yes for the same surface the panel said no to.
 *
 * This is the §12.6 relaxation SNOW-418 established for favourites and
 * SNOW-661 applied to field observations, applied to that list. A route is
 * a file the user uploaded, not a forecast that expires, so the cached rows
 * render as themselves with a line saying where they came from.
 *
 * Why the RENDERED BODY rather than a record per route
 * -----------------------------------------------------
 * Nothing is rebuilt from the overlay's GeoJSON, which holds geometry for
 * the line and not the row this list renders. Nor is a JSON record per
 * route the right shape: a row carries a server-translated name, distance
 * and ascent line, and rebuilding it would mean assembling a translated,
 * markup-bearing sentence in JavaScript — precisely the class of string
 * ``i18n-lint`` exists to catch. SNOW-879's ``panel_rows_cache.js`` already
 * holds this endpoint's exact response body, in memory, for one page load.
 * Persisting the same bytes keeps every translation and every measurement
 * verbatim, and leaves this module with no prose of its own: the one line
 * the user reads offline is ``routes.js``'s, from the template's strings
 * block.
 *
 * Two ways in
 * ------------
 * A body reaches ``write()`` either from a panel OPEN — ``htmx:beforeSwap``
 * on the container this module owns — or from the idle WARM ``routes.js``
 * fires at every map page load. Both are needed, and the second is the one
 * that matters: a warm is a plain fetch raising no htmx event, so on the
 * open path alone the rows would be stored only for a user who had opened
 * the sheet while online, and not for the user who loads the map with
 * signal and opens the panel in the backcountry. The warm reaches here
 * through ``panel_rows_cache.js``'s ``onWarmed()`` hook, registered below
 * for this key only.
 *
 * Storage only. This module writes and reads; ``routes.js`` decides when to
 * paint, because it owns everything else the user sees in that panel — and
 * it is ``routes.js`` that takes the online-only controls back off a cached
 * row.
 *
 * Row shape in ``data:panel_rows`` (``db.js`` schema v6):
 *
 *     { key: 'routes', body: '<the rendered rows>', cached_at, principal }
 *
 * ``principal`` partitions the row by the signed-in account, exactly as
 * SNOW-493 does for the account-specific overlays: these are the user's own
 * routes, and a row cached under one account must never repaint into
 * another's session on a shared browser. A row whose principal does not
 * match reads back as ``null``, and so does one written before principals
 * existed.
 *
 * No exports beyond ``window.pwaRoutesOffline``.
 */

(function routesOfflineInit() {
  'use strict';

  const STORE = 'data:panel_rows';
  const KEY = 'routes';

  /**
   * True when ``window.pwaDb`` is present and the app is not in the terminal
   * Reset Required state. Every DB access below is guarded by this first,
   * mirroring observations_offline.js and map_overlay_offline_cache.js.
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
      // account-specific overlay rows. The stored side is compared
      // untouched, exactly as map_overlay_offline_cache.js does it:
      // normalising an absent key to ``null`` would make such a row match an
      // anonymous reader, whose own principal is null.
      if (record?.principal !== currentPrincipal()) return null;
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
    // target rather than on the request path means the observations and
    // favourites panels — whose list endpoints look much alike — cannot be
    // mistaken for this one.
    if (!detail.target.matches || !detail.target.matches('[data-routes-rows]')) {
      return;
    }
    // An error response says nothing about the rows; caching it would serve
    // the failure back as content on the next open.
    if (detail.isError) return;
    write(detail.serverResponse).catch(function () {
      // Swallowed — see write().
    });
  });

  // The other half of the write-through, and the one that serves the user
  // this module was written for. ``routes.js`` warms the panel's rows at
  // module init on every map page load, and that warm is a plain fetch
  // raising no htmx event (``panel_rows_cache.js``'s header says why), so
  // the listener above never sees it. Without this the rows would be
  // persisted only by a panel OPEN while online — leaving the far commoner
  // case, loading the map with signal and opening the panel later without
  // it, on the failure line the ticket exists to remove.
  //
  // Naming the key here rather than in panel_rows_cache.js is SNOW-722's
  // boundary: the shared cache serves three panels and must not know which
  // of them keeps its rows.
  window.pwaPanelRows?.onWarmed(KEY, write);

  Object.defineProperty(window, 'pwaRoutesOffline', {
    value: Object.freeze({ read: read, write: write }),
    writable: false,
    configurable: false,
  });
}());
