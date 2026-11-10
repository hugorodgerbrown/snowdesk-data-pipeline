// @ts-check
/*
 * static/js/offline_journey_core.js — one panel per downloaded area on
 * /offline/: three stages and one verdict (SNOW-1308).
 *
 * The offline report (`offline_audit_core.js`) answers about the app and
 * the map as a whole. What a reader about to lose signal asks is narrower
 * and more useful: *will THIS area work?* So each area the device holds
 * gets a panel above the report, and the panel answers three questions in
 * the order a trip depends on them:
 *
 *   - **The map.** The area's bucket verifies — its style, TileJSON and
 *     sprite (`areaState`, unchanged, SNOW-843/844), its terrain-class
 *     tiles where it was downloaded with them (SNOW-1138) — AND its
 *     basemap tiles are all there (`blobFullyCached` over the record's own
 *     tile template and ground, injected as `tiles`). The report never
 *     checked the tiles themselves; a bucket holding its documents and
 *     half its tiles read ready.
 *   - **The conditions.** The bulletins and weather inside the boundary
 *     were fetched today (`areaContentState` is `fresh`). Stale or never
 *     is a No here, not a caveat: the panel's question is whether the area
 *     is ready, and yesterday's bulletin is not. SNOW-1379: an area that
 *     carries the problem-terrain feed (downloaded with terrain tiles, in
 *     a terrain country) adds a "Problem terrain" line counting the days
 *     saved, from the urls the last sync stamped (`items.problemTerrain`,
 *     `withProblemTerrain`). SNOW-1387: a day not held makes the stage
 *     No (reason `terrain`), so the area reads "Needs a refresh" and Sync
 *     now fetches it again; a line that could not be read makes a Yes
 *     unknown. The stage's own No (stale, never) keeps its reason.
 *   - **User data.** Each trip and pin in the area, by name, Yes only
 *     when every url it needs is in Cache Storage. What "needs" means is
 *     stamped on the record by the sync that fetched them
 *     (`stampContentRecord`, `areaItemExpectation`) — `/api/area-items/`
 *     is never cached, so this page cannot ask offline. No stamp is
 *     unknown, and a signed-out reader gets no stage at all: they have no
 *     things, and an absent stage must not read as a fault.
 *
 *   SNOW-1417: the conditions stage also carries a "SAC tours" line (each
 *   tour's Feature and profile in Cache Storage, `toursLine`) and a
 *   "Community reports" line (the stamped report ids against the ids the
 *   map's overlay cache holds, which the caller reads and passes as
 *   `readings.reportIds`, `reportsLine`). Either short makes the stage No
 *   (`tours-missing`, `reports-missing`), as a missing problem-terrain day
 *   does.
 *
 * The verdict is one plain line, from `verdictFor`:
 *
 *   - `ready` — every stage is Yes.
 *   - `open-map` — the map stage is No. Tiles and documents can only be
 *     fetched where the basemap style is loaded, so the remedy is the
 *     map's Manage downloads sheet, never a tap here.
 *   - `refresh` — another stage is No; the tap runs Sync now.
 *   - `unknown` — a stage could not be read and none is No.
 *
 * A reading that could not be taken is unknown, never a Yes and never a
 * No — the report's rule, kept here.
 *
 * Pure: no DOM and no storage. Every reading arrives as an argument.
 * Reads `pwaOfflineAuditCore` at call time for `areaState`,
 * `areaContentState`, `completableArea` and `chosenAreas`, so the panels
 * and the report cannot disagree about one area.
 *
 * ## The connection panel (SNOW-1415)
 *
 * The header's connection panel asks the same question of every area on
 * every page, where the audit core is not loaded. `base.html` loads this
 * file globally for it. `stagesFromRecord` builds the stages from the
 * stored record alone: the map stage from a `verified` stamp that the
 * tile-capable pages write (`verifiedFromSync` on the map,
 * `verifiedFromStages` on /offline/), the conditions from `contentAt`,
 * and the reader's things from the same stamp. `verdictFor` is unchanged
 * and stays the one verdict. The record-only path never reads the audit
 * core, so it works where that core is absent.
 *
 * ## The Downloads inventory (SNOW-1418)
 *
 * `inventoryFor` lists what one area holds, row by row, for the reveal
 * under its row in the map's Downloads sheet: the map, the bulletin, the
 * weather, the reader's routes, trips and saved places, the SAC tours, the
 * resorts and the community reports. It reads the same stamp as
 * `stagesFromRecord`, and takes what the worker and the overlay cache
 * were read to hold as injected readings (`inventoryUrls` names the urls
 * to ask about).
 */

(function () {
  'use strict';

  /**
   * @typedef {'yes'|'no'|'unknown'} StageStatus
   */

  /**
   * @typedef {Object} ThingRow
   * @property {'trip'|'pin'} kind
   * @property {string} name
   * @property {StageStatus} status
   */

  /**
   * @typedef {Object} TerrainLine
   * @property {StageStatus} status Yes when every day is held.
   * @property {number} held The days in Cache Storage.
   * @property {number} total The days the last sync saved.
   */

  /**
   * @typedef {Object} CountLine
   * @property {StageStatus} status Yes when everything stamped is held.
   * @property {number} held How many are on the device.
   * @property {number} total How many the last sync saved.
   * @property {string|null} [newestAt] Reports only: the newest one's
   *   observed time (SNOW-1417).
   */

  /**
   * @typedef {Object} Stage
   * @property {StageStatus} status
   * @property {string} [reason]
   * @property {ThingRow[]} [rows] User data only.
   * @property {TerrainLine} [terrain] The conditions stage only (SNOW-1379).
   * @property {CountLine} [tours] The conditions stage only (SNOW-1417).
   * @property {CountLine} [reports] The conditions stage only (SNOW-1417).
   */

  /**
   * @typedef {Object} Panel
   * @property {string} id The bucket id.
   * @property {string} name
   * @property {'region'|'dropzone'|'custom'} kind
   * @property {boolean} orphaned
   * @property {string|null} contentAt
   * @property {{map: Stage, conditions: Stage, things: Stage|null}} stages
   * @property {'ready'|'open-map'|'refresh'|'unknown'} verdict
   * @property {boolean} canSync Whether this host can run Sync now for it.
   * @property {number} bytes The recorded size, 0 where none landed.
   */

  // Regions first: a region is what most people download, and the ones
  // they drew themselves are the exceptions. The order the report's Map
  // downloads section used, kept.
  /** @type {Array<'region'|'dropzone'|'custom'>} */
  var KIND_ORDER = ['region', 'dropzone', 'custom'];

  /**
   * The audit core, read at call time.
   *
   * @returns {any}
   */
  function audit() {
    return self.pwaOfflineAuditCore;
  }

  /**
   * Fill ``%(key)s`` placeholders.
   *
   * @param {string} template
   * @param {Record<string, string>} values
   * @returns {string}
   */
  function fill(template, values) {
    return String(template || '').replace(/%\((\w+)\)s/g, function (_m, key) {
      return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : '';
    });
  }

  /**
   * Which kind of download a record is.
   *
   * @param {any} area
   * @returns {'region'|'dropzone'|'custom'}
   */
  function areaKind(area) {
    if (area && area.kind === 'region') return 'region';
    return area && area.type === 'dropzone' ? 'dropzone' : 'custom';
  }

  /**
   * The name an area is spoken of by: its stored name, the sheet's
   * "Custom area N" default from its ordinal (SNOW-1133), or its id.
   *
   * @param {any} area
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function areaName(area, t) {
    if (area.name) return String(area.name);
    if (typeof area.ordinal === 'number' && Number.isFinite(area.ordinal)) {
      return fill(t['default-custom-name'] || 'Custom area %(n)s', { n: String(area.ordinal) });
    }
    return String(area.id);
  }

  /**
   * The ground a record's tiles cover, as the blob `blobFullyCached`
   * walks.
   *
   * A region carries the run's own `z` row spans. A custom area carries
   * `bbox` + `band`, rebuilt the way it was fetched: a drop zone as its
   * circle (`circleBlobForBBox`, SNOW-1136), a framed box as `buildBlob`.
   *
   * @param {any} area
   * @param {any} download `pwaBasemapDownloadCore`.
   * @returns {any} Null where the record names no ground.
   */
  function areaBlob(area, download) {
    if (area.z) return { z: area.z };
    var band = Array.isArray(area.band) ? area.band : null;
    if (!Array.isArray(area.bbox) || !band) return null;
    if (area.type === 'dropzone' && typeof download.circleBlobForBBox === 'function') {
      return download.circleBlobForBBox(area.bbox, band[0], band[1]);
    }
    if (typeof download.buildBlob !== 'function') return null;
    return download.buildBlob(area.bbox, band[0], band[1]);
  }

  /**
   * Whether every basemap tile an area's ground implies is in its bucket.
   *
   * The reading is injected — `download` is `pwaBasemapDownloadCore`,
   * passed by the caller — because static/offline.html loads no such core
   * and this module stays pure. Unknown (null) for: no core, a bucket that
   * did not answer, a record naming no tile template or no ground, or any
   * throw. Never a No drawn from a reading that was not taken.
   *
   * @param {any} area An audit area reading.
   * @param {any} download `pwaBasemapDownloadCore`, or null.
   * @returns {boolean|null}
   */
  function tileReading(area, download) {
    if (!area || !download || typeof download.blobFullyCached !== 'function') return null;
    if (area.bucketReadable === false || area.orphaned) return null;
    // SNOW-1478: every map the area holds, primary first; a record before
    // that ticket reads as one, from its own `template`.
    var templates =
      typeof download.recordBasemaps === 'function'
        ? download
            .recordBasemaps(area)
            .map(function (/** @type {any} */ entry) {
              return entry.template;
            })
            .filter(Boolean)
        : area.template
          ? [area.template]
          : [];
    if (templates.length === 0) return null;
    try {
      var blob = areaBlob(area, download);
      if (!blob) return null;
      return templates.every(function (/** @type {any} */ template) {
        return !!download.blobFullyCached(template, blob, area.entries || []);
      });
    } catch (_err) {
      return null;
    }
  }

  /**
   * The map stage: the bucket verifies AND its tiles are all there.
   *
   * @param {any} area
   * @param {boolean|null|undefined} tiles From `tileReading`.
   * @returns {Stage}
   */
  function mapStage(area, tiles) {
    var core = audit();
    if (!core) return { status: 'unknown' };
    var state = core.areaState(area).status;
    if (state === 'ready') {
      if (tiles === true) return { status: 'yes' };
      if (tiles === false) return { status: 'no', reason: 'tiles' };
      return { status: 'unknown', reason: 'tiles-unread' };
    }
    if (state === 'unreadable' || state === 'unverifiable') {
      return { status: 'unknown', reason: state };
    }
    // `orphaned`, `incomplete`, `missing`.
    return { status: 'no', reason: state };
  }

  /**
   * The conditions stage: today's bulletins and weather are here.
   *
   * @param {any} area
   * @param {string} [now]
   * @returns {Stage}
   */
  function conditionsStage(area, now) {
    var core = audit();
    if (!core) return { status: 'unknown' };
    var content = core.areaContentState(area, now);
    if (content === 'fresh') return { status: 'yes' };
    if (content === 'stale') return { status: 'no', reason: 'stale' };
    if (content === 'never') return { status: 'no', reason: 'never' };
    // `none`: the tiles do not verify (the map stage already says so) or
    // an unparseable stamp. Neither is evidence about the content.
    return { status: 'unknown' };
  }

  /**
   * Whether a reader is signed in, from the account the device's queue is
   * set for (`mutations.principal`, null when signed out).
   *
   * @param {any} readings
   * @returns {boolean}
   */
  function isSignedIn(readings) {
    var p = readings && readings.currentPrincipal;
    return typeof p === 'string' && p !== '' && p !== 'anonymous' && p !== 'unknown';
  }

  /**
   * Every url the shell cache holds, or null where it could not be read.
   *
   * @param {any} readings
   * @returns {Map<string, any>|null} By url, each to its entry.
   */
  function shellUrls(readings) {
    if (!readings || readings.cachesReadable === false || readings.failure) return null;
    /** @type {Map<string, any>} */
    var urls = new Map();
    (Array.isArray(readings.shellEntries) ? readings.shellEntries : []).forEach(
      /** @param {any} entry */ function (entry) {
        if (entry && entry.url) urls.set(entry.url, entry);
      },
    );
    return urls;
  }

  /**
   * Whether one of your things' urls will open offline for this reader.
   *
   * Presence is not enough for the principal-scoped families — a trip page,
   * its route, a pin's place sheet (`isPrincipalScoped`): the worker
   * serves those only under a matching `X-SW-Principal`
   * (`_principalMatches`), so one cached for another account is a No. A
   * stamp whose read did not come back is unknown. Public weather sheets
   * need only be there (SNOW-1308 review).
   *
   * @param {string} url Absolute.
   * @param {Map<string, any>} held
   * @param {string|null} principal
   * @returns {StageStatus}
   */
  function urlState(url, held, principal) {
    var entry = held.get(url);
    if (!entry) return 'no';
    var core = audit();
    if (!core || typeof core.isPrincipalScoped !== 'function') return 'unknown';
    if (!core.isPrincipalScoped(url)) return 'yes';
    if (entry.stampUnread) return 'unknown';
    return core.principalMatches(entry.principal, principal) ? 'yes' : 'no';
  }

  /**
   * One url as the cache keys it — absolute, on this origin.
   *
   * @param {string} url
   * @param {string} origin
   * @returns {string}
   */
  function absolute(url, origin) {
    try {
      return new URL(url, origin || 'http://localhost').toString();
    } catch (_err) {
      return url;
    }
  }

  /**
   * The conditions stage's "Problem terrain" line: how many of the days
   * the last sync saved are in Cache Storage (SNOW-1379).
   *
   * Only for an area that carries the feed: one downloaded with terrain
   * tiles (`area.terrain`) whose last sync stamped a non-empty
   * `items.problemTerrain` — the stamp is empty for an area outside the
   * terrain countries, and absent for one not synced since this ticket.
   * The feed is public, so presence is enough (`urlState`). A cache that
   * could not be read counts nothing and is unknown.
   *
   * @param {any} area
   * @param {{urls: Map<string, any>|null, origin: string,
   *   principal?: string|null}} ctx
   * @returns {TerrainLine|null} Null where the area carries no feed.
   */
  function problemTerrainLine(area, ctx) {
    if (!area || !area.terrain) return null;
    var stamped = area.items && area.items.problemTerrain;
    if (!Array.isArray(stamped) || stamped.length === 0) return null;
    var total = stamped.length;
    if (!ctx.urls) return { status: 'unknown', held: 0, total: total };
    var held = /** @type {Map<string, any>} */ (ctx.urls);
    var count = stamped.filter(function (/** @type {string} */ url) {
      return urlState(absolute(url, ctx.origin), held, ctx.principal || null) === 'yes';
    }).length;
    return { status: count === total ? 'yes' : 'no', held: count, total: total };
  }

  /**
   * The conditions stage's "SAC tours" line: how many of the tours the
   * last sync saved will open offline (SNOW-1417).
   *
   * A tour opens when its Feature and its height profile are both in
   * Cache Storage. Public, so presence is enough (`urlState`). Null where
   * the last sync stamped no tours: an area with none, or one not synced
   * since this ticket, or a page without the ski-touring layer.
   *
   * @param {any} area
   * @param {{urls: Map<string, any>|null, origin: string,
   *   principal?: string|null}} ctx
   * @returns {CountLine|null}
   */
  function toursLine(area, ctx) {
    var stamped = area && area.items && area.items.tours;
    if (!Array.isArray(stamped) || stamped.length === 0) return null;
    var total = stamped.length;
    if (!ctx.urls) return { status: 'unknown', held: 0, total: total };
    var held = /** @type {Map<string, any>} */ (ctx.urls);
    var count = stamped.filter(function (/** @type {any} */ tour) {
      var urls = tour && Array.isArray(tour.urls) ? tour.urls : [];
      return urls.length > 0 && urls.every(function (/** @type {string} */ url) {
        return urlState(absolute(url, ctx.origin), held, ctx.principal || null) === 'yes';
      });
    }).length;
    return { status: count === total ? 'yes' : 'no', held: count, total: total };
  }

  /**
   * The conditions stage's "Community reports" line: how many of the
   * reports inside the area the last sync saved are in the map's overlay
   * cache (SNOW-1417).
   *
   * Reports are not in Cache Storage: the map keeps the whole feed in
   * IndexedDB (`pwaMapOverlayCache`), so the caller reads it and passes
   * the ids it holds. Null where the last sync stamped no reports field;
   * an empty stamp is a Yes with nothing to count.
   *
   * @param {any} area
   * @param {Set<string>|null|undefined} ids The report ids the overlay
   *   cache holds, or null when it could not be read.
   * @returns {CountLine|null}
   */
  function reportsLine(area, ids) {
    var items = area && area.items;
    var stamped = items && items.reports;
    if (!Array.isArray(stamped)) return null;
    var total = stamped.length;
    var newestAt = typeof items.reportsNewestAt === 'string' ? items.reportsNewestAt : null;
    if (!ids) return { status: 'unknown', held: 0, total: total, newestAt: newestAt };
    var have = /** @type {Set<string>} */ (ids);
    var count = stamped.filter(function (/** @type {any} */ report) {
      return !!report && have.has(String(report.id));
    }).length;
    return { status: count === total ? 'yes' : 'no', held: count, total: total, newestAt: newestAt };
  }

  /**
   * The "User data" stage: each trip and pin, Yes only when every url
   * it needs is cached.
   *
   * @param {any} area
   * @param {{signedIn: boolean, urls: Map<string, any>|null, origin: string,
   *   principal?: string|null}} ctx
   * @returns {Stage|null} Null for a signed-out reader: no stage at all.
   */
  function thingsStage(area, ctx) {
    if (!ctx.signedIn) return null;
    var items = area.items;
    if (!items || !Array.isArray(items.trips) || !Array.isArray(items.pins)) {
      return { status: 'unknown', reason: 'unrecorded', rows: [] };
    }
    // SNOW-1308 review: the names were written by whoever SYNCED, which
    // need not be the area's downloader (`area.principal`) — another
    // account signed in on this device can press Sync now. So the guard is
    // the principal stamped with the items, and nothing is named unless it
    // is this reader's: a missing, empty or different stamp is unknown.
    var stamped = typeof items.principal === 'string' ? items.principal : '';
    if (!stamped || stamped !== ctx.principal) {
      return { status: 'unknown', reason: 'other-account', rows: [] };
    }
    /** @type {ThingRow[]} */
    var rows = [];
    /**
     * @param {'trip'|'pin'} kind
     * @param {any} item
     */
    var add = function (kind, item) {
      if (!item) return;
      var urls = Array.isArray(item.urls) ? item.urls : [];
      /** @type {StageStatus} */
      var status = 'unknown';
      if (urls.length > 0 && ctx.urls) {
        var held = /** @type {Map<string, any>} */ (ctx.urls);
        var states = urls.map(function (/** @type {string} */ url) {
          return urlState(absolute(url, ctx.origin), held, ctx.principal || null);
        });
        status =
          states.indexOf('no') >= 0 ? 'no' : states.indexOf('unknown') >= 0 ? 'unknown' : 'yes';
      }
      rows.push({ kind: kind, name: typeof item.name === 'string' ? item.name : '', status: status });
    };
    items.trips.forEach(function (/** @type {any} */ trip) {
      add('trip', trip);
    });
    items.pins.forEach(function (/** @type {any} */ pin) {
      add('pin', pin);
    });
    if (rows.length === 0) return { status: 'yes', reason: 'none-here', rows: rows };
    var statuses = rows.map(function (row) {
      return row.status;
    });
    if (statuses.indexOf('no') >= 0) return { status: 'no', rows: rows };
    if (statuses.indexOf('unknown') >= 0) return { status: 'unknown', rows: rows };
    return { status: 'yes', rows: rows };
  }

  /**
   * The one line a panel leads with.
   *
   * A map No outranks every other answer, because its remedy is somewhere
   * else: tiles can only be fetched where the style is loaded. Any other
   * No outranks an unknown, because the tap that fixes it is right here.
   *
   * @param {{map: Stage, conditions: Stage, things: Stage|null}} stages
   * @returns {'ready'|'open-map'|'refresh'|'unknown'}
   */
  function verdictFor(stages) {
    var list = [stages.map, stages.conditions];
    if (stages.things) list.push(stages.things);
    // SNOW-1483: a map missing only its terrain data is one Sync mends
    // (it fetches the terrain tiles alone), so the tap is right here.
    if (stages.map.status === 'no' && stages.map.reason !== 'terrain') return 'open-map';
    var any = /** @param {StageStatus} status */ function (status) {
      return list.some(function (stage) {
        return stage.status === status;
      });
    };
    if (any('no')) return 'refresh';
    if (any('unknown')) return 'unknown';
    return 'ready';
  }

  /**
   * A "tap to fix" verdict only where a tap can fix it.
   *
   * Sync now needs an area whose tiles verify (`completableArea`); one
   * whose map stage could not be read, with another stage No, would
   * otherwise promise a tap that is never offered. It reads "Can't tell"
   * instead (SNOW-1308 review).
   *
   * @param {'ready'|'open-map'|'refresh'|'unknown'} verdict
   * @param {boolean} completable
   * @returns {'ready'|'open-map'|'refresh'|'unknown'}
   */
  function tapCanFix(verdict, completable) {
    return verdict === 'refresh' && !completable ? 'unknown' : verdict;
  }

  /**
   * A stage with its "Problem terrain" line, where the area has one, and
   * the line folded into the stage's answer (SNOW-1387, Hugo 2 Nov 2026):
   * an area missing a day of the bulletin's slopes is not ready for
   * offline, so a No line makes the stage No and the verdict "Needs a
   * refresh". The stage's own No outranks it and keeps its reason (the
   * bulletins themselves are stale, which the note says); an unread line
   * makes a Yes unknown, never a Yes. A `terrain` reason has no note of
   * its own: the line under the stage already says how many days are
   * saved.
   *
   * @param {Stage} stage
   * @param {TerrainLine|null} line
   * @returns {Stage}
   */
  function withTerrainLine(stage, line) {
    if (!line) return stage;
    var out = Object.assign({}, stage, { terrain: line });
    if (stage.status === 'no') return out;
    if (line.status === 'no') return Object.assign(out, { status: 'no', reason: 'terrain' });
    if (line.status === 'unknown' && stage.status === 'yes') {
      return Object.assign(out, { status: 'unknown' });
    }
    return out;
  }

  /**
   * A stage with a tours or reports line folded in (SNOW-1417), by the
   * same rule as the problem-terrain line (`withTerrainLine`): a No line
   * makes a stage that is not already No say No with the line's reason;
   * an unread line makes a Yes unknown.
   *
   * @param {Stage} stage
   * @param {'tours'|'reports'} key
   * @param {CountLine|null} line
   * @param {string} reason `tours-missing` or `reports-missing`.
   * @returns {Stage}
   */
  function withCountLine(stage, key, line, reason) {
    if (!line) return stage;
    /** @type {Stage} */
    var out = Object.assign({}, stage);
    out[key] = line;
    if (stage.status === 'no') return out;
    if (line.status === 'no') return Object.assign(out, { status: 'no', reason: reason });
    if (line.status === 'unknown' && stage.status === 'yes') {
      return Object.assign(out, { status: 'unknown' });
    }
    return out;
  }

  /**
   * @typedef {Object} Verified
   * @property {'yes'|'no'|null} [map] Whether the area's tiles verified.
   * @property {'yes'|'no'|null} [content] Whether everything the area's
   *   content plan named, the reader's own things included, landed.
   * @property {string} [reason] Why the map is No, where it is.
   * @property {string} [principal] The account the content answer was
   *   read for (the `pwa-user-id` meta). A content answer is only ever
   *   shown back to that account.
   * @property {string} at When the reading was taken (ISO).
   */

  /**
   * One stamp answer, narrowed: only Yes and No are claims.
   *
   * @param {unknown} value
   * @returns {'yes'|'no'|null}
   */
  function answer(value) {
    return value === 'yes' || value === 'no' ? value : null;
  }

  /**
   * SNOW-1415: what one map-page sync learned, as a `verified` stamp.
   *
   * `syncArea`'s `tiles` is the map half. `'ok'` and `'none'` say only
   * that the render dependencies are established
   * (docs/decisions/a-pre-departure-sync-is-unconditional.md), so a Yes
   * also needs `tilesHeld === true`: every tile of the area's grid is in
   * its bucket (`tileReading`). `tilesHeld === false` is a No for the
   * tiles; null is no reading, and leaves the map answer as it was.
   * `'absent'`, `'failed'` and `'incomplete'` are No, and `'unknown'` is
   * no claim at all. `content` is the content half's own answer, stamped
   * for `principal` and only when there is one (a signed-out sync has no
   * things of its own to answer for).
   *
   * @param {{tiles?: string, tilesHeld?: boolean|null, content?: boolean}|null|undefined} result
   * @param {string} at
   * @param {string} [principal] The `pwa-user-id` the sync ran as.
   * @returns {Verified|null} Null when the sync claimed nothing.
   */
  function verifiedFromSync(result, at, principal) {
    if (!result) return null;
    /** @type {Verified} */
    var out = { at: at };
    var depsOk = result.tiles === 'ok' || result.tiles === 'none';
    if (depsOk && result.tilesHeld === true) out.map = 'yes';
    else if (depsOk && result.tilesHeld === false) {
      out.map = 'no';
      out.reason = 'tiles';
    } else if (
      result.tiles === 'absent' ||
      result.tiles === 'failed' ||
      result.tiles === 'incomplete'
    ) {
      out.map = 'no';
      out.reason = result.tiles === 'incomplete' ? 'tiles' : result.tiles;
    }
    // An absent bucket is never fetched into, so its content answer is
    // no reading of the content at all.
    if (result.tiles !== 'absent' && principal) {
      out.content = result.content ? 'yes' : 'no';
      out.principal = principal;
    }
    return out.map || out.content ? out : null;
  }

  /**
   * SNOW-1415: what one /offline/ panel read, as a `verified` stamp.
   *
   * The panel's map stage is the full reading (bucket and tiles); its
   * things stage is the cache walk over the urls the last sync stamped.
   * Unknown stages claim nothing, so a host that cannot read tiles
   * (static/offline.html) never overwrites what the map page learned.
   *
   * @param {{map: Stage, things: Stage|null}} stages
   * @param {string} at
   * @param {string} [principal] The `pwa-user-id` the page was rendered
   *   for. With none, the things answer is not stamped.
   * @returns {Verified|null}
   */
  function verifiedFromStages(stages, at, principal) {
    if (!stages) return null;
    /** @type {Verified} */
    var out = { at: at };
    var map = stages.map ? answer(stages.map.status) : null;
    if (map) out.map = map;
    if (map === 'no' && stages.map.reason) out.reason = stages.map.reason;
    var things = stages.things ? answer(stages.things.status) : null;
    if (things && principal) {
      out.content = things;
      out.principal = principal;
    }
    return out.map || out.content ? out : null;
  }

  /**
   * SNOW-1415: one record with a `verified` stamp merged in.
   *
   * Merged field by field, so a reading that claims only the content (a
   * map-page sync that could not judge the tiles) keeps the map answer an
   * earlier reading gave. A map Yes drops a stale map-No reason.
   *
   * Synchronous, because every caller runs it inside a `readModifyWrite`
   * mutator, which must not await. Device-only: `downloads_sync.js` sends
   * a whitelist of fields, and this is not one of them.
   *
   * @param {any} entry The stored record.
   * @param {Verified|null} verified
   * @returns {any} A new record, or the entry itself when there is nothing
   *   to stamp.
   */
  function stampVerifiedRecord(entry, verified) {
    if (!entry || !verified) return entry;
    var prior = entry.verified && typeof entry.verified === 'object' ? entry.verified : {};
    var next = Object.assign({}, prior, { at: verified.at });
    if (verified.map) {
      next.map = verified.map;
      if (verified.map === 'no' && verified.reason) next.reason = verified.reason;
      else delete next.reason;
    }
    if (verified.content) {
      next.content = verified.content;
      next.principal = verified.principal || '';
    }
    return Object.assign({}, entry, { verified: next });
  }

  /**
   * SNOW-1415: the stages of one area from its stored record alone.
   *
   * The connection panel runs on every page, where neither the audit's
   * readings nor the tile arithmetic exist. So the pages that CAN read
   * them stamp what they learned on the record (`verified`, from
   * `verifiedFromSync` and `verifiedFromStages`), and every other page
   * reads the stamp back through here. `verdictFor` then gives the same
   * one-line answer the /offline/ panels give.
   *
   *   - **map** — the stamp's map answer. No stamp is unknown, which is
   *     the honest answer for a record no tile-capable page has read.
   *   - **conditions** — the record's own `contentAt`, by calendar day,
   *     as `areaContentState` reads it; a fetch that fell short
   *     (`contentIncomplete`) makes a fresh stamp No.
   *   - **things** — the stamp's content answer, signed-in readers only,
   *     and only when it was read for this reader's account
   *     (`verified.principal`). Another account's answer is unknown here,
   *     as `thingsStage` treats another account's items on /offline/.
   *
   * SNOW-1483: `terrainExpected` is whether this area should hold the
   * terrain-class tiles (the page fetches terrain data and the area's
   * ground meets their coverage). With it set, a record saved without
   * `terrain` has a map stage of No, reason `terrain`, in place of a Yes,
   * so the dot agrees with the inventory's Terrain row. A map No or
   * unknown keeps its own answer. 3D (elevation) is never read.
   *
   * @param {any} record A `basemap.regions` or `basemap.customAreas` row.
   * @param {string|Date} [now]
   * @param {{signedIn?: boolean, principal?: string, terrainExpected?: boolean}} [options]
   * @returns {{map: Stage, conditions: Stage, things: Stage|null}}
   */
  function stagesFromRecord(record, now, options) {
    var opts = options || {};
    var r = record || {};
    var v = r.verified && typeof r.verified === 'object' ? r.verified : null;

    /** @type {Stage} */
    var map = { status: 'unknown', reason: 'unverified' };
    var mapAnswer = v ? answer(v.map) : null;
    if (mapAnswer === 'yes') map = { status: 'yes' };
    else if (mapAnswer === 'no') map = { status: 'no', reason: String(v.reason || 'tiles') };
    if (map.status === 'yes' && opts.terrainExpected && !r.terrain) {
      map = { status: 'no', reason: 'terrain' };
    }

    /** @type {Stage} */
    var conditions;
    if (!r.contentAt) {
      conditions = { status: 'no', reason: 'never' };
    } else {
      var fetched = new Date(r.contentAt);
      var today = now ? new Date(now) : new Date();
      if (Number.isNaN(fetched.getTime()) || Number.isNaN(today.getTime())) {
        conditions = { status: 'unknown' };
      } else if (fetched.toDateString() !== today.toDateString()) {
        conditions = { status: 'no', reason: 'stale' };
      } else if (r.contentIncomplete) {
        conditions = { status: 'no', reason: 'incomplete' };
      } else {
        conditions = { status: 'yes' };
      }
    }

    /** @type {Stage|null} */
    var things = null;
    if (opts.signedIn) {
      var ours = !!(v && opts.principal && v.principal === opts.principal);
      var contentAnswer = ours ? answer(v.content) : null;
      if (contentAnswer === 'yes') things = { status: 'yes', rows: [] };
      else if (contentAnswer === 'no') things = { status: 'no', reason: 'incomplete', rows: [] };
      else things = { status: 'unknown', rows: [] };
    }
    return { map: map, conditions: conditions, things: things };
  }

  /**
   * @typedef {'yes'|'partial'|'missing'|'unknown'} InventoryStatus
   */

  /**
   * @typedef {Object} InventoryRow
   * @property {'map'|'terrain'|'bulletin'|'weather'|'routes'|'trips'|'places'|'tours'|'resorts'|'reports'} key
   * @property {InventoryStatus} status `partial` is what a sync fixes;
   *   `missing` is the map or terrain, which only a fresh download fixes.
   * @property {number} [count] A count row's stamped total.
   * @property {string} [at] The bulletin and weather rows: `contentAt`.
   */

  /**
   * One stage status as an inventory status. A content No is something a
   * sync fixes, so it is partial; the map's No is missing.
   *
   * @param {Stage|null} stage
   * @param {boolean} [isMap]
   * @returns {InventoryStatus}
   */
  function fromStage(stage, isMap) {
    if (!stage) return 'unknown';
    if (stage.status === 'yes') return 'yes';
    if (stage.status === 'no') return isMap ? 'missing' : 'partial';
    return 'unknown';
  }

  /**
   * A count row's status from what the device was read to hold.
   *
   * Yes when every stamped thing is held, partial when one is not. Where
   * nothing was read (`check` answers null), the row follows its stage,
   * so it never says No on a reading it did not take.
   *
   * @param {any[]} stamped
   * @param {function(any): (boolean|null)} check Held, not held, or unread.
   * @param {InventoryStatus} fallback
   * @returns {InventoryStatus}
   */
  function countStatus(stamped, check, fallback) {
    var unread = false;
    for (var i = 0; i < stamped.length; i++) {
      var held = check(stamped[i]);
      if (held === false) return 'partial';
      if (held === null) unread = true;
    }
    return unread ? fallback : 'yes';
  }

  /**
   * SNOW-1418: what one downloaded area holds, row by row — the inventory
   * the Downloads sheet reveals under an area's row.
   *
   * Built on `stagesFromRecord`, so a row's verdict dot and the inventory
   * under it read the same stamp. The map, bulletin and weather rows are
   * always there. A count row is there only when the last sync stamped its
   * field, so a record synced before its field existed lists less rather
   * than a zero it never counted. Routes, trips and saved places are the
   * reader's own: shown only signed in, and only when the account that
   * stamped them (`items.principal`) is this reader's. Saved places only
   * when the area has some.
   *
   * The readings are optional and injected by the caller: `held`, the
   * absolute urls the worker said it would serve offline (SNOW-1416's
   * `held-content`), and `overlayIds`, the route and report ids the
   * map's overlay cache holds. Absent or null, a row follows its stage.
   * `terrainAvailable` adds the terrain row: the map's status when the
   * area was saved with terrain data, missing when it was not.
   *
   * @param {any} record A `basemap.regions` or `basemap.customAreas` row.
   * @param {string|Date} [now]
   * @param {{signedIn?: boolean, principal?: string, origin?: string,
   *   terrainAvailable?: boolean, held?: Set<string>|null,
   *   overlayIds?: {routes?: Set<string>|null, reports?: Set<string>|null}|null}} [options]
   * @returns {InventoryRow[]}
   */
  function inventoryFor(record, now, options) {
    var opts = options || {};
    var r = record || {};
    // The map row is the map itself: terrain has its own row below, so
    // the map stage is read without it.
    var stages = stagesFromRecord(r, now, Object.assign({}, opts, { terrainExpected: false }));
    var items = r.items && typeof r.items === 'object' ? r.items : {};
    var held = opts.held instanceof Set ? opts.held : null;
    var overlays = opts.overlayIds || {};
    var origin = opts.origin || '';
    var conditions = fromStage(stages.conditions);
    var things = fromStage(stages.things);
    var at = typeof r.contentAt === 'string' ? r.contentAt : undefined;

    /** @param {any} thing */
    var urlsHeld = function (thing) {
      var urls = thing && Array.isArray(thing.urls) ? thing.urls : [];
      if (!held || urls.length === 0) return null;
      var set = /** @type {Set<string>} */ (held);
      return urls.every(function (/** @type {string} */ url) {
        return set.has(absolute(url, origin));
      });
    };
    /**
     * @param {Set<string>|null|undefined} ids
     * @param {string} field
     */
    var idHeld = function (ids, field) {
      return function (/** @type {any} */ thing) {
        if (!(ids instanceof Set) || !thing || !thing[field]) return null;
        return ids.has(String(thing[field]));
      };
    };

    /** @type {InventoryRow[]} */
    var rows = [
      { key: 'map', status: fromStage(stages.map, true) },
    ];
    // The terrain-class tiles, on their own row where this page can fetch
    // them. They ride in the map's bucket and its reading; an area saved
    // without them lacks them until it is downloaded again.
    if (opts.terrainAvailable) {
      rows.push({ key: 'terrain', status: r.terrain ? rows[0].status : 'missing' });
    }
    rows.push(
      { key: 'bulletin', status: conditions, at: at },
      { key: 'weather', status: conditions, at: at },
    );
    var ours =
      !!opts.signedIn &&
      typeof items.principal === 'string' &&
      items.principal !== '' &&
      items.principal === opts.principal;
    if (ours && Array.isArray(items.routes)) {
      rows.push({
        key: 'routes',
        status: countStatus(items.routes, idHeld(overlays.routes, 'uuid'), things),
        count: items.routes.length,
      });
    }
    if (ours && Array.isArray(items.trips)) {
      rows.push({
        key: 'trips',
        status: countStatus(items.trips, urlsHeld, things),
        count: items.trips.length,
      });
    }
    if (ours && Array.isArray(items.pins) && items.pins.length > 0) {
      rows.push({
        key: 'places',
        status: countStatus(items.pins, urlsHeld, things),
        count: items.pins.length,
      });
    }
    if (Array.isArray(items.tours)) {
      rows.push({
        key: 'tours',
        status: countStatus(items.tours, urlsHeld, conditions),
        count: items.tours.length,
      });
    }
    if (Array.isArray(items.resorts)) {
      rows.push({
        key: 'resorts',
        status: countStatus(items.resorts, urlsHeld, conditions),
        count: items.resorts.length,
      });
    }
    if (Array.isArray(items.reports)) {
      rows.push({
        key: 'reports',
        status: countStatus(items.reports, idHeld(overlays.reports, 'id'), conditions),
        count: items.reports.length,
      });
    }
    return rows;
  }

  /**
   * SNOW-1418: every url an area's inventory asks the worker about — the
   * stamped urls of its trips, pins, tours and resorts, absolute.
   *
   * @param {any} record
   * @param {string} origin
   * @returns {string[]}
   */
  function inventoryUrls(record, origin) {
    var items = record && record.items && typeof record.items === 'object' ? record.items : {};
    /** @type {string[]} */
    var out = [];
    ['trips', 'pins', 'tours', 'resorts'].forEach(function (field) {
      var list = Array.isArray(items[field]) ? items[field] : [];
      list.forEach(function (/** @type {any} */ thing) {
        (thing && Array.isArray(thing.urls) ? thing.urls : []).forEach(function (/** @type {string} */ url) {
          var href = absolute(url, origin);
          if (out.indexOf(href) < 0) out.push(href);
        });
      });
    });
    return out;
  }

  /**
   * Every panel, in paint order.
   *
   * @param {any} readings What `offline_audit.js` collected.
   * @param {Record<string, string>} t The journey's strings.
   * @param {{tiles?: Record<string, boolean|null>, origin?: string,
   *   canFetch?: boolean}} [options] `tiles` is each area's
   *   `tileReading`, by id; `canFetch` whether this host can run Sync now.
   * @returns {Panel[]}
   */
  function buildPanels(readings, t, options) {
    var core = audit();
    var opts = options || {};
    var r = readings || {};
    if (!core) return [];
    var strings = t || {};
    var tiles = opts.tiles || {};
    var failed = !!r.failure;
    var ctx = {
      signedIn: isSignedIn(r),
      urls: shellUrls(r),
      origin: opts.origin || '',
      principal: typeof r.currentPrincipal === 'string' ? r.currentPrincipal : null,
    };
    // SNOW-1417: the report ids the map's overlay cache holds, read by
    // the caller; absent is unread.
    /** @type {Set<string>|null} */
    var reportIds = r.reportIds instanceof Set ? r.reportIds : null;
    /** @type {any[]} */
    var areas = core.chosenAreas(r);
    /** @type {Panel[]} */
    var panels = [];
    KIND_ORDER.forEach(function (kind) {
      areas
        .filter(function (area) {
          return areaKind(area) === kind;
        })
        .forEach(function (area) {
          /** @type {{map: Stage, conditions: Stage, things: Stage|null}} */
          var stages = failed
            ? {
                map: { status: 'unknown' },
                conditions: { status: 'unknown' },
                things: ctx.signedIn ? { status: 'unknown', rows: [] } : null,
              }
            : {
                map: mapStage(area, tiles[area.id]),
                conditions: withCountLine(
                  withCountLine(
                    withTerrainLine(conditionsStage(area, r.now), problemTerrainLine(area, ctx)),
                    'tours',
                    toursLine(area, ctx),
                    'tours-missing',
                  ),
                  'reports',
                  reportsLine(area, reportIds),
                  'reports-missing',
                ),
                things: thingsStage(area, ctx),
              };
          panels.push({
            id: String(area.id),
            name: areaName(area, strings),
            kind: kind,
            orphaned: !!area.orphaned,
            contentAt: typeof area.contentAt === 'string' ? area.contentAt : null,
            stages: stages,
            verdict: tapCanFix(verdictFor(stages), !failed && core.completableArea(area, r.now)),
            canSync: !!opts.canFetch && !failed && core.completableArea(area, r.now),
            bytes: typeof area.bytes === 'number' ? area.bytes : 0,
          });
        });
    });
    return panels;
  }

  self.pwaOfflineJourneyCore = Object.freeze({
    buildPanels: buildPanels,
    verdictFor: verdictFor,
    stagesFromRecord: stagesFromRecord,
    inventoryFor: inventoryFor,
    inventoryUrls: inventoryUrls,
    verifiedFromSync: verifiedFromSync,
    verifiedFromStages: verifiedFromStages,
    stampVerifiedRecord: stampVerifiedRecord,
    mapStage: mapStage,
    conditionsStage: conditionsStage,
    thingsStage: thingsStage,
    problemTerrainLine: problemTerrainLine,
    toursLine: toursLine,
    reportsLine: reportsLine,
    withCountLine: withCountLine,
    tileReading: tileReading,
    isSignedIn: isSignedIn,
    areaName: areaName,
  });
})();
