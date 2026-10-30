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
 *     is ready, and yesterday's bulletin is not.
 *   - **Your things.** Each trip and pin in the area, by name, Yes only
 *     when every url it needs is in Cache Storage. What "needs" means is
 *     stamped on the record by the sync that fetched them
 *     (`stampContentRecord`, `areaItemExpectation`) — `/api/area-items/`
 *     is never cached, so this page cannot ask offline. No stamp is
 *     unknown, and a signed-out reader gets no stage at all: they have no
 *     things, and an absent stage must not read as a fault.
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
 * Pure: no DOM and no storage. Every reading arrives as an argument;
 * `offline_journey.js` is the only caller. Reads `pwaOfflineAuditCore`
 * at call time for `areaState`, `areaContentState`, `completableArea` and
 * `chosenAreas`, so the panels and the report cannot disagree about one
 * area.
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
   * @typedef {Object} Stage
   * @property {StageStatus} status
   * @property {string} [reason]
   * @property {ThingRow[]} [rows] Your things only.
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
    if (!area.template) return null;
    try {
      var blob = areaBlob(area, download);
      if (!blob) return null;
      return !!download.blobFullyCached(area.template, blob, area.entries || []);
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
   * The "your things" stage: each trip and pin, Yes only when every url
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
    if (stages.map.status === 'no') return 'open-map';
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
                conditions: conditionsStage(area, r.now),
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
    mapStage: mapStage,
    conditionsStage: conditionsStage,
    thingsStage: thingsStage,
    tileReading: tileReading,
    isSignedIn: isSignedIn,
    areaName: areaName,
  });
})();
