// @ts-check
/*
 * static/js/offline_audit_core.js — turn one set of raw storage readings
 * into the offline-content report (SNOW-907).
 *
 * The report answers one question a user can act on — *will the map open
 * when the signal goes?* — and then shows its working, so a "no" can be
 * debugged rather than merely believed.
 *
 * ## Why this exists
 *
 * What makes Snowdesk work offline is spread across four stores that know
 * nothing about each other, and every surface that reports on one of them
 * reports only its own half:
 *
 *   - the page HTML and the shell's JS/CSS, in the versioned shell cache;
 *   - a downloaded area's tiles and render dependencies, in that area's
 *     pinned bucket;
 *   - the map's data feeds, split between the shell cache and the
 *     ``data:*`` IndexedDB stores;
 *   - anything still queued in ``queue:mutations``.
 *
 * A user who downloads a region and then cannot open the app is not wrong
 * about the region. A download is irrelevant if the map page's own HTML
 * was never cached, or was cached under a different account — ``sw.js``'s
 * ``_networkFirstFallback`` refuses an entry whose ``X-SW-Principal``
 * stamp does not match the principal signed in now, and falls through to
 * ``offline.html`` without saying so anywhere. Nothing on the device
 * reported either condition before this module.
 *
 * ## Two rules the whole report follows
 *
 * **A reading that could not be taken is ``unknown``, never ``ok``.** The
 * report is read by someone who has already been let down once by a
 * surface that said everything was fine. Every check therefore carries
 * four states, and the absence of evidence takes its own one — a browser
 * with no ``storage.estimate()``, a device whose IndexedDB would not
 * open, an area whose record names no dependencies. ``unknown`` never
 * counts towards the verdict in either direction.
 *
 * **The verdict is the worst thing that is true, said plainly.** One
 * line, in the user's terms rather than the storage layer's: the page
 * either opens or it does not, and if it does not, the reason names the
 * single thing to do about it.
 *
 * Pure: no DOM, no fetch, no storage. Every reading arrives as an
 * argument, collected by ``offline_audit.js``, which is also the only
 * caller. Split out for the same reason every other ``*_core.js`` is —
 * the arithmetic is what needs testing and the DOM half is not where you
 * want to test it.
 */

(function () {
  'use strict';

  /** @typedef {'ok'|'warn'|'fail'|'unknown'} AuditStatus */

  /**
   * @typedef {Object} AuditCheck
   * @property {string} id Stable identifier, for tests and the text form.
   * @property {string} label What is being checked, in the user's terms.
   * @property {string} value The short answer, rendered beside the label.
   * @property {AuditStatus} status
   * @property {string} [detail] One sentence saying what to do about a
   *   non-ok status, or naming the evidence behind it.
   * @property {string} [reason] An untranslated discriminator, where the
   *   verdict has to tell two failures of the same check apart.
   */

  /**
   * @typedef {Object} AuditSection
   * @property {string} id
   * @property {string} title
   * @property {AuditStatus} status The worst status among its checks.
   * @property {AuditCheck[]} checks
   */

  /**
   * @typedef {Object} AuditReport
   * @property {{status: AuditStatus, text: string}} verdict
   * @property {AuditSection[]} sections
   * @property {string} generatedAt ISO 8601.
   */

  /**
   * @typedef {Object} ShellEntry
   * @property {string} url
   * @property {boolean} [isPage] Whether the entry is page HTML rather
   *   than an asset — decided by the collector, which is the half that
   *   can afford to look.
   * @property {string|null} [principal] The ``X-SW-Principal`` stamp, on
   *   pages only. Null where there is none, which is never servable.
   */

  /**
   * @typedef {Object} AreaReading
   * @property {string} id
   * @property {'region'|'custom'|'base'} [kind] A base layer is the
   *   shared low-zoom tile set every area under one basemap reads
   *   (SNOW-856), and is judged differently — see ``areaState``.
   * @property {string} [name]
   * @property {string|null} [basemapKey]
   * @property {number} [bytes]
   * @property {string} [savedAt]
   * @property {string[]} [deps] The render dependencies the download run
   *   recorded. Empty means "nothing claimed", which is UNKNOWN.
   * @property {boolean} [bucketPresent]
   * @property {string[]} [entries] Every URL in the area's pinned bucket.
   */

  /**
   * Everything ``offline_audit.js`` managed to read. Every field is
   * optional: a device broken enough to fail these reads is exactly the
   * device whose user is reading this.
   *
   * @typedef {Object} AuditReadings
   * @property {string} [now]
   * @property {boolean} [online]
   * @property {string|null} [networkMode] The ``meta:app``
   *   ``network.mode`` row — 'auto', 'offline' (latched) or
   *   'offline-forced'.
   * @property {{supported?: boolean, registered?: boolean,
   *   controlled?: boolean, waiting?: boolean}} [serviceWorker]
   * @property {{usage?: number, quota?: number, persisted?: boolean}|null}
   *   [storage] ``navigator.storage.estimate()``, plus ``persisted``.
   * @property {string[]} [shellCacheNames]
   * @property {ShellEntry[]} [shellEntries]
   * @property {string|null} [currentPrincipal] The ``meta:app``
   *   ``mutations.principal`` row.
   * @property {string} [mapPath]
   * @property {AreaReading[]} [areas]
   * @property {string[]} [orphanBuckets]
   * @property {Record<string, number|null>} [stores]
   * @property {{count?: number|null}} [mutations]
   * @property {boolean} [dbAvailable]
   */

  // Worst-first, so `worst()` can pick by index rather than by a chain of
  // comparisons. 'unknown' sits BELOW 'ok' deliberately: a section of
  // unreadable checks must not present as a failure, and one unreadable
  // check among good ones must not drag the section down. It is the
  // absence of a signal, and the checks say so individually.
  var SEVERITY = ['fail', 'warn', 'ok', 'unknown'];

  /**
   * The worst status in a list, by ``SEVERITY`` order.
   *
   * @param {AuditStatus[]} statuses
   * @returns {AuditStatus} ``'unknown'`` for an empty list — nothing was
   *   measured, which is not a pass.
   */
  function worst(statuses) {
    var best = SEVERITY.length - 1;
    for (var i = 0; i < statuses.length; i += 1) {
      var rank = SEVERITY.indexOf(statuses[i]);
      if (rank >= 0 && rank < best) best = rank;
    }
    return /** @type {AuditStatus} */ (SEVERITY[best]);
  }

  /**
   * A human byte figure, in the units the rest of the app uses.
   *
   * Zero is treated as absent, not as a measurement. A download record
   * whose byte figure has not landed carries ``bytes: 0`` (SNOW-863) —
   * the tiles are on the device either way — and "0 MB" beside an area
   * that demonstrably holds four hundred tiles is a worse answer than
   * declining to give one.
   *
   * @param {*} bytes
   * @returns {string} ``'—'`` for zero, a negative, or anything that is
   *   not a number at all.
   */
  function formatBytes(bytes) {
    var n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    var mb = n / (1024 * 1024);
    return (mb < 10 ? mb.toFixed(1) : Math.round(mb)) + ' MB';
  }

  /**
   * Which of ``wanted`` is absent from ``present``.
   *
   * Deliberately the same contract as
   * ``pwaBasemapDownloadCore.missingRenderDependencies`` — subset in the
   * order given, deduplicated, and ``[]`` for an empty ``wanted``, which
   * means "nothing was claimed" and must be read as UNKNOWN rather than
   * as a pass (the three-row resolution rule in
   * docs/decisions/a-downloaded-area-is-verified-by-what-it-renders.md).
   *
   * Restated here rather than imported. That module is 116 KB of tile
   * arithmetic this report has no use for, and it would have to be
   * precached for ``offline.html`` to run the same check — which is the
   * one surface where this check matters most. Twelve lines against a
   * shared contract is the cheaper of the two debts; the tests assert the
   * shared cases on both sides.
   *
   * @param {string[]} wanted
   * @param {Set<string>|string[]} present
   * @returns {string[]}
   */
  function missingFrom(wanted, present) {
    if (!Array.isArray(wanted) || wanted.length === 0) return [];
    var have = present instanceof Set ? present : new Set(present || []);
    var missing = [];
    var seen = new Set();
    for (var i = 0; i < wanted.length; i += 1) {
      var url = wanted[i];
      if (typeof url !== 'string' || !url) continue;
      if (seen.has(url)) continue;
      seen.add(url);
      if (!have.has(url)) missing.push(url);
    }
    return missing;
  }

  /**
   * Which kind of shell-cache entry a URL is, for the inventory.
   *
   * Classified by path rather than by the response's content type: the
   * reading is taken from ``cache.keys()``, which returns Requests, and
   * asking each one for its Response to read a header would turn a
   * cheap listing into one round trip per entry on a device that may hold
   * hundreds.
   *
   * @param {string} url
   * @returns {'page'|'script'|'style'|'font'|'image'|'feed'|'other'}
   */
  function classifyEntry(url) {
    var path;
    try {
      path = new URL(url, 'https://snowdesk.info').pathname;
    } catch (_err) {
      path = String(url || '');
    }
    if (path.startsWith('/api/')) return 'feed';
    if (/\.m?js$/.test(path)) return 'script';
    if (/\.css$/.test(path)) return 'style';
    if (/\.(woff2?|ttf|otf)$/.test(path)) return 'font';
    if (/\.(png|jpe?g|svg|webp|ico|avif)$/.test(path)) return 'image';
    if (/\.[a-z0-9]{2,5}$/i.test(path)) return 'other';
    return 'page';
  }

  /**
   * Look up one string, falling back to its key so a missing entry is
   * visible rather than ``undefined``.
   *
   * @param {Record<string, string>} strings
   * @param {string} key
   * @returns {string}
   */
  function s(strings, key) {
    var value = strings && strings[key];
    return typeof value === 'string' && value ? value : key;
  }

  /**
   * Substitute ``%(name)s`` placeholders, by name.
   *
   * By name and never positionally, for the reason ``i18n_strings.js``
   * gives at length: a locale is free to reorder Django's placeholders.
   *
   * @param {string} template
   * @param {Record<string, string|number>} values
   * @returns {string}
   */
  function fill(template, values) {
    return String(template).replace(/%\(([a-z_]+)\)s/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(values, name)
        ? String(values[name])
        : whole;
    });
  }

  /**
   * The shell-cache entry for the map page, if one is saved.
   *
   * Matches on pathname alone, ignoring the query string, because the map
   * writes ``?d=YYYY-MM-DD`` into the URL with ``history.replaceState``
   * while the user scrubs — those URLs are never fetched and never
   * cached, and ``sw.js``'s ``_networkFirstFallback`` does the same
   * ``ignoreSearch`` lookup before giving up. Matching exactly here would
   * report "not saved" for a device that will in fact open the page.
   *
   * @param {Array<{url: string, principal?: string|null}>} entries
   * @param {string} mapPath
   * @returns {{url: string, principal?: string|null}|null}
   */
  function findPage(entries, mapPath) {
    var list = Array.isArray(entries) ? entries : [];
    for (var i = 0; i < list.length; i += 1) {
      var entry = list[i];
      if (!entry || typeof entry.url !== 'string') continue;
      var path;
      try {
        path = new URL(entry.url, 'https://snowdesk.info').pathname;
      } catch (_err) {
        continue;
      }
      if (path === mapPath) return entry;
    }
    return null;
  }

  /**
   * The state of one downloaded area, from its record and its bucket.
   *
   * Three questions, in the order a failure is worth knowing about:
   * is the bucket there at all, does it hold anything, and does it hold
   * the four documents MapLibre needs before a single tile is reachable.
   * The third is SNOW-843's whole bug class — a perfect tile set with no
   * TileJSON renders a blank map, and every surface called it "done".
   *
   * A SHARED BASE LAYER is the exception: it is tiles and nothing else,
   * because the area downloads that read it carry the style, TileJSON and
   * sprite between them (SNOW-856). Its empty dependency list is therefore
   * not "nothing was recorded" but "there is nothing to record", and
   * reporting it as unverifiable would send the user off to re-download
   * something that is complete.
   *
   * @param {AreaReading} area
   * @returns {{status: AuditStatus, missingDeps: string[],
   *   tiles: number, supporting: number}}
   */
  function areaState(area) {
    var entries = Array.isArray(area.entries) ? area.entries : [];
    var deps = Array.isArray(area.deps) ? area.deps : [];
    var missingDeps = missingFrom(deps, entries);
    // A dependency URL is in the bucket alongside the tiles, so the tile
    // figure is every entry that is not one of them. Counted from the
    // declared list rather than by URL shape, because a tile template is
    // per-basemap and this module is deliberately not in the business of
    // knowing them.
    var declared = new Set(deps);
    var supporting = 0;
    for (var i = 0; i < entries.length; i += 1) {
      if (declared.has(entries[i])) supporting += 1;
    }
    var status = /** @type {AuditStatus} */ ('ok');
    if (area.bucketPresent === false) {
      status = 'fail';
    } else if (entries.length === 0) {
      status = 'fail';
    } else if (missingDeps.length > 0) {
      status = 'warn';
    } else if (area.kind === 'base') {
      status = 'ok';
    } else if (deps.length === 0) {
      // Downloaded before SNOW-844, so nothing on the record says what
      // this run fetched. The tiles are demonstrably there; whether the
      // style, TileJSON and sprite are cannot be answered from here.
      status = 'unknown';
    } else {
      status = 'ok';
    }
    return {
      status: status,
      missingDeps: missingDeps,
      tiles: entries.length - supporting,
      supporting: supporting,
    };
  }

  /**
   * "This device" — is there a worker, is it in charge, and is there room.
   *
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {AuditSection}
   */
  function deviceSection(r, t) {
    var sw = r.serviceWorker || {};
    var checks = /** @type {AuditCheck[]} */ ([]);

    var swStatus = /** @type {AuditStatus} */ ('fail');
    var swValue = s(t, 'sw-absent');
    if (!sw.supported) {
      swValue = s(t, 'sw-unsupported');
    } else if (sw.controlled) {
      swStatus = 'ok';
      swValue = s(t, 'sw-controlling');
    } else if (sw.registered) {
      // Registered but not in control: the very first load of a page
      // before the worker claims it, or a worker that was unregistered
      // and re-registered in this tab. Nothing offline works until the
      // next load, which is the one thing to say.
      swStatus = 'warn';
      swValue = s(t, 'sw-registered-not-controlling');
    }
    checks.push({
      id: 'service-worker',
      label: s(t, 'label-service-worker'),
      value: swValue,
      status: /** @type {AuditStatus} */ (swStatus),
      detail: swStatus === 'ok' ? '' : s(t, 'detail-service-worker'),
    });

    if (sw.waiting) {
      checks.push({
        id: 'update-waiting',
        label: s(t, 'label-update'),
        value: s(t, 'update-waiting'),
        status: 'warn',
        detail: s(t, 'detail-update'),
      });
    }

    // The forced mode is a choice the user made and can unmake, so it is
    // a warning rather than a failure; the latched one is the worker
    // having given up on a dead connection after three timeouts, which is
    // worth surfacing for exactly the journey that produced this report.
    if (r.networkMode === 'offline-forced' || r.networkMode === 'offline') {
      checks.push({
        id: 'network-mode',
        label: s(t, 'label-network-mode'),
        value: s(t, r.networkMode === 'offline-forced' ? 'mode-forced' : 'mode-latched'),
        status: 'warn',
        detail: s(
          t,
          r.networkMode === 'offline-forced' ? 'detail-forced' : 'detail-latched',
        ),
      });
    }

    if (r.storage && Number.isFinite(Number(r.storage.usage))) {
      var usage = Number(r.storage.usage);
      var quota = Number(r.storage.quota);
      var hasQuota = Number.isFinite(quota) && quota > 0;
      // Near the quota, eviction is the next thing that happens, and it
      // takes the pinned buckets with it. 90% is where warning is still
      // actionable — the Manage downloads sheet can free an area.
      var tight = hasQuota && usage / quota > 0.9;
      checks.push({
        id: 'storage',
        label: s(t, 'label-storage'),
        value: hasQuota
          ? fill(s(t, 'storage-of'), {
              used: formatBytes(usage),
              total: formatBytes(quota),
            })
          : formatBytes(usage),
        status: tight ? 'warn' : 'ok',
        detail: tight ? s(t, 'detail-storage-tight') : '',
      });
    } else {
      checks.push({
        id: 'storage',
        label: s(t, 'label-storage'),
        value: s(t, 'unknown'),
        status: 'unknown',
        detail: s(t, 'detail-storage-unknown'),
      });
    }

    // Without persistent storage the browser may evict the whole origin
    // under pressure — every downloaded area included — and it does so
    // silently. Not a failure (the grant is the browser's to give, and
    // eviction is not certain), but it is the answer to "my download
    // vanished".
    if (r.storage && typeof r.storage.persisted === 'boolean') {
      checks.push({
        id: 'persisted',
        label: s(t, 'label-persisted'),
        value: s(t, r.storage.persisted ? 'persisted-yes' : 'persisted-no'),
        status: r.storage.persisted ? 'ok' : 'warn',
        detail: r.storage.persisted ? '' : s(t, 'detail-persisted-no'),
      });
    }

    return {
      id: 'device',
      title: s(t, 'section-device'),
      status: worst(checks.map(pluckStatus)),
      checks: checks,
    };
  }

  /**
   * "Pages saved for offline" — the check that explains the tube.
   *
   * A page opens offline only if its HTML is in the shell cache AND the
   * ``X-SW-Principal`` stamp written at cache time equals the principal
   * signed in now. The second half fails silently: the worker skips the
   * entry and serves ``offline.html``, which says the page has never been
   * opened — which, for the account now signed in, is true, and is not
   * what the user sees when they read it.
   *
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {AuditSection}
   */
  function pagesSection(r, t) {
    var entries = (Array.isArray(r.shellEntries) ? r.shellEntries : []).filter(
      function (entry) {
        return entry && entry.isPage;
      },
    );
    var current = r.currentPrincipal;
    var checks = /** @type {AuditCheck[]} */ ([]);

    var mapEntry = findPage(entries, r.mapPath || '/');
    var mapStatus = /** @type {AuditStatus} */ ('fail');
    var mapReason = 'absent';
    var mapValue = s(t, 'page-not-saved');
    var mapDetail = s(t, 'detail-page-not-saved');
    if (mapEntry) {
      if (principalMatches(mapEntry.principal, current)) {
        mapStatus = 'ok';
        mapReason = 'saved';
        mapValue = s(t, 'page-saved');
        mapDetail = '';
      } else {
        mapReason = 'principal';
        mapValue = s(t, 'page-other-account');
        mapDetail = fill(s(t, 'detail-page-other-account'), {
          stamped: describePrincipal(mapEntry.principal, t),
          current: describePrincipal(current, t),
        });
      }
    }
    checks.push({
      id: 'map-page',
      label: s(t, 'label-map-page'),
      value: mapValue,
      status: /** @type {AuditStatus} */ (mapStatus),
      detail: mapDetail,
      // Machine-readable, because the verdict needs to tell "never saved"
      // from "saved for someone else" and the rendered `value` is
      // translated copy — comparing against that would make the verdict
      // depend on the locale.
      reason: mapReason,
    });

    var usable = entries.filter(function (entry) {
      return principalMatches(entry.principal, current);
    });
    checks.push({
      id: 'pages-saved',
      label: s(t, 'label-pages-saved'),
      value: String(usable.length),
      status: usable.length > 0 ? 'ok' : 'fail',
      detail: fill(s(t, 'detail-pages-saved'), { total: entries.length }),
    });

    // Listed one per line so the report can be read as an inventory
    // rather than only as a verdict — "did the page I care about get
    // saved" is a question this answers and the two checks above do not.
    entries.forEach(function (entry, index) {
      var match = principalMatches(entry.principal, current);
      checks.push({
        id: 'page-' + index,
        label: shortPath(entry.url),
        value: s(t, match ? 'page-entry-usable' : 'page-entry-other'),
        status: /** @type {AuditStatus} */ (match ? 'ok' : 'warn'),
        detail: match ? '' : describePrincipal(entry.principal, t),
      });
    });

    return {
      id: 'pages',
      title: s(t, 'section-pages'),
      status: worst(checks.map(pluckStatus)),
      checks: checks,
    };
  }

  /**
   * "App files" — the shell-cache inventory.
   *
   * A page whose HTML is cached and whose scripts are not paints a blank
   * frame offline, which is indistinguishable to the user from the page
   * not being saved at all. Counts rather than a list: the shell holds
   * dozens of files under hashed names nobody can read, and the useful
   * question is whether each KIND is there.
   *
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {AuditSection}
   */
  function filesSection(r, t) {
    var entries = Array.isArray(r.shellEntries) ? r.shellEntries : [];
    var counts = { script: 0, style: 0, font: 0, image: 0, feed: 0, other: 0 };
    entries.forEach(function (entry) {
      if (!entry || entry.isPage) return;
      var kind = classifyEntry(entry.url);
      if (kind === 'page') return;
      counts[kind] = (counts[kind] || 0) + 1;
    });

    var checks = /** @type {AuditCheck[]} */ ([
      {
        id: 'scripts',
        label: s(t, 'label-scripts'),
        value: String(counts.script),
        status: /** @type {AuditStatus} */ (counts.script > 0 ? 'ok' : 'fail'),
        detail: counts.script > 0 ? '' : s(t, 'detail-no-scripts'),
      },
      {
        id: 'styles',
        label: s(t, 'label-styles'),
        value: String(counts.style),
        status: /** @type {AuditStatus} */ (counts.style > 0 ? 'ok' : 'warn'),
        detail: counts.style > 0 ? '' : s(t, 'detail-no-styles'),
      },
      {
        id: 'feeds',
        label: s(t, 'label-feeds'),
        value: String(counts.feed),
        status: /** @type {AuditStatus} */ (counts.feed > 0 ? 'ok' : 'warn'),
        detail: counts.feed > 0 ? '' : s(t, 'detail-no-feeds'),
      },
      {
        id: 'other-files',
        label: s(t, 'label-other-files'),
        value: String(counts.font + counts.image + counts.other),
        status: /** @type {AuditStatus} */ ('ok'),
        detail: '',
      },
    ]);

    return {
      id: 'files',
      title: s(t, 'section-files'),
      status: worst(checks.map(pluckStatus)),
      checks: checks,
    };
  }

  /**
   * "Downloaded maps" — one row per area, plus the two ways a device and
   * its own records disagree.
   *
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {AuditSection}
   */
  function mapsSection(r, t) {
    var areas = Array.isArray(r.areas) ? r.areas : [];
    var checks = /** @type {AuditCheck[]} */ ([]);

    if (areas.length === 0) {
      checks.push({
        id: 'no-areas',
        label: s(t, 'label-areas'),
        value: s(t, 'areas-none'),
        status: /** @type {AuditStatus} */ ('warn'),
        detail: s(t, 'detail-areas-none'),
      });
    }

    areas.forEach(function (area) {
      var state = areaState(area);
      var value;
      if (state.status === 'fail') {
        value = s(t, area.bucketPresent === false ? 'area-missing' : 'area-empty');
      } else if (state.status === 'warn') {
        value = fill(s(t, 'area-incomplete'), {
          count: state.missingDeps.length,
        });
      } else {
        value = fill(s(t, 'area-tiles'), {
          tiles: state.tiles,
          size: formatBytes(area.bytes),
        });
      }
      var detail = '';
      if (state.status === 'warn') {
        detail = s(t, 'detail-area-incomplete');
      } else if (state.status === 'fail') {
        detail = s(t, 'detail-area-missing');
      } else if (state.status === 'unknown') {
        detail = s(t, 'detail-area-unverifiable');
      }
      checks.push({
        id: 'area-' + area.id,
        // A base layer's record is keyed by basemap, so its name is a
        // style key ('openfreemap_liberty') — an internal identifier the
        // user never chose and has no control over. It is named for what
        // it does instead, the way the reset panel's one combined row is.
        label:
          area.kind === 'base'
            ? fill(s(t, 'label-base-layer'), { basemap: area.basemapKey || '' })
            : area.name || area.id,
        value: value,
        status: state.status,
        detail: detail,
      });
    });

    (Array.isArray(r.orphanBuckets) ? r.orphanBuckets : []).forEach(function (id) {
      checks.push({
        id: 'orphan-' + id,
        label: fill(s(t, 'label-orphan'), { id: id }),
        value: s(t, 'orphan-value'),
        status: /** @type {AuditStatus} */ ('warn'),
        detail: s(t, 'detail-orphan'),
      });
    });

    return {
      id: 'maps',
      title: s(t, 'section-maps'),
      status: worst(checks.map(pluckStatus)),
      checks: checks,
    };
  }

  /**
   * "Saved data and unsent changes" — the IndexedDB half.
   *
   * @param {AuditReadings} r
   * @param {Record<string, string>} t
   * @returns {AuditSection}
   */
  function dataSection(r, t) {
    var checks = /** @type {AuditCheck[]} */ ([]);
    if (!r.dbAvailable) {
      checks.push({
        id: 'db',
        label: s(t, 'label-db'),
        value: s(t, 'unknown'),
        status: /** @type {AuditStatus} */ ('unknown'),
        detail: s(t, 'detail-db-unavailable'),
      });
      return {
        id: 'data',
        title: s(t, 'section-data'),
        status: 'unknown',
        checks: checks,
      };
    }

    var stores = r.stores || {};
    [
      ['data:favourites', 'label-favourites'],
      ['data:map_overlays', 'label-overlays'],
      ['data:panel_rows', 'label-panel-rows'],
    ].forEach(function (pair) {
      var count = Number(stores[pair[0]]);
      var known = Number.isFinite(count);
      checks.push({
        id: pair[0],
        label: s(t, pair[1]),
        value: known ? String(count) : s(t, 'unknown'),
        // Zero rows is not a fault. A user with no favourites has nothing
        // to cache, and the map draws without any of these.
        status: /** @type {AuditStatus} */ (known ? 'ok' : 'unknown'),
        detail: '',
      });
    });

    var pending = Number(r.mutations && r.mutations.count);
    if (!Number.isFinite(pending)) pending = 0;
    checks.push({
      id: 'mutations',
      label: s(t, 'label-mutations'),
      value: String(pending),
      status: /** @type {AuditStatus} */ (pending > 0 ? 'warn' : 'ok'),
      detail: pending > 0 ? s(t, 'detail-mutations') : '',
    });

    return {
      id: 'data',
      title: s(t, 'section-data'),
      status: worst(checks.map(pluckStatus)),
      checks: checks,
    };
  }

  /**
   * The one-line answer, which is the worst true thing in the report.
   *
   * Ordered by what the user would do about it, not by severity of the
   * underlying fault: no worker at all comes first because nothing below
   * it can be true, and a missing map page comes before a missing
   * download because the download cannot be reached without the page.
   *
   * @param {AuditSection[]} sections
   * @param {Record<string, string>} t
   * @returns {{status: AuditStatus, text: string}}
   */
  function verdictFor(sections, t) {
    var byId = /** @type {Record<string, AuditCheck>} */ ({});
    sections.forEach(function (section) {
      section.checks.forEach(function (check) {
        byId[check.id] = check;
      });
    });

    if (byId['service-worker'] && byId['service-worker'].status === 'fail') {
      return { status: 'fail', text: s(t, 'verdict-no-worker') };
    }
    var page = byId['map-page'];
    if (page && page.status === 'fail') {
      return {
        status: 'fail',
        text: s(
          t,
          page.reason === 'principal' ? 'verdict-other-account' : 'verdict-no-page',
        ),
      };
    }
    if (byId.scripts && byId.scripts.status === 'fail') {
      return { status: 'fail', text: s(t, 'verdict-no-scripts') };
    }

    var maps = sections.filter(function (section) {
      return section.id === 'maps';
    })[0];
    if (maps && maps.status === 'warn') {
      return { status: 'warn', text: s(t, 'verdict-map-incomplete') };
    }
    if (maps && maps.status === 'fail') {
      return { status: 'warn', text: s(t, 'verdict-map-missing') };
    }
    return { status: 'ok', text: s(t, 'verdict-ok') };
  }

  /**
   * Build the whole report.
   *
   * Every reading is optional. A device broken enough to fail these reads
   * is exactly the device whose user is reading this, so a missing input
   * degrades to a stated ``unknown`` rather than a thrown error.
   *
   * @param {AuditReadings} [readings] Collected by ``offline_audit.js``:
   *   ``serviceWorker`` ``{supported, registered, controlled, waiting}``;
   *   ``storage`` ``navigator.storage.estimate()``'s result plus
   *   ``persisted``; ``networkMode`` the ``meta:app`` ``network.mode``
   *   row; ``shellEntries`` one ``{url, isPage, principal}`` per shell
   *   cache entry; ``currentPrincipal`` the ``meta:app``
   *   ``mutations.principal`` row; ``mapPath`` the map page's path;
   *   ``areas`` one ``{id, name, deps, entries, bucketPresent, bytes}``
   *   per downloaded area; ``orphanBuckets`` pinned bucket ids no record
   *   names; ``stores`` row counts by store name; ``mutations``
   *   ``{count}``; ``dbAvailable`` whether IndexedDB opened at all.
   * @param {Record<string, string>} [strings] Translated copy, keyed as
   *   in ``_offline_audit_panel.html``. Falls back to the key itself.
   * @returns {AuditReport}
   */
  function buildReport(readings, strings) {
    var r = readings || {};
    var t = strings || {};
    var sections = [
      deviceSection(r, t),
      pagesSection(r, t),
      filesSection(r, t),
      mapsSection(r, t),
      dataSection(r, t),
    ];
    return {
      verdict: verdictFor(sections, t),
      sections: sections,
      generatedAt: typeof r.now === 'string' ? r.now : new Date().toISOString(),
    };
  }

  /**
   * The report as plain text, for the Copy control.
   *
   * A phone with no devtools is the only place this data exists, so
   * getting it off the device is not a convenience. Statuses are spelled
   * out rather than coloured, and the section structure survives, so a
   * pasted report reads the same as the panel did.
   *
   * @param {AuditReport} report
   * @param {{userAgent?: string, appVersion?: string, url?: string}} [context]
   * @returns {string}
   */
  function reportText(report, context) {
    var ctx = context || {};
    var lines = ['Snowdesk offline content report', report.generatedAt];
    if (ctx.url) lines.push(ctx.url);
    if (ctx.appVersion) lines.push('app version: ' + ctx.appVersion);
    if (ctx.userAgent) lines.push(ctx.userAgent);
    lines.push('');
    lines.push(report.verdict.status.toUpperCase() + ': ' + report.verdict.text);
    report.sections.forEach(function (section) {
      lines.push('');
      lines.push('## ' + section.title + ' [' + section.status + ']');
      section.checks.forEach(function (check) {
        lines.push('  [' + check.status + '] ' + check.label + ': ' + check.value);
        if (check.detail) lines.push('      ' + check.detail);
      });
    });
    return lines.join('\n');
  }

  /**
   * Whether a cached page's stamp lets it be served to the principal
   * signed in now.
   *
   * Mirrors ``sw.js``'s ``_principalMatches``, including its fail-closed
   * treatment of an absent or ``'unknown'`` stamp: an entry the worker
   * will never serve must never be reported as saved.
   *
   * @param {string|null|undefined} stamped
   * @param {string|null|undefined} current
   * @returns {boolean}
   */
  function principalMatches(stamped, current) {
    if (!stamped || stamped === 'unknown') return false;
    return stamped === (current || 'anonymous');
  }

  /**
   * A stamp in words. The account uuid is never shown whole — it names
   * an account to anyone reading over a shoulder and means nothing to the
   * person holding the phone; the first segment is enough to tell two
   * apart in a report.
   *
   * @param {string|null|undefined} principal
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function describePrincipal(principal, t) {
    if (!principal || principal === 'unknown') return s(t, 'principal-unknown');
    if (principal === 'anonymous') return s(t, 'principal-anonymous');
    return fill(s(t, 'principal-account'), {
      id: String(principal).slice(0, 8),
    });
  }

  /**
   * The readable part of a cached URL — path only, query dropped.
   *
   * @param {string} url
   * @returns {string}
   */
  function shortPath(url) {
    try {
      return new URL(url, 'https://snowdesk.info').pathname;
    } catch (_err) {
      return String(url || '');
    }
  }

  /**
   * A check's status, as a named function so the ``.map`` calls above
   * read as what they are.
   *
   * @param {AuditCheck} check
   * @returns {AuditStatus}
   */
  function pluckStatus(check) {
    return check.status;
  }

  self.pwaOfflineAuditCore = Object.freeze({
    buildReport: buildReport,
    reportText: reportText,
    missingFrom: missingFrom,
    classifyEntry: classifyEntry,
    formatBytes: formatBytes,
    principalMatches: principalMatches,
    areaState: areaState,
    worst: worst,
  });
})();
