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
 * Everything is gated on being signed in. For an anonymous visitor
 * `isEnabled()` is false and every function is an immediate no-op — which
 * is also what a page carrying no downloads surface at all gets, since the
 * configuration is read off that surface.
 *
 * ## A download carries its principal (SNOW-1142)
 *
 * The bytes are device-wide on purpose — a user who signs out keeps their
 * maps for the trip — but the browser can be shared. Every local area
 * record is stamped with `principal`, the account id from
 * `<meta name="pwa-user-id">` at the moment it was written ('' for an
 * anonymous download), and `ownership()` below is the one rule deciding
 * what may be recorded to the signed-in account: its own areas, plus a
 * LEGACY record (no `principal` field) or an ANONYMOUS one, each claimed
 * ONCE by the first signed-in account to see it — stamped with that id
 * first, so no later account can claim it again. Another account's area
 * is still listed and usable here; it is never pushed. See
 * docs/decisions/a-download-carries-its-principal.md.
 */

(function () {
  'use strict';

  // Where the area ids already pushed to the account are remembered, so
  // `adopt()` can tell a first authenticated load from a repeat one
  // without a round trip. Advisory only: a lost or stale value costs one
  // redundant push, which the server absorbs as an `update_or_create` on
  // the same `(user, area_id)`.
  //
  // SNOW-1142: keyed by principal — `{[accountId]: [areaIds]}` — because
  // "this device has told the account" means nothing on a shared browser
  // without saying WHICH account. The flat array every earlier release
  // wrote is read as nobody's, so the first `adopt()` after the deploy
  // re-checks every held area against `ownership()` and pushes only what
  // the signed-in account may claim.
  var SYNCED_KEY = 'basemap.syncedAreaIds';

  // The two `meta:app` records a held area lives in — named here because
  // a claim (SNOW-1142) stamps the record itself.
  var REGIONS_KEY = 'basemap.regions';
  var CUSTOM_AREAS_KEY = 'basemap.customAreas';

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
   * Two conditions, and both are needed: authentication is what every
   * endpoint here requires, and a sync URL is what says the server side is
   * wired up at all — a page with no downloads surface supplies neither.
   * Either missing means every function below is an immediate no-op rather
   * than a request that would fail.
   *
   * SNOW-749 had a third, the `download_sync` rollout flag. It was dropped
   * before merge on query cost (see
   * ``apps.public.views._downloads_context``), so the gate is the session
   * alone.
   *
   * @returns {boolean}
   */
  function isEnabled() {
    var cfg = config();
    return cfg.downloadsEligible === 'true' && !!cfg.downloadSyncUrl;
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
   * The signed-in account's public id — `<meta name="pwa-user-id">`, which
   * `public/base.html` renders on every page — or '' when nobody is
   * signed in (SNOW-1142).
   *
   * Read off the tag at call time rather than through `pwaDb.context()`,
   * whose snapshot is cached: the tag is the page's own statement of who
   * it was rendered for, and it is all a stamp needs.
   *
   * @returns {string}
   */
  function principal() {
    var meta = document.querySelector('meta[name="pwa-user-id"]');
    return (meta && meta.getAttribute('content')) || '';
  }

  /**
   * Whose area a held record is, as far as recording it to the account is
   * concerned (SNOW-1142). The one rule; `adopt()`, `recordHeld()` and the
   * writers' own pushes all ask it.
   *
   *   'own'     — stamped with the signed-in id: push it.
   *   'claim'   — a LEGACY record (no `principal`, null here) or an
   *               ANONYMOUS download (''), seen by a signed-in account:
   *               stamp it with that id, then push it. Once.
   *   'foreign' — stamped with another account's id: never push it.
   *   'none'    — nobody is signed in: there is no account to push to.
   *
   * Pure: no DOM, no storage.
   *
   * @param {string|null|undefined} recordPrincipal The record's stamp.
   * @param {string} signedIn The signed-in account id, '' for none.
   * @returns {'own'|'claim'|'foreign'|'none'}
   */
  function ownership(recordPrincipal, signedIn) {
    if (!signedIn) return 'none';
    if (recordPrincipal === signedIn) return 'own';
    if (recordPrincipal === undefined || recordPrincipal === null || recordPrincipal === '') {
      return 'claim';
    }
    return 'foreign';
  }

  /**
   * The record a writer stores, stamped with its principal (SNOW-1142).
   *
   * A NEW record — nothing held under this id before — takes the signed-in
   * id, or '' for an anonymous download. A record that REPLACES one
   * already held (a re-download, a repair) keeps the stamp it had: a
   * refresh is not a change of owner, and a legacy record with no stamp
   * stays without one, so the claim rule — not a refresh — decides who
   * it belongs to.
   *
   * @param {Object} record The record about to be written.
   * @param {Object|null|undefined} existing The record it replaces, if any.
   * @returns {Object} A copy of `record` with `principal` set (or, for a
   *   legacy predecessor, left absent).
   */
  function stampPrincipal(record, existing) {
    var out = Object.assign({}, record);
    delete out.principal;
    if (existing) {
      if (Object.prototype.hasOwnProperty.call(existing, 'principal')) {
        out.principal = existing.principal;
      }
    } else {
      out.principal = principal();
    }
    return out;
  }

  /**
   * The whole synced marker, as an object keyed by principal (SNOW-1142).
   *
   * The flat array every earlier release wrote is read as `{}` — nobody's
   * — so the first `adopt()` after the deploy re-checks every held area
   * against `ownership()` rather than trusting a list that never said
   * which account it was for. Best-effort: an unreadable IndexedDB is `{}`
   * too, which makes `adopt()` push again — the safe direction, since a
   * redundant push is an update and a skipped one is a missing row.
   *
   * @returns {Promise<Object<string, string[]>>}
   */
  async function _readSyncedAll() {
    try {
      var row = await window.pwaDb?.get('meta:app', SYNCED_KEY);
      var value = row && row.value;
      if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
      return value;
    } catch (_err) {
      return {};
    }
  }

  /**
   * Read the area ids already pushed to `owner`'s account.
   *
   * @param {string} owner The account id.
   * @returns {Promise<string[]>}
   */
  async function _readSynced(owner) {
    var all = await _readSyncedAll();
    var ids = owner ? all[owner] : null;
    return Array.isArray(ids) ? ids : [];
  }

  /**
   * Write `ids` as `owner`'s synced list, leaving every other account's
   * alone. Always a `put` of the whole object: it is small, and nothing
   * else writes it.
   *
   * @param {string} owner
   * @param {string[]} ids
   * @returns {Promise<void>}
   */
  async function _writeSynced(owner, ids) {
    var all = Object.assign({}, await _readSyncedAll());
    all[owner] = ids;
    await window.pwaDb?.put('meta:app', { key: SYNCED_KEY, value: all });
  }

  /**
   * Remember `areaId` as pushed to the signed-in account.
   *
   * @param {string} areaId
   * @returns {Promise<void>}
   */
  async function _markSynced(areaId) {
    var owner = principal();
    if (!owner) return;
    try {
      var existing = await _readSynced(owner);
      if (existing.indexOf(areaId) !== -1) return;
      await _writeSynced(owner, existing.concat([areaId]));
    } catch (_err) {
      // Best-effort — see `_readSyncedAll`.
    }
  }

  /**
   * Forget that `areaId` was pushed to the signed-in account.
   *
   * Called when the account row goes, so that re-downloading the same area
   * later pushes it again rather than assuming a row that is no longer
   * there.
   *
   * @param {string} areaId
   * @returns {Promise<void>}
   */
  async function _unmarkSynced(areaId) {
    var owner = principal();
    if (!owner) return;
    try {
      var existing = await _readSynced(owner);
      if (existing.indexOf(areaId) === -1) return;
      await _writeSynced(
        owner,
        existing.filter(function (id) {
          return id !== areaId;
        }),
      );
    } catch (_err) {
      // Best-effort — see `_readSyncedAll`.
    }
  }

  /**
   * Stamp a LEGACY or ANONYMOUS held record with `owner` — the local half
   * of a claim (SNOW-1142).
   *
   * Conditional inside the write: an entry already stamped with an id is
   * left as it is, so two tabs racing a claim cannot hand one area to two
   * accounts. On one `readModifyWrite` transaction where the store offers
   * it, for the reason every other writer of these whole-array rows uses
   * one (SNOW-959).
   *
   * @param {string} areaId
   * @param {string} owner
   * @returns {Promise<boolean>} Whether the record now carries `owner` —
   *   false when no such record is held here, another account stamped it
   *   first, or the write failed. A claim pushes only on true.
   */
  async function _claimLocal(areaId, owner) {
    var core = self.pwaBasemapDownloadCore;
    var db = window.pwaDb;
    if (!core || !db || !areaId || !owner) return false;
    var isCustom = core.isCustomAreaId(areaId);
    var key = isCustom ? CUSTOM_AREAS_KEY : REGIONS_KEY;
    var matches = function (entry) {
      if (!entry) return false;
      return isCustom ? entry.id === areaId : core.areaIdForRegion(entry.region_id) === areaId;
    };
    var claimed = false;
    var mutate = function (row) {
      var list = Array.isArray(row && row.value) ? row.value : [];
      var touched = false;
      var next = list.map(function (entry) {
        if (!matches(entry)) return entry;
        if (entry.principal === owner) {
          claimed = true;
          return entry;
        }
        if (ownership(entry.principal, owner) !== 'claim') return entry;
        touched = true;
        claimed = true;
        return Object.assign({}, entry, { principal: owner });
      });
      return touched ? { key: key, value: next } : undefined;
    };
    try {
      if (typeof db.readModifyWrite === 'function') {
        await db.readModifyWrite('meta:app', key, mutate);
      } else {
        var written = mutate(await db.get('meta:app', key));
        if (written) await db.put('meta:app', written);
      }
      return claimed;
    } catch (_err) {
      return false;
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
   * @param {number[]} [doneStatuses] Response codes that mean the write is
   *   already applied (SNOW-1132), passed through to the queue.
   * @returns {Promise<boolean>} Whether the operation was accepted for
   *   delivery. Never rejects: a failed enqueue costs a missing account
   *   row, not a failed download, and the download is what the user asked
   *   for.
   */
  async function _enqueue(url, fields, doneStatuses) {
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
        doneStatuses: doneStatuses || [],
      });
      return true;
    } catch (_err) {
      return false;
    }
  }

  /**
   * The account's `shape` for a local record's `type` (SNOW-1136).
   *
   * A drop zone is a circle — its tiles are clipped to it
   * (`basemap_download_core.js`'s `circleBlob`) — and everything else is a
   * box. The inverse is `basemap_manage_core.js`'s `reconcileAreas`, which
   * reads `shape: 'CIRCLE'` back as `type: 'dropzone'`.
   *
   * @param {string|undefined} type
   * @returns {'BOX'|'CIRCLE'}
   */
  function shapeFor(type) {
    return type === 'dropzone' ? 'CIRCLE' : 'BOX';
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
   *   basemapKey?: string|null, name?: string, terrain?: boolean,
   *   type?: string}} record
   *   The area as the local writer has it. `bbox` is `[west, south, east,
   *   north]` in (lon, lat) order, matching the server's own carve-out; it
   *   is required for a custom area and ignored for a region, whose tiles
   *   are computed server-side from its real boundary. SNOW-1072:
   *   `terrain` is whether the download included terrain data, posted as
   *   "true"/"false" so another device repeats the choice. SNOW-1136:
   *   `type` is the local record's kind — 'dropzone' posts `shape:
   *   "CIRCLE"`, anything else (a framed area, a region, an older record
   *   with no type) posts "BOX" — so another device restores a drop zone
   *   as a drop zone rather than as the rectangle around it.
   *   SNOW-1142: `principal` is the stamp on the local record the writer
   *   just stored. When the key is PRESENT the push goes ahead only if
   *   `ownership()` says the record is the signed-in account's own — a
   *   re-download of another account's area keeps that account's stamp
   *   and is not recorded here, and a legacy predecessor (stamp absent,
   *   passed as undefined) is left for `adopt()`'s claim.
   * @returns {Promise<boolean>} Whether the write was queued. Never
   *   rejects — see `_enqueue`.
   */
  async function push(record) {
    if (!isEnabled() || !record || !record.areaId) return false;
    if (
      Object.prototype.hasOwnProperty.call(record, 'principal') &&
      ownership(record.principal, principal()) !== 'own'
    ) {
      return false;
    }
    var queued = await _enqueue(config().downloadSyncUrl, {
      area_id: record.areaId,
      region_id: record.regionId || '',
      // A JSON string, not repeated fields: the server parses one value
      // and validates it as four numbers, so a partially-posted box can
      // never be read as a whole one.
      bbox: Array.isArray(record.bbox) ? JSON.stringify(record.bbox) : '',
      basemap_key: record.basemapKey || '',
      name: record.name || '',
      terrain: record.terrain ? 'true' : 'false',
      shape: shapeFor(record.type),
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
    // SNOW-1132: a 404 means the account has no such area — another
    // device forgot it, or it never got there. Nothing is left to rename,
    // which is done, not a failure to keep on the badge.
    return _enqueue(
      template.replace('__AREA_ID__', encodeURIComponent(areaId)),
      { name: name || '' },
      [404],
    );
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
    // SNOW-1132: a 404 is the row already gone — removed on another
    // device, or never synced — which is the outcome asked for.
    var queued = await _enqueue(
      template.replace('__AREA_ID__', encodeURIComponent(areaId)),
      {},
      [404],
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
   * Resolves `[]` for every unhappy path there is — signed out (403),
   * offline, timed out, refused, or a body that will not parse. The
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
   *   name: string, terrain: boolean, shape: string, created_at: string}>>}
   *   Never rejects.
   */
  async function accountAreas() {
    if (!isEnabled()) return [];
    var url = config().downloadAreasUrl;
    if (!url) return [];
    // Skipped outright while the app is not using the network — that needs
    // no budget to discover, and the sheet opens sooner.
    //
    // SNOW-748: this asks the effective connectivity, not ``navigator.onLine``
    // alone. Under an offline mode — the worker's own latch, or the one the
    // user set from the network menu — the interface is up and no request
    // may leave the app, so the radio answers only half the question. The
    // raw read stays as the fallback for a page where ``pwa_offline.js``
    // has not run.
    var connectivity = window.pwaConnectivity;
    var online = connectivity ? connectivity.isOnline() : navigator.onLine !== false;
    if (!online) return [];
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
   * The name to record on the account for a held area.
   *
   * SNOW-1133: a custom area's display `name` is filled in memory with the
   * localised "Custom area N" when it was never renamed. Posting that froze
   * one language into the account, so the STORED name is sent instead —
   * '' for an unrenamed area. A region carries no `storedName` and its
   * name is its real one.
   *
   * @param {{name?: string, storedName?: string}} area
   * @returns {string}
   */
  function accountName(area) {
    if (!area) return '';
    return typeof area.storedName === 'string' ? area.storedName : area.name || '';
  }

  /**
   * Record one area HELD on this device to the signed-in account, if it
   * may be (SNOW-1142).
   *
   * The one place the ownership rule is applied to a held area — `adopt()`
   * and the Manage downloads sheet's `_reassertHeldAreas` both call this
   * rather than `push()`, so they cannot disagree about whose an area is:
   *
   *   own     → pushed.
   *   claim   → the local record is stamped with the signed-in id FIRST,
   *             and pushed only once that stamp has landed, so a later
   *             account sees the area as foreign and never claims it too.
   *   foreign → skipped. It is still listed and usable here.
   *   none    → skipped: nobody to record it to.
   *
   * The shape exclusions every caller needs are here too: an orphaned
   * bucket (SNOW-612 — a download that failed partway, leaving tiles and
   * no record) has no completed area to describe; a shared base layer
   * (SNOW-860) is not one of the user's downloads and the endpoint's
   * `_AREA_ID_RE` 400s its `base-` prefix, which the mutation queue
   * classifies as permanent and never retries; and a custom area with no
   * stored box cannot be described to another device, so the server
   * refuses it.
   *
   * @param {Object} area An area as `basemap_downloaded_areas.js`'s reader
   *   (or `reconcileAreas` after it) returns it, carrying `principal`.
   * @returns {Promise<boolean>} Whether a push was queued. Never rejects.
   */
  async function recordHeld(area) {
    if (!isEnabled() || !area || !area.id || area.orphaned) return false;
    // An account-only row is not held here: there is no record to stamp.
    if (area.onDevice === false) return false;
    var core = self.pwaBasemapDownloadCore;
    if (core && core.isBaseLayerAreaId(area.id)) return false;
    var isCustom = core ? core.isCustomAreaId(area.id) : false;
    if (isCustom && !Array.isArray(area.bbox)) return false;
    var owner = principal();
    var verdict = ownership(area.principal, owner);
    if (verdict === 'claim') {
      if (!(await _claimLocal(area.id, owner))) return false;
    } else if (verdict !== 'own') {
      return false;
    }
    return push({
      areaId: area.id,
      regionId: isCustom ? '' : String(area.id).replace(/^region-/, ''),
      bbox: area.bbox,
      basemapKey: area.basemapKey,
      name: accountName(area),
      terrain: !!area.terrain,
      // SNOW-1136: box or drop zone — see `shapeFor`.
      type: area.type,
      principal: owner,
    });
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
   * SNOW-1142: only what the signed-in account may record — its own areas,
   * and a legacy or anonymous one it claims (see `recordHeld`). Another
   * account's area held on this shared browser is skipped, and the marker
   * consulted is this account's own.
   *
   * Reads through `window.pwaBasemapDownloads.areas()` rather than the
   * two `meta:app` records directly — that bridge is the single
   * normalising layer, and a second reader here would have to re-derive
   * the record-to-bucket-id mapping it already owns. The cost of sharing
   * that reader is that its list is wider than this loop's: it includes
   * the shared base layers, which are not the user's downloads and which
   * this endpoint rejects, so they are skipped explicitly below.
   *
   * @param {Array<Object>} [known] SNOW-1140: that same list, when the
   *   caller has already read it — used as given, so no second read (and
   *   no second account request) is made.
   * @returns {Promise<number>} How many areas were pushed — 0 when
   *   everything was already synced, which is the common case on every
   *   load after the first.
   */
  async function adopt(known) {
    if (!isEnabled()) return 0;
    var owner = principal();
    if (!owner) return 0;
    var areas;
    // SNOW-1140: a caller that has just read the list hands it in, so the
    // account's areas are not fetched a second time at boot (the Manage
    // downloads sheet reads it once for this and its restore notice).
    if (Array.isArray(known)) {
      areas = known;
    } else {
      try {
        areas = await window.pwaBasemapDownloads?.areas();
      } catch (_err) {
        return 0;
      }
    }
    if (!Array.isArray(areas) || !areas.length) return 0;

    var synced = await _readSynced(owner);
    var pushed = 0;
    for (var i = 0; i < areas.length; i += 1) {
      var area = areas[i];
      if (!area || !area.id || synced.indexOf(area.id) !== -1) continue;
      if (await recordHeld(area)) pushed += 1;
    }
    return pushed;
  }

  self.pwaDownloadsSync = Object.freeze({
    accountName: accountName,
    ownership: ownership,
    principal: principal,
    recordHeld: recordHeld,
    stampPrincipal: stampPrincipal,
    shapeFor: shapeFor,
    isEnabled: isEnabled,
    push: push,
    rename: rename,
    forget: forget,
    accountAreas: accountAreas,
    adopt: adopt,
  });
})();
