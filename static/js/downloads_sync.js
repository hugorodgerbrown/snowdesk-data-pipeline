/*
 * static/js/downloads_sync.js — the account half of offline downloads
 * (SNOW-749).
 *
 * A downloaded area is two things kept in different places. The BYTES are
 * a pinned Cache Storage bucket and a `meta:app` record, both per-browser
 * and neither of them ever leaving the device. The DEFINITION — which
 * region, or which framed box, under which basemap, called what — is a row
 * on the account, and that is all this module moves.
 *
 * The split is the whole design. A user who frames an area on a laptop can
 * re-download it on a phone with one tap instead of re-framing it by hand,
 * and a device that hits its storage budget evicts the tiles while leaving
 * a record that the area ever existed. Nothing here makes a download
 * available on another device: only its definition travels.
 *
 * Three fields are deliberately NOT synced. `band` is the constant
 * `MICRO_BAND` and is identical on every download; `template` is derived
 * from settings plus the basemap key, so syncing it would pin an external
 * tile endpoint into a database row; `bytes` is a per-device measurement,
 * not a property of the area.
 *
 * ## The join key is the client-minted area id
 *
 * `region-<region_id>` is deterministic and identical on every device
 * (`basemap_download_core.js`'s `areaIdForRegion`), and `custom-<uuid>` is
 * already a uuid. The server stores it verbatim, unique per user, so
 * `basemap_manage_core.js`'s `reconcileAreas` stays a single keyed join
 * with no server-id ↔ local-id mapping to maintain — and every write here
 * addresses a row by an id the client already has, which matters because
 * these writes replay through the mutation queue long after the response
 * that would have carried a server uuid was discarded.
 *
 * ## Nothing downstream ever waits on this
 *
 * `accountAreas()` resolves `[]` for an anonymous visitor, an offline
 * device, a refusal, a timeout and a parse failure alike. It is read on
 * the path that opens the Manage downloads sheet, which must work with no
 * signal, so it is time-bounded on the same reasoning as the service
 * worker's own read paths — a dead radio hangs rather than rejecting (see
 * docs/decisions/bounded-offline-read-paths.md). This module has no latch
 * of its own: an empty list is a correct, complete answer here, unlike a
 * missing bulletin.
 *
 * ## Writes go through the mutation queue, reads do not
 *
 * A write is enqueued (`window.pwaMutationQueue`), so a download completed
 * in a tunnel is recorded when the device surfaces. A read is a plain
 * `fetch()`, because a queued read has nothing to deliver its answer to.
 * The queue replays with a plain fetch, which is why every enqueued
 * operation carries `HX-Request: true` — the endpoints are `@require_htmx`
 * and would otherwise 400 on replay.
 *
 * Everything is gated on `download_sync`. With the flag off `isEnabled()`
 * is false and every function is an immediate no-op, which is exactly the
 * behaviour that shipped before this ticket.
 */

(function () {
  'use strict';

  // Where the area ids already pushed to the account are remembered, so
  // `adopt()` can tell a first authenticated load from a repeat one
  // without a round trip. Advisory only: a lost or stale value costs one
  // redundant push, which the server absorbs as an `update_or_create` on
  // the same `(user, area_id)`.
  var SYNCED_KEY = 'basemap.syncedAreaIds';

  // The budget for the one read this module makes. Generous enough for a
  // slow mobile round trip, short enough that opening the Manage downloads
  // sheet in a car park never feels hung — the list this call adds to is
  // supplementary, and the device's own areas are already on screen.
  var READ_BUDGET_MS = 4000;

  /**
   * The element every download surface reads its configuration off.
   *
   * Resolved per call rather than captured at parse time so this module
   * has no load-order relationship with the template that renders it: the
   * roundel is in the document long before anything here is called.
   *
   * @returns {DOMStringMap} The dataset, or an empty object when the page
   *   carries no downloads surface at all.
   */
  function config() {
    var el = document.getElementById('map-custom-download-control');
    return el ? el.dataset : {};
  }

  /**
   * Whether the account half of downloads is switched on for this visitor.
   *
   * Three conditions, and all three are needed. The `download_sync` flag
   * is the rollout gate; authentication is what the endpoints require; a
   * sync URL is what says the server side is actually wired up. Any of
   * them missing means every function below is a no-op rather than a
   * failed request.
   *
   * @returns {boolean}
   */
  function isEnabled() {
    var cfg = config();
    return (
      cfg.downloadsSync === 'true' &&
      cfg.downloadsEligible === 'true' &&
      !!cfg.downloadSyncUrl
    );
  }

  /**
   * The CSRF token for a write, read off the page's own form input.
   *
   * Django issues one token per session and it is valid across every form
   * on the page, so the sign-out form the nav renders for a signed-in user
   * is a legitimate source — the same read `account_favourites.js` makes.
   * A signed-out page has no such form and also nothing to sync, so an
   * empty token is never reached in practice.
   *
   * @returns {string}
   */
  function csrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  /**
   * Read the area ids already pushed to the account.
   *
   * Best-effort: an unreadable IndexedDB yields an empty list, which makes
   * `adopt()` push everything again. That is the safe direction — a
   * redundant push is an update, a skipped one is a missing row.
   *
   * @returns {Promise<string[]>}
   */
  async function _readSynced() {
    try {
      var row = await window.pwaDb?.get('meta:app', SYNCED_KEY);
      return Array.isArray(row && row.value) ? row.value : [];
    } catch (_err) {
      return [];
    }
  }

  /**
   * Remember `areaId` as pushed.
   *
   * Always a `put` of the whole list, never an append to a shared cursor:
   * this is one small array and a read-modify-write of it is atomic enough
   * for a value nothing else writes.
   *
   * @param {string} areaId
   * @returns {Promise<void>}
   */
  async function _markSynced(areaId) {
    try {
      var existing = await _readSynced();
      if (existing.indexOf(areaId) !== -1) return;
      await window.pwaDb?.put('meta:app', {
        key: SYNCED_KEY,
        value: existing.concat([areaId]),
      });
    } catch (_err) {
      // Best-effort — see `_readSynced`.
    }
  }

  /**
   * Forget that `areaId` was pushed.
   *
   * Called when the account row goes, so that re-downloading the same area
   * later pushes it again rather than assuming a row that is no longer
   * there.
   *
   * @param {string} areaId
   * @returns {Promise<void>}
   */
  async function _unmarkSynced(areaId) {
    try {
      var existing = await _readSynced();
      if (existing.indexOf(areaId) === -1) return;
      await window.pwaDb?.put('meta:app', {
        key: SYNCED_KEY,
        value: existing.filter(function (id) {
          return id !== areaId;
        }),
      });
    } catch (_err) {
      // Best-effort — see `_readSynced`.
    }
  }

  /**
   * Enqueue one form-encoded POST through the mutation queue.
   *
   * The queue is what makes a write survive a download finished with no
   * signal: it persists the operation to IndexedDB and replays it with a
   * stable `Idempotency-Key` on reconnect. `HX-Request` is set because
   * every endpoint here is `@require_htmx` and the replay is a plain
   * fetch — the exact shape `favourites.js`'s create submit uses.
   *
   * @param {string} url
   * @param {Object<string, string>} fields Form fields, CSRF included by
   *   this function.
   * @returns {Promise<boolean>} Whether the operation was accepted for
   *   delivery. Never rejects: a failed enqueue costs a missing account
   *   row, not a failed download, and the download is what the user asked
   *   for.
   */
  async function _enqueue(url, fields) {
    if (!window.pwaMutationQueue || !url) return false;
    var body = new URLSearchParams(
      Object.assign({ csrfmiddlewaretoken: csrfToken() }, fields),
    ).toString();
    try {
      await window.pwaMutationQueue.enqueue({
        method: 'POST',
        url: url,
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          // Every downloads endpoint is @require_htmx; the queue replays
          // with a plain fetch, which would otherwise 400.
          'HX-Request': 'true',
        },
        body: body,
      });
      return true;
    } catch (_err) {
      return false;
    }
  }

  /**
   * Record one downloaded area against the account.
   *
   * Called from the two places a completed download is already recorded
   * locally — `map_region_download.js`'s `_recordRegionDownload` and
   * `map_basemap_downloads.js`'s `_appendCustomArea` — so the account row
   * and the device record are written from the same moment and cannot
   * disagree about what was downloaded.
   *
   * The server derives `kind` from the id's own prefix rather than trusting
   * a posted field, so nothing here has to say which of the two an area
   * is.
   *
   * @param {{areaId: string, regionId?: string, bbox?: number[],
   *   basemapKey?: string|null, name?: string}} record The area as the
   *   local writer has it. `bbox` is `[west, south, east, north]` in
   *   (lon, lat) order, matching the server's own carve-out; it is
   *   required for a custom area and ignored for a region, whose tiles are
   *   computed server-side from its real boundary.
   * @returns {Promise<boolean>} Whether the write was queued. Never
   *   rejects — see `_enqueue`.
   */
  async function push(record) {
    if (!isEnabled() || !record || !record.areaId) return false;
    var queued = await _enqueue(config().downloadSyncUrl, {
      area_id: record.areaId,
      region_id: record.regionId || '',
      // A JSON string, not repeated fields: the server parses one value
      // and validates it as four numbers, so a partially-posted box can
      // never be read as a whole one.
      bbox: Array.isArray(record.bbox) ? JSON.stringify(record.bbox) : '',
      basemap_key: record.basemapKey || '',
      name: record.name || '',
    });
    if (queued) await _markSynced(record.areaId);
    return queued;
  }

  /**
   * Rename an area on the account.
   *
   * The local rename (`map_basemap_downloads.js`'s `renameCustomArea`) is
   * what the sheet renders from and is never blocked on this — a rename
   * lands on the device whether or not the account row follows.
   *
   * @param {string} areaId
   * @param {string} name
   * @returns {Promise<boolean>} Whether the write was queued.
   */
  async function rename(areaId, name) {
    if (!isEnabled() || !areaId) return false;
    var template = config().downloadRenameUrlTemplate;
    if (!template) return false;
    return _enqueue(template.replace('__AREA_ID__', encodeURIComponent(areaId)), {
      name: name || '',
    });
  }

  /**
   * Drop one area from the account.
   *
   * The account half of the manage sheet's trash. It does NOT evict the
   * device's tiles — that is a separate, local call, and the two verbs are
   * deliberately separate: "free up space" and SNOW-586's automatic budget
   * eviction take only the local half, which is what lets an evicted area
   * survive as a one-tap re-download.
   *
   * @param {string} areaId
   * @returns {Promise<boolean>} Whether the write was queued.
   */
  async function forget(areaId) {
    if (!isEnabled() || !areaId) return false;
    var template = config().downloadForgetUrlTemplate;
    if (!template) return false;
    var queued = await _enqueue(
      template.replace('__AREA_ID__', encodeURIComponent(areaId)),
      {},
    );
    // Unmarked whether or not the enqueue landed: either the row is going,
    // or it never got there, and both mean a later download of the same
    // area should push again.
    await _unmarkSynced(areaId);
    return queued;
  }

  /**
   * Fetch with a time budget.
   *
   * An explicit `AbortController` rather than `AbortSignal.timeout(ms)`,
   * for the reasons `sw.js`'s `_boundedFetch` gives: it is drivable by a
   * test's fake clock, and it needs no Safari 16.
   *
   * @param {string} url
   * @param {number} ms
   * @returns {Promise<Response>}
   */
  async function _boundedFetch(url, ms) {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, ms);
    try {
      return await fetch(url, {
        signal: controller.signal,
        // The endpoint is per-user; the session cookie is the whole point.
        credentials: 'same-origin',
      });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Every area on the requesting user's account.
   *
   * Resolves `[]` for every unhappy path there is — flag off, signed out
   * (403), offline, timed out, refused, or a body that will not parse. The
   * caller (`basemap_manage_core.js`'s `reconcileAreas`, through
   * `map_basemap_downloads.js`'s `basemapDownloadedAreas`) treats an empty
   * list as "no account rows", which produces exactly the pre-SNOW-749
   * output — so a failure here degrades the surface to what it was, never
   * to an error.
   *
   * Deliberately NOT memoised. The list changes when the user downloads,
   * renames or forgets an area, and this is read on sheet open — a cache
   * would show a list the user just changed. It is one small request,
   * bounded above, behind a flag.
   *
   * @returns {Promise<Array<{area_id: string, kind: string,
   *   region_id: string, bbox: number[]|null, basemap_key: string,
   *   name: string, created_at: string}>>} Never rejects.
   */
  async function accountAreas() {
    if (!isEnabled()) return [];
    var url = config().downloadAreasUrl;
    if (!url) return [];
    // Skipped outright while the platform already knows the interface is
    // down — that needs no budget to discover, and the sheet opens sooner.
    if (navigator.onLine === false) return [];
    try {
      var response = await _boundedFetch(url, READ_BUDGET_MS);
      if (!response.ok) return [];
      var payload = await response.json();
      return Array.isArray(payload && payload.areas) ? payload.areas : [];
    } catch (_err) {
      return [];
    }
  }

  /**
   * Push every local area the account has not been told about.
   *
   * The migration path for a device that was downloading before this
   * feature existed, and the repair path for a push that never landed.
   * Idempotent on both sides: the marker list skips what has already been
   * sent, and the endpoint is an `update_or_create` on `(user, area_id)`
   * for anything that slips through.
   *
   * Reads through `window.pwaBasemapDownloads.areas()` rather than the
   * two `meta:app` records directly — that bridge is the single
   * normalising layer, and a second reader here would have to re-derive
   * the record-to-bucket-id mapping it already owns.
   *
   * @returns {Promise<number>} How many areas were pushed — 0 when
   *   everything was already synced, which is the common case on every
   *   load after the first.
   */
  async function adopt() {
    if (!isEnabled()) return 0;
    var areas;
    try {
      areas = await window.pwaBasemapDownloads?.areas();
    } catch (_err) {
      return 0;
    }
    if (!Array.isArray(areas) || !areas.length) return 0;

    var synced = await _readSynced();
    var core = self.pwaBasemapDownloadCore;
    var pushed = 0;
    for (var i = 0; i < areas.length; i += 1) {
      var area = areas[i];
      if (!area || !area.id || synced.indexOf(area.id) !== -1) continue;
      // An orphaned bucket (SNOW-612 — a download that failed partway,
      // leaving tiles and no record) is skipped: there is no completed
      // area to describe, and syncing one would offer another device a
      // download that never finished on this one.
      if (area.orphaned) continue;
      var isCustom = core ? core.isCustomAreaId(area.id) : false;
      // A custom area with no stored bbox cannot be described to another
      // device, and the server refuses it — so it is skipped here rather
      // than posted to be rejected.
      if (isCustom && !Array.isArray(area.bbox)) continue;
      var ok = await push({
        areaId: area.id,
        regionId: isCustom ? '' : String(area.id).replace(/^region-/, ''),
        bbox: area.bbox,
        basemapKey: area.basemapKey,
        name: area.name,
      });
      if (ok) pushed += 1;
    }
    return pushed;
  }

  self.pwaDownloadsSync = Object.freeze({
    isEnabled: isEnabled,
    push: push,
    rename: rename,
    forget: forget,
    accountAreas: accountAreas,
    adopt: adopt,
  });
})();
