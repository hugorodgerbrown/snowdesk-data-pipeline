/*
 * static/js/routes_bulletin_offline.js — one route's reading of one day's
 * bulletin, kept readable away from signal (SNOW-973).
 *
 * The map's route detail panel fetches ``routes:bulletin`` when a saved
 * route is tapped, and ``sw.js`` classifies that endpoint network-only. Off
 * signal the panel would therefore open onto a failure line for a route the
 * map is already drawing beside it — the same gap SNOW-950 closed for the
 * routes LIST, and the shape of this module is that one's.
 *
 * What it is NOT the same as, and this is the whole of the difference:
 *
 *   routes_offline.js caches rows whose content is a file the user
 *   uploaded. A track does not expire, so its row carries ``cached_at``
 *   and nothing else, and the panel repaints it behind a line saying when
 *   it was last updated.
 *
 *   A BULLETIN READING DOES EXPIRE. It is the forecaster's problems joined
 *   to that track, it changes twice a day, and repainting a two-day-old one
 *   as though it were current is exactly the failure the offline-first
 *   spec's §12.6 relaxation is written to prevent. So the row also carries
 *   the freshness envelope the response was served with, and a row past
 *   its own ``unsafe_after_seconds`` is reported EXPIRED rather than
 *   rendered — favourites_offline.js's ``_isExpired`` rule, on routes'
 *   rendered-body shape.
 *
 * The envelope is read straight off the ``Response`` headers
 * ``apps.core.freshness.apply_freshness_headers`` already writes
 * (``X-Data-Generated-At`` / ``X-Data-Unsafe-After``). That is the reason
 * the panel fetches rather than swapping over htmx: favourites gets the
 * same pair from a server-rendered ``json_script`` sidecar, which a
 * fetch-driven surface would have to invent a second copy of.
 *
 * Row shape in ``data:route_bulletins`` (``db.js`` schema v7):
 *
 *     {
 *       key: '<route uuid>:<YYYY-MM-DD>',
 *       body: '<the rendered fragment>',
 *       generated_at: '2026-03-01T05:00:00+00:00',
 *       unsafe_after_seconds: 172800,   // null when nothing expires
 *       cached_at: '2026-03-01T08:14:22.000Z',
 *       principal: '42'
 *     }
 *
 * ONE ROW PER (ROUTE, DAY), which is why this is a store of its own rather
 * than another key in ``data:panel_rows`` — those are keyed by PANEL, one
 * body each. Keying by the day is also what stops a cached reading being
 * repainted under a date it does not belong to: the panel asks for the day
 * the scrubber is showing and gets that day's row or nothing.
 *
 * ``principal`` partitions the row by the signed-in account, exactly as
 * SNOW-493 does for the account-specific overlays and SNOW-950 for the
 * routes list: these are readings of the user's own tracks, and a row
 * cached under one account must never repaint into another's session on a
 * shared browser. A row whose principal does not match reads back as
 * ``null``, and so does one written before principals existed.
 *
 * NO EAGER WARM. Nothing here prefetches the user's 25 routes: a reading is
 * a real per-region join on the server, and warming every route on every
 * map load would pay for routes that are never opened. What this device
 * holds is what it has already been shown — the rows are written as the
 * panel is opened, and refreshed the next time the same route and day are.
 *
 * Storage only. This module writes and reads and judges expiry;
 * ``map_route_detail.js`` decides what to paint, because it owns everything
 * else in that sheet.
 *
 * No exports beyond ``window.pwaRoutesBulletinOffline``.
 */

(function routesBulletinOfflineInit() {
  'use strict';

  var STORE = 'data:route_bulletins';

  /**
   * True when ``window.pwaDb`` is present and the app is not in the
   * terminal Reset Required state. Every DB access below is guarded by
   * this first, mirroring routes_offline.js.
   *
   * @returns {boolean}
   */
  function dbReady() {
    return typeof window.pwaDb === 'object' && !window.pwaDb.isResetRequired();
  }

  /**
   * The current principal — ``<meta name="pwa-user-id">`` read through
   * ``window.pwaDb.context().user_id``, with an empty string or a missing
   * value normalised to ``null`` (anonymous). Copied verbatim from
   * routes_offline.js, which took it from map_overlay_offline_cache.js.
   *
   * @returns {string|null}
   */
  function currentPrincipal() {
    try {
      if (typeof window.pwaDb !== 'object') return null;
      var userId = window.pwaDb.context().user_id;
      return userId === undefined || userId === '' ? null : userId;
    } catch (_e) {
      return null;
    }
  }

  /**
   * The store key for one route on one day.
   *
   * @param {string} uuid The route's uuid.
   * @param {string} day The ISO date the reading answers for.
   * @returns {string}
   */
  function keyFor(uuid, day) {
    return String(uuid) + ':' + String(day);
  }

  /**
   * Persist one rendered reading, with the envelope it was served under.
   *
   * Best effort throughout: a failed write leaves the panel exactly as it
   * behaved before this module existed, and must never break the open it
   * rides on.
   *
   * @param {string} key From ``keyFor``.
   * @param {string} body The rendered fragment, verbatim.
   * @param {string|null} generatedAt ``X-Data-Generated-At``.
   * @param {number|null} unsafeAfterSeconds ``X-Data-Unsafe-After``, or
   *   null when the response omitted it — which the server does when no
   *   bulletin was read at all, so there is nothing to expire.
   * @returns {Promise<void>}
   */
  async function write(key, body, generatedAt, unsafeAfterSeconds) {
    if (!dbReady() || typeof body !== 'string') return;
    try {
      await window.pwaDb.put(STORE, {
        key: key,
        body: body,
        generated_at: generatedAt || null,
        unsafe_after_seconds:
          typeof unsafeAfterSeconds === 'number' ? unsafeAfterSeconds : null,
        cached_at: new Date().toISOString(),
        principal: currentPrincipal(),
      });
    } catch (_e) {
      // Non-fatal — the cache is best effort.
    }
  }

  /**
   * Read one cached reading back, or ``null`` when there is nothing to
   * paint.
   *
   * Null covers four cases the caller cannot tell apart and does not need
   * to — the DB is unavailable, nothing has been cached for this route and
   * day, the row belongs to a different account, or the read threw.
   *
   * @param {string} key From ``keyFor``.
   * @returns {Promise<object|null>}
   */
  async function read(key) {
    if (!dbReady()) return null;
    try {
      var record = await window.pwaDb.get(STORE, key);
      if (!record || typeof record.body !== 'string') return null;
      // A row with no principal at all was written before this
      // partitioning existed, and never matches — the same rule SNOW-493
      // applies to the account-specific overlay rows. The stored side is
      // compared UNTOUCHED: normalising an absent key to ``null`` would
      // make such a row match an anonymous reader, whose own principal is
      // null.
      if (record?.principal !== currentPrincipal()) return null;
      return record;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Whether a cached reading is past its own horizon.
   *
   * Wall-clock ``Date.now()`` against the envelope the response carried,
   * mirroring favourites_offline.js's ``_isExpired``. A row with no
   * horizon never expires — the server omits ``X-Data-Unsafe-After`` when
   * the answer contains no bulletin, and a sentence saying the route
   * crosses no forecast region does not go stale.
   *
   * @param {object} record A row from ``read``.
   * @returns {boolean}
   */
  function isExpired(record) {
    if (!record || record.unsafe_after_seconds == null) return false;
    var generated = Date.parse(record.generated_at);
    if (Number.isNaN(generated)) return false;
    return Date.now() - generated >= record.unsafe_after_seconds * 1000;
  }

  Object.defineProperty(window, 'pwaRoutesBulletinOffline', {
    value: Object.freeze({
      keyFor: keyFor,
      read: read,
      write: write,
      isExpired: isExpired,
    }),
    writable: false,
    configurable: false,
  });
}());
