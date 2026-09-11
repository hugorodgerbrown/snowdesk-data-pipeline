/*
 * static/js/offline_audit.js — read what this device actually holds, and
 * paint the offline-content report (SNOW-907).
 *
 * The collector and the DOM half. ``offline_audit_core.js`` turns the
 * readings into the report; everything that touches Cache Storage,
 * IndexedDB, the service worker or the page is here.
 *
 * ## Two hosts, one module
 *
 * This runs on ``/account/settings/`` (a row in the "This device" card)
 * and on ``static/offline.html`` (the branded fallback page). The second
 * is the point: settings is a normal Django page, and a device that
 * cannot reach the network cannot be relied on to have it cached — the
 * moment a user most needs to know what is stored is the moment the only
 * page they can open is the one telling them nothing is. So this file and
 * its core are warmed into the shell cache on install (``AUDIT_SCRIPTS``,
 * static/js/sw.js), and the offline page loads them by their unhashed
 * ``/static/`` path.
 *
 * Both hosts provide the same markup contract and style it themselves —
 * this module writes classless semantic HTML carrying
 * ``data-audit-status`` and nothing else. A Tailwind class string would
 * be inert on the offline page, whose stylesheet is inline and whose
 * whole design rule is "no external assets".
 *
 * ## The log is one line per check
 *
 * Strictly one, and nothing under it. A row is elapsed, label, answer,
 * and everything a failing row would want to explain is composed into
 * the summary instead (``composeSummary``). The rule is not stylistic:
 * per-row helper text made this panel three times taller, turned
 * scanning into reading, and repeated one remedy across three rows
 * rather than saying it once. The renderer enforces it by having nowhere
 * to put a second line, and ``tests/js/test_offline_audit.js`` asserts a
 * row never grows one.
 *
 * ## Timings are measured, not staged
 *
 * The elapsed column is real: the collector marks the clock as each
 * reading completes, and the core hands each row the mark behind it. Rows
 * produced by ONE reading therefore share a figure — every page row comes
 * out of a single cache walk — and on a fast device several will read
 * ``0.00s``. A staggered reveal would look better and would be measuring
 * the animation instead of the work, which is the one thing a diagnostic
 * must not do.
 *
 * ## It reads storage, it does not ask another module
 *
 * `pwaBasemapAreas`, `pwaBasemapDownloadCore` and `pwaDb` all expose
 * readers for most of this, and none of them is used here. A report whose
 * job is to catch storage and the app disagreeing must not take the app's
 * word for what storage holds — SNOW-843 was three separate surfaces
 * agreeing an area was downloaded while the map drew nothing. Reading the
 * buckets and the object stores directly is the only way the report can
 * contradict them.
 *
 * It also has to run where those modules are absent. The offline page
 * loads two files; `basemap_download_core.js` alone is 116 KB of tile
 * arithmetic this has no use for.
 *
 * ## Never writes
 *
 * Every read is opened read-only and the one action — warming the map
 * page into the shell cache — goes through the worker's existing
 * ``warm-cache`` message rather than writing a cache entry from here.
 * A diagnostic that changes what it is diagnosing is worse than none.
 */

(function () {
  'use strict';

  var ROOT_SELECTOR = '[data-offline-audit]';
  var SHELL_CACHE_PREFIX = 'snowdesk-shell-';
  var PINNED_CACHE_PREFIX = 'snowdesk-basemap-pinned-';
  var DB_NAME = 'snowdesk-pwa-v1';

  // The stores whose depth the report states. Not every store in db.js:
  // `queue:events` is the telemetry buffer and `log:*` are diagnostics,
  // neither of which is content the user would miss offline.
  var DATA_STORES = ['data:favourites', 'data:map_overlays', 'data:panel_rows'];

  // How long to wait for the worker to name its own cache version before
  // falling back to reading every shell cache present. Short because the
  // reply is a postMessage round trip to a worker that is, by definition,
  // already running — and because the fallback is correct, just less
  // precise.
  var VERSION_PROBE_MS = 1500;

  // SNOW-620: server-translated copy where a template provides it, the
  // English literal everywhere else. `pwaStrings` is not loaded on the
  // offline page, so the fallbacks are not a safety net there — they are
  // the copy, exactly as every other string on that page is.
  //
  // Four families:
  //   section-* / label-*   the log's section rules and its left column
  //   the answers           what a row's right column says
  //   note-*                one standalone clause for the summary
  //   group-* / subject-*
  //     / effect-*          the parts the summary composes into a single
  //                         sentence when several faults share a remedy
  var FALLBACKS = {
    'section-device': 'This device',
    'section-pages': 'Pages saved for offline',
    'section-files': 'App files',
    'section-maps': 'Downloaded maps',
    'section-data': 'Saved data',

    'label-service-worker': 'Offline mode',
    'sw-controlling': 'active',
    'sw-registered-not-controlling': 'starting up',
    'sw-absent': 'not set up',
    'sw-unsupported': 'not supported',
    'note-service-worker': 'offline mode is not running on this device yet',
    'label-update': 'Update',
    'update-waiting': 'waiting to install',
    'note-update': 'a newer version is waiting to install',
    'label-network-mode': 'Connection',
    'mode-forced': 'offline mode is on',
    'mode-latched': 'no usable connection',
    'note-forced': 'offline mode is switched on, so nothing new will be fetched',
    'note-latched':
      'the app has stopped trying to reach the network after repeated timeouts',
    'label-storage': 'Space used',
    'storage-of': '%(used)s of %(total)s',
    'note-storage-tight':
      'storage is nearly full, so the browser may start deleting downloads',
    'label-persisted': 'Protected from cleanup',
    'persisted-yes': 'yes',
    'persisted-no': 'no',
    'note-persisted': 'downloads are not protected from browser cleanup',

    'label-map-page': 'The map page',
    'page-saved': 'saved',
    'page-not-saved': 'not saved',
    'page-other-account': 'another account',
    'label-pages-saved': 'Pages you can open offline',
    'page-entry-usable': 'ready',

    'label-scripts': 'Program files',
    'label-styles': 'Styling',
    'label-feeds': 'Data feeds',
    'label-other-files': 'Fonts and images',
    'subject-scripts': 'program files',
    'subject-styles': 'styling',
    'subject-feeds': 'data feeds',
    'effect-scripts': 'will open blank',
    'effect-styles': 'will look plain',
    'effect-feeds': 'may be missing danger ratings',
    'group-open-map-lead': 'Saved pages %(effects)s — %(subjects)s are not saved yet.',
    'group-open-map-remedy': 'Opening the map once while connected fixes %(count)s.',

    'label-areas': 'Downloaded areas',
    'areas-none': 'none',
    'note-areas-none':
      'no map area is downloaded, so there will be nothing to show in the map',
    'area-tiles': '%(tiles)s tiles, %(size)s',
    'area-incomplete': 'incomplete, %(count)s missing',
    'area-missing': 'not on this device',
    'area-empty': 'empty',
    'note-area-incomplete':
      '%(name)s will not draw until you repair it from the map’s Manage downloads sheet',
    'note-area-missing':
      '%(name)s is recorded as downloaded but nothing is stored, so download it again',
    'label-base-layer': 'Zoomed-out overview (%(basemap)s)',
    'label-orphan': 'Unnamed area %(id)s',
    'orphan-value': 'no record',
    'note-orphan': 'some tiles are taking up space with nothing pointing at them',

    'label-favourites': 'Saved places',
    'label-overlays': 'Map overlays',
    'label-panel-rows': 'Reports',
    'label-mutations': 'Changes waiting to send',
    'note-mutations': '%(n)s of your changes have not reached the server yet',
    'label-db': 'Local database',

    'principal-anonymous': 'signed-out visitor',
    'principal-account': 'account %(id)s…',
    'principal-unknown': 'unknown account',
    unknown: 'unknown',

    'verdict-ok': 'The map will open offline.',
    'verdict-no-worker': 'This device is not set up for offline use yet.',
    'verdict-no-page': 'The map page is not saved. Open it once while connected.',
    'verdict-other-account':
      'The saved map page belongs to another account. Open the map once while connected.',
    'verdict-no-scripts':
      'The app’s program files are not saved, so pages will open blank.',
    'verdict-map-incomplete':
      'The map page will open, but a downloaded area is incomplete.',
    'verdict-map-missing':
      'The map page will open, but no area is downloaded to show in it.',

    'notes-sentence': 'Also worth knowing: %(notes)s.',
    'list-pair': '%(first)s and %(last)s',
    'list-separator': ', ',
    'count-one': 'it',
    'count-two': 'both',
    'count-many': 'all %(n)s of them',

    'counts-line': '%(total)s checks · %(attention)s need attention',
    'counts-line-clear': '%(total)s checks · nothing needs attention',

    running: 'Checking…',
    copied: 'copied',
    'copy-failed': 'copy failed',
    saving: 'Saving the map page…',
    saved: 'Saved. Re-checking…',
    'save-failed': 'That could not be saved. Try again while connected.',
  };

  /**
   * The translated copy for this surface, or the English fallbacks.
   *
   * @returns {Record<string, string>}
   */
  function strings() {
    if (self.pwaStrings && typeof self.pwaStrings.read === 'function') {
      return self.pwaStrings.read('offline-audit-strings-template', FALLBACKS);
    }
    return FALLBACKS;
  }

  /**
   * A clock that measures this run, starting now.
   *
   * ``performance.now()`` where it exists, ``Date.now()`` otherwise — the
   * figures render to 10ms, so the difference in resolution never shows,
   * and the fallback keeps the column honest rather than blank on a
   * browser without the API.
   *
   * @returns {{mark: (name: string) => void, marks: Record<string, number>,
   *   elapsed: () => number}}
   */
  function clock() {
    var now = function () {
      return typeof performance !== 'undefined' && performance.now
        ? performance.now()
        : Date.now();
    };
    var start = now();
    var marks = /** @type {Record<string, number>} */ ({});
    return {
      marks: marks,
      mark: function (name) {
        marks[name] = Math.round(now() - start);
      },
      elapsed: function () {
        return Math.round(now() - start);
      },
    };
  }

  /**
   * Every Cache Storage bucket name, or ``[]`` where the API is absent.
   *
   * @returns {Promise<string[]>}
   */
  async function cacheNames() {
    try {
      if (!self.caches || typeof self.caches.keys !== 'function') return [];
      return await self.caches.keys();
    } catch (_err) {
      return [];
    }
  }

  /**
   * Ask the controlling worker which shell cache it is using.
   *
   * ``sw.js`` answers the bare ``'version'`` message with its
   * ``CACHE_VERSION``, which IS the cache name. Worth the round trip
   * because ``activate`` deletes stale shell caches but a worker that has
   * installed and not yet activated leaves two on disk — and reading the
   * wrong one would report a page as saved that the live worker will
   * never serve.
   *
   * @returns {Promise<string|null>} Null on timeout, no controller, or no
   *   service-worker support at all.
   */
  function liveShellCacheName() {
    return new Promise(function (resolve) {
      var controller =
        'serviceWorker' in navigator ? navigator.serviceWorker.controller : null;
      if (!controller) {
        resolve(null);
        return;
      }
      var settled = false;
      var finish = function (value) {
        if (settled) return;
        settled = true;
        navigator.serviceWorker.removeEventListener('message', onMessage);
        resolve(value);
      };
      var onMessage = function (event) {
        if (event.data && event.data.type === 'version') {
          finish(event.data.version || null);
        }
      };
      navigator.serviceWorker.addEventListener('message', onMessage);
      setTimeout(function () {
        finish(null);
      }, VERSION_PROBE_MS);
      try {
        controller.postMessage('version');
      } catch (_err) {
        finish(null);
      }
    });
  }

  /**
   * Whether a cached URL is a page rather than a file, by path shape.
   *
   * The cheap half of the classification: anything the core calls a
   * ``page`` is worth opening to read its principal stamp, and nothing
   * else is. On a device holding hundreds of hashed asset entries, that
   * is the difference between a handful of ``cache.match`` calls and one
   * per entry.
   *
   * @param {string} url
   * @returns {boolean}
   */
  function looksLikePage(url) {
    var core = self.pwaOfflineAuditCore;
    return !!core && core.classifyEntry(url) === 'page';
  }

  /**
   * Every entry in the shell cache, with a principal stamp on the pages.
   *
   * @param {string[]} names Which shell caches to read. More than one
   *   means the worker did not answer and every candidate is being read,
   *   which overstates rather than understates.
   * @returns {Promise<Array<{url: string, isPage: boolean,
   *   principal: string|null}>>}
   */
  async function readShellEntries(names) {
    var entries = [];
    for (var i = 0; i < names.length; i += 1) {
      var cache;
      try {
        cache = await self.caches.open(names[i]);
      } catch (_err) {
        continue;
      }
      var requests = [];
      try {
        requests = await cache.keys();
      } catch (_err) {
        continue;
      }
      for (var j = 0; j < requests.length; j += 1) {
        var url = requests[j].url;
        var isPage = looksLikePage(url);
        var principal = null;
        if (isPage) {
          try {
            var response = await cache.match(requests[j]);
            // The same header ``sw.js``'s ``_principalMatches`` reads. An
            // entry with no stamp is one the worker will refuse to serve,
            // and the core treats a null stamp exactly that way.
            principal = response ? response.headers.get('X-SW-Principal') : null;
          } catch (_err) {
            principal = null;
          }
        }
        entries.push({ url: url, isPage: isPage, principal: principal });
      }
    }
    return entries;
  }

  /**
   * Open the app database read-only, without ever creating or upgrading
   * it.
   *
   * Opening with no version means the request never triggers an upgrade,
   * so this cannot race ``db.js``'s own migrations or leave a
   * half-created schema behind. Where ``indexedDB.databases()`` exists the
   * open is skipped entirely for a device that has no database yet —
   * otherwise a bare open would CREATE an empty one, which is a write, and
   * this file does not write.
   *
   * @returns {Promise<IDBDatabase|null>}
   */
  async function openDb() {
    if (!self.indexedDB) return null;
    try {
      if (typeof self.indexedDB.databases === 'function') {
        var present = await self.indexedDB.databases();
        var found = (present || []).some(function (entry) {
          return entry && entry.name === DB_NAME;
        });
        if (!found) return null;
      }
    } catch (_err) {
      // `databases()` is rejected or absent in some privacy modes. Fall
      // through to the open, which is the pre-existing behaviour
      // everywhere that method was never available.
    }
    return await new Promise(function (resolve) {
      var request;
      try {
        request = self.indexedDB.open(DB_NAME);
      } catch (_err) {
        resolve(null);
        return;
      }
      request.onsuccess = function () {
        resolve(request.result);
      };
      request.onerror = function () {
        resolve(null);
      };
      request.onblocked = function () {
        resolve(null);
      };
    });
  }

  /**
   * One record from ``meta:app``, by key.
   *
   * @param {IDBDatabase} db
   * @param {string} key
   * @returns {Promise<*>} The row's ``value``, or null for a missing row,
   *   a missing store, or any failure.
   */
  function readMeta(db, key) {
    return new Promise(function (resolve) {
      try {
        if (!db.objectStoreNames.contains('meta:app')) {
          resolve(null);
          return;
        }
        var request = db
          .transaction('meta:app', 'readonly')
          .objectStore('meta:app')
          .get(key);
        request.onsuccess = function () {
          resolve(request.result ? request.result.value : null);
        };
        request.onerror = function () {
          resolve(null);
        };
      } catch (_err) {
        resolve(null);
      }
    });
  }

  /**
   * How many rows a store holds.
   *
   * @param {IDBDatabase} db
   * @param {string} name
   * @returns {Promise<number|null>} Null where the store does not exist or
   *   cannot be read — which the report prints as unknown, never as zero.
   */
  function countStore(db, name) {
    return new Promise(function (resolve) {
      try {
        if (!db.objectStoreNames.contains(name)) {
          resolve(null);
          return;
        }
        var request = db.transaction(name, 'readonly').objectStore(name).count();
        request.onsuccess = function () {
          resolve(Number(request.result));
        };
        request.onerror = function () {
          resolve(null);
        };
      } catch (_err) {
        resolve(null);
      }
    });
  }

  /**
   * Every URL in one pinned bucket, and whether the bucket exists at all.
   *
   * ``caches.has`` first, deliberately: ``caches.open`` CREATES a bucket
   * that was not there, so probing with it would silently manufacture the
   * very thing being checked for and turn "this area is gone" into "this
   * area is empty" for every subsequent run.
   *
   * @param {string} areaId
   * @returns {Promise<{present: boolean, entries: string[]}>}
   */
  async function readBucket(areaId) {
    var name = PINNED_CACHE_PREFIX + areaId;
    try {
      if (!(await self.caches.has(name))) return { present: false, entries: [] };
      var cache = await self.caches.open(name);
      var requests = await cache.keys();
      return {
        present: true,
        entries: requests.map(function (request) {
          return request.url;
        }),
      };
    } catch (_err) {
      return { present: false, entries: [] };
    }
  }

  /**
   * The downloaded areas this device has records for, normalised.
   *
   * Three record shapes become one: a region (keyed by ``region_id``, its
   * bucket named ``region-<id>``), a custom area (its own ``id``) and a
   * shared base layer (``base-<basemapKey>``, the zoomed-out tiles every
   * area reads). The base layers are included because they are stored,
   * they take space, and they are invisible everywhere else — a user
   * comparing this report against the Manage downloads sheet should not
   * find tiles the sheet never mentions and conclude the report is wrong.
   *
   * @param {IDBDatabase|null} db
   * @returns {Promise<Array<Object>>}
   */
  async function readAreaRecords(db) {
    if (!db) return [];
    var areas = [];
    var regions = (await readMeta(db, 'basemap.regions')) || [];
    if (Array.isArray(regions)) {
      regions.forEach(function (record) {
        if (!record || !record.region_id) return;
        areas.push({
          id: 'region-' + record.region_id,
          kind: 'region',
          name: record.name || record.region_id,
          basemapKey: record.basemapKey || null,
          bytes: record.bytes,
          savedAt: record.savedAt,
          deps: Array.isArray(record.deps) ? record.deps : [],
        });
      });
    }
    var custom = (await readMeta(db, 'basemap.customAreas')) || [];
    if (Array.isArray(custom)) {
      custom.forEach(function (record) {
        if (!record || !record.id) return;
        areas.push({
          id: record.id,
          kind: 'custom',
          name: record.name || record.id,
          basemapKey: record.basemapKey || null,
          bytes: record.bytes,
          savedAt: record.savedAt,
          deps: Array.isArray(record.deps) ? record.deps : [],
        });
      });
    }
    var baseLayers = (await readMeta(db, 'basemap.baseLayers')) || [];
    if (Array.isArray(baseLayers)) {
      baseLayers.forEach(function (record) {
        if (!record || !record.basemapKey) return;
        areas.push({
          id: 'base-' + record.basemapKey,
          kind: 'base',
          name: record.name || record.basemapKey,
          basemapKey: record.basemapKey,
          bytes: record.bytes,
          savedAt: record.savedAt,
          // A base layer is tiles only — it has no style, TileJSON or
          // sprite of its own, because the area downloads that share it
          // carry those. `areaState` knows not to read this as unknown.
          deps: [],
        });
      });
    }
    return areas;
  }

  /**
   * Take every reading the report is built from, marking the clock as
   * each one lands.
   *
   * @returns {Promise<Object>} The readings object ``buildReport``
   *   documents.
   */
  async function collect() {
    var run = clock();
    var swSupported = 'serviceWorker' in navigator;
    var registration = null;
    if (swSupported) {
      try {
        registration = await navigator.serviceWorker.getRegistration();
      } catch (_err) {
        registration = null;
      }
    }
    run.mark('worker');

    var live = await liveShellCacheName();
    var names = await cacheNames();
    var shellNames = live
      ? [live]
      : names.filter(function (name) {
          return name.indexOf(SHELL_CACHE_PREFIX) === 0;
        });
    var shellEntries = shellNames.length ? await readShellEntries(shellNames) : [];
    run.mark('shell');

    var db = await openDb();
    run.mark('db');
    var areaRecords = await readAreaRecords(db);
    run.mark('areas');
    var areas = [];
    for (var i = 0; i < areaRecords.length; i += 1) {
      var bucket = await readBucket(areaRecords[i].id);
      // One mark per area, because one area IS one bucket read — this is
      // the part of the column that genuinely varies, and on a device
      // holding several downloads it is where the time goes.
      run.mark('area:' + areaRecords[i].id);
      areas.push(
        Object.assign({}, areaRecords[i], {
          bucketPresent: bucket.present,
          entries: bucket.entries,
        }),
      );
    }

    var recordedIds = new Set(
      areas.map(function (area) {
        return area.id;
      }),
    );
    var orphanBuckets = names
      .filter(function (name) {
        return name.indexOf(PINNED_CACHE_PREFIX) === 0;
      })
      .map(function (name) {
        return name.slice(PINNED_CACHE_PREFIX.length);
      })
      .filter(function (id) {
        return id && !recordedIds.has(id);
      });
    run.mark('orphans');

    var stores = {};
    if (db) {
      for (var j = 0; j < DATA_STORES.length; j += 1) {
        stores[DATA_STORES[j]] = await countStore(db, DATA_STORES[j]);
        run.mark('store:' + DATA_STORES[j]);
      }
    }
    var mutations = db ? await countStore(db, 'queue:mutations') : null;
    run.mark('mutations');
    var currentPrincipal = db ? await readMeta(db, 'mutations.principal') : null;
    var networkMode = db ? await readMeta(db, 'network.mode') : null;
    run.mark('network');
    if (db) {
      try {
        db.close();
      } catch (_err) {
        // Non-fatal.
      }
    }

    var storage = null;
    try {
      if (navigator.storage && typeof navigator.storage.estimate === 'function') {
        storage = await navigator.storage.estimate();
        run.mark('storage');
        if (typeof navigator.storage.persisted === 'function') {
          storage = Object.assign({}, storage, {
            persisted: await navigator.storage.persisted(),
          });
        }
      }
    } catch (_err) {
      storage = null;
    }
    run.mark('persisted');

    return {
      now: new Date().toISOString(),
      online: navigator.onLine !== false,
      networkMode: typeof networkMode === 'string' ? networkMode : null,
      serviceWorker: {
        supported: swSupported,
        registered: !!registration,
        controlled: swSupported && !!navigator.serviceWorker.controller,
        waiting: !!(registration && registration.waiting),
      },
      storage: storage,
      shellCacheNames: shellNames,
      shellEntries: shellEntries,
      currentPrincipal: typeof currentPrincipal === 'string' ? currentPrincipal : null,
      // The map is the site root. Hard-coded rather than derived from the
      // current location, because this panel is reached from two pages and
      // neither of them is the one being asked about.
      mapPath: '/',
      areas: areas,
      orphanBuckets: orphanBuckets,
      stores: stores,
      mutations: { count: mutations },
      dbAvailable: !!db,
      timings: run.marks,
      elapsedMs: run.elapsed(),
    };
  }

  /**
   * One log line: elapsed, label, answer. Never more.
   *
   * @param {Object} check
   * @param {Document} doc
   * @returns {HTMLElement}
   */
  function renderLine(check, doc) {
    var core = self.pwaOfflineAuditCore;
    var row = doc.createElement('li');
    row.setAttribute('data-audit-check', '');
    row.setAttribute('data-audit-status', check.status);

    var at = doc.createElement('span');
    at.setAttribute('data-audit-at', '');
    at.textContent = core.formatElapsed(check.at);

    var label = doc.createElement('span');
    label.setAttribute('data-audit-label', '');
    label.textContent = check.label;
    // The label is the one field with no length bound (a cached URL, a
    // region name), and the row must stay one line — so CSS truncates and
    // the full text goes in the title for anyone who needs it.
    label.title = check.label;

    var value = doc.createElement('span');
    value.setAttribute('data-audit-value', '');
    value.textContent = check.value;

    row.appendChild(at);
    row.appendChild(label);
    row.appendChild(value);
    return row;
  }

  /**
   * "15 checks · 6 need attention", or the all-clear form.
   *
   * What makes the log's evidence legible at a glance: it says how many
   * separate things were looked at without the reader counting rows,
   * which is the whole reason the log is there.
   *
   * @param {Object} report
   * @param {Record<string, string>} t
   * @returns {string}
   */
  function countsLine(report, t) {
    var key = report.counts.attention > 0 ? 'counts-line' : 'counts-line-clear';
    var template = t[key] || FALLBACKS[key];
    return String(template)
      .replace('%(total)s', String(report.counts.total))
      .replace('%(attention)s', String(report.counts.attention));
  }

  /**
   * Paint the report into ``target``, replacing whatever was there.
   *
   * Three parts, in the order they are read: the log (the evidence), the
   * summary (the answer), and the count (how much was looked at).
   *
   * @param {HTMLElement} target
   * @param {Object} report
   * @param {Record<string, string>} t
   */
  function render(target, report, t) {
    var doc = target.ownerDocument;
    target.textContent = '';

    var log = doc.createElement('div');
    log.setAttribute('data-audit-log', '');
    report.sections.forEach(function (section) {
      if (section.checks.length === 0) return;
      var el = doc.createElement('section');
      el.setAttribute('data-audit-section', '');
      var heading = doc.createElement('h3');
      heading.textContent = section.title;
      el.appendChild(heading);
      var list = doc.createElement('ol');
      section.checks.forEach(function (check) {
        list.appendChild(renderLine(check, doc));
      });
      el.appendChild(list);
      log.appendChild(el);
    });
    target.appendChild(log);

    var summary = doc.createElement('div');
    summary.setAttribute('data-audit-summary', '');
    summary.setAttribute('data-audit-status', report.verdict.status);
    var verdict = doc.createElement('p');
    verdict.setAttribute('data-audit-verdict', '');
    verdict.textContent = report.verdict.text;
    summary.appendChild(verdict);
    if (report.summary) {
      var detail = doc.createElement('p');
      detail.setAttribute('data-audit-detail', '');
      detail.textContent = report.summary;
      summary.appendChild(detail);
    }
    target.appendChild(summary);

    var counts = doc.createElement('p');
    counts.setAttribute('data-audit-counts', '');
    counts.textContent = countsLine(report, t);
    target.appendChild(counts);
  }

  /**
   * Bind one host's markup.
   *
   * Every control is optional. The offline page carries no Save button
   * (there is nothing to save without a connection) and a host that omits
   * Copy simply has no Copy.
   *
   * @param {HTMLElement} root
   */
  function bind(root) {
    var t = strings();
    var output = root.querySelector('[data-offline-audit-output]');
    var runButton = root.querySelector('[data-offline-audit-run]');
    var copyButton = root.querySelector('[data-offline-audit-copy]');
    var saveButton = root.querySelector('[data-offline-audit-save]');
    var statusEl = root.querySelector('[data-offline-audit-status]');
    if (!output) return;

    var lastReport = null;

    var say = function (message) {
      if (statusEl) statusEl.textContent = message || '';
    };

    var run = async function () {
      say(t.running || FALLBACKS.running);
      var readings = await collect();
      lastReport = self.pwaOfflineAuditCore.buildReport(readings, t);
      render(output, lastReport, t);
      output.hidden = false;
      say('');
      if (copyButton) copyButton.hidden = false;
      if (saveButton) {
        // Offered only when it could actually work, and only when it is
        // the thing that would help: a controlled page, a connection, and
        // a map page that is not already saved for this account.
        var pageCheck = null;
        lastReport.sections.forEach(function (section) {
          section.checks.forEach(function (check) {
            if (check.id === 'map-page') pageCheck = check;
          });
        });
        saveButton.hidden = !(
          readings.online &&
          readings.serviceWorker.controlled &&
          pageCheck &&
          pageCheck.status !== 'ok'
        );
      }
    };

    if (runButton) {
      runButton.addEventListener('click', function () {
        run();
      });
    }

    if (copyButton) {
      copyButton.addEventListener('click', async function () {
        if (!lastReport) return;
        var text = self.pwaOfflineAuditCore.reportText(lastReport, {
          url: self.location ? self.location.href : '',
          userAgent: navigator.userAgent,
          appVersion: appVersion(),
        });
        try {
          await navigator.clipboard.writeText(text);
          say(t.copied || FALLBACKS.copied);
        } catch (_err) {
          say(t['copy-failed'] || FALLBACKS['copy-failed']);
        }
      });
    }

    if (saveButton) {
      saveButton.addEventListener('click', async function () {
        say(t.saving || FALLBACKS.saving);
        saveButton.disabled = true;
        try {
          // The worker's own warm path, not a `fetch` from here. SNOW-624
          // made it stamp a same-origin HTML response with the principal
          // its body declares — which is the whole reason the entry will
          // be servable offline. A `cache.put` from this page would write
          // an unstamped entry that the worker refuses for ever.
          var result =
            typeof self.pwaWarmCache === 'function'
              ? await self.pwaWarmCache(['/'])
              : null;
          if (result && result.ok > 0 && result.failed === 0) {
            say(t.saved || FALLBACKS.saved);
            await run();
          } else {
            say(t['save-failed'] || FALLBACKS['save-failed']);
          }
        } catch (_err) {
          say(t['save-failed'] || FALLBACKS['save-failed']);
        } finally {
          saveButton.disabled = false;
        }
      });
    }
  }

  /**
   * The deployed app version, for a copied report.
   *
   * @returns {string} Empty where the meta tag is absent — the offline
   *   page carries none.
   */
  function appVersion() {
    var meta = document.querySelector('meta[name="pwa-app-version"]');
    return (meta && meta.getAttribute('content')) || '';
  }

  /**
   * Bind every host present on this page.
   *
   * @returns {void}
   */
  function init() {
    var roots = document.querySelectorAll(ROOT_SELECTOR);
    for (var i = 0; i < roots.length; i += 1) {
      bind(/** @type {HTMLElement} */ (roots[i]));
    }
  }

  self.pwaOfflineAudit = Object.freeze({
    collect: collect,
    render: render,
    init: init,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
