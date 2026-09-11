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
 * Strictly one, and nothing under it. A row is label and answer, and
 * everything a failing row would want to explain is composed into the
 * summary instead (``composeSummary``). The rule is not stylistic:
 * per-row helper text made this panel three times taller, turned
 * scanning into reading, and repeated one remedy across three rows
 * rather than saying it once. The renderer enforces it by having nowhere
 * to put a second line, and ``tests/js/test_offline_audit.js`` asserts a
 * row never grows one.
 *
 * ## The build is an animation, and says nothing about timing
 *
 * Every reading is taken before a single answer is painted. The rows then
 * fill in on a fixed ``ROW_INTERVAL_MS`` cadence — a reveal, not a
 * measurement, and deliberately carrying no numbers that could be read as
 * one. An earlier cut printed each row's real elapsed time; on any
 * ordinary device that was fifteen rows of ``0.00s``, which looked like
 * precision and conveyed nothing.
 *
 * What the build IS for: the list of questions is on screen in full,
 * unanswered, from the first frame, and the reader watches each one get
 * answered. That is the whole confidence argument — thirteen separate
 * things were asked, and here is each of them being settled — and it is
 * why the skeleton is painted before the answers rather than the rows
 * appearing one at a time out of nothing.
 *
 * ``prefers-reduced-motion`` skips the cadence and paints the answered
 * report in one go. Nothing is lost: the reveal is decoration over a
 * report that is complete before it starts.
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

  // How long between one row's answer and the next. Thirteen rows plus
  // however many downloads, at 70ms, is about a second — long enough to
  // read as deliberate, short enough that nobody waits for it. It is an
  // animation over an already-finished report; see the header.
  var ROW_INTERVAL_MS = 70;

  // SNOW-620: server-translated copy where a template provides it, the
  // English literal everywhere else. `pwaStrings` is not loaded on the
  // offline page, so the fallbacks are not a safety net there — they are
  // the copy, exactly as every other string on that page is.
  //
  // Four families:
  //   section-* / label-*   the log's section rules and its left column
  //   the answers           what a row's right column says
  //   note-*                one standalone clause for the summary
  //   group-* / effect-*    the parts the summary composes into a single
  //                         sentence when several Nos share one remedy
  var FALLBACKS = {
    'section-access': 'Getting in',
    'section-map': 'The map',
    'section-regions': 'Regions you downloaded',
    'section-dropzones': 'Drop zones you downloaded',
    'section-custom': 'Areas you drew',
    'section-content': 'Your content',
    'section-keeping': 'Keeping it',

    'row-offline-mode': 'Offline mode is on',
    'row-app-opens': 'The app opens',
    'row-app-complete': 'The app is complete',
    'row-danger-ratings': 'Danger ratings',
    'row-region-shapes': 'Region outlines',
    'row-overview': 'Zoomed-out overview',
    'row-no-downloads': 'Map areas downloaded',
    'row-bulletins': 'Bulletins you have opened',
    'row-saved-places': 'Your saved places',
    'row-reports': 'Community reports',
    'row-weather': 'Weather',
    'row-protected': 'Safe from browser cleanup',
    'row-room': 'Room for more',
    'row-unsent': 'Changes you make are kept',

    'answer-yes': 'Yes',
    'answer-no': 'No',
    'answer-unknown': '—',
    'answer-pending': '…',

    'note-sw-unsupported': 'this browser has no offline mode at all',
    'note-sw-starting': 'offline mode is starting up and will be ready on the next load',
    'note-sw-absent': 'offline mode has not been set up on this device',
    'note-other-account': 'the saved copy belongs to %(stamped)s',
    'note-no-areas': 'no map area is downloaded, so there is no ground to draw',
    'note-no-overview': 'zooming out past a downloaded area will show nothing',
    'note-area-incomplete':
      '%(name)s will not draw until you repair it from the map’s Manage downloads sheet',
    'note-area-missing':
      '%(name)s is recorded as downloaded but nothing is stored, so download it again',
    'note-area-unverifiable':
      '%(name)s was downloaded before the app recorded what an area needs, so it cannot be checked',
    'note-no-bulletins': 'no bulletin has been opened on this device yet',
    'note-no-favourites': 'none of your saved places has been loaded here yet',
    'note-no-reports': 'community reports have not been loaded on this device',
    'note-no-weather': 'the weather overlay has not been opened on this device',
    'note-not-persisted':
      'the browser may delete downloads when space runs low, and installing Snowdesk to the home screen usually stops that',
    'note-no-room':
      'only %(used)s of %(total)s is left, so the browser may start deleting downloads',
    'note-no-db': 'the local database would not open, so nothing can be saved here',
    'note-unsent': '%(n)s of your changes are waiting to be sent',

    'effect-styles': 'will look plain',
    'effect-ratings': 'will show no danger ratings',
    'effect-shapes': 'will draw no region outlines',
    'group-open-map-lead': 'Without a signal the app %(effects)s.',
    'group-open-map-remedy': 'Opening the map once while connected fixes %(count)s.',

    'principal-anonymous': 'a signed-out visitor',
    'principal-account': 'account %(id)s…',
    'principal-unknown': 'an unknown account',

    'verdict-ok': 'Everything you need is on this device.',
    'verdict-no-worker': 'This device is not set up for offline use yet.',
    'verdict-no-page':
      'The app will not open without a signal. Open the map once while connected.',
    'verdict-other-account':
      'The saved app belongs to another account. Open the map once while connected.',
    'verdict-incomplete-app':
      'The app would open blank without a signal. Open the map once while connected.',
    'verdict-no-map': 'The app opens, but there is no map to show in it.',
    'verdict-downloads-broken': 'The app opens, but none of your downloads will draw.',

    'notes-sentence': 'Also worth knowing: %(notes)s.',
    'list-pair': '%(first)s and %(last)s',
    'list-join': '%(first)s, %(rest)s',
    'count-one': 'it',
    'count-two': 'both',
    'count-many': 'all %(n)s of them',

    'counts-line': '%(yes)s of %(total)s available offline',

    running: 'Checking…',
    copied: 'copied',
    'copy-failed': 'copy failed',
    saving: 'Saving the app…',
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
   * The keys a keyPath-addressed store holds.
   *
   * ``data:map_overlays`` and ``data:panel_rows`` each hold one row per
   * RESOURCE, so the key is the answer: 'weather' being present is what
   * makes "will the weather show" a Yes. A row count would say three and
   * mean nothing.
   *
   * @param {IDBDatabase} db
   * @param {string} name
   * @returns {Promise<string[]>} ``[]`` where the store does not exist or
   *   cannot be read.
   */
  function readKeys(db, name) {
    return new Promise(function (resolve) {
      try {
        if (!db.objectStoreNames.contains(name)) {
          resolve([]);
          return;
        }
        var request = db.transaction(name, 'readonly').objectStore(name).getAllKeys();
        request.onsuccess = function () {
          resolve(
            (request.result || []).map(function (key) {
              return String(key);
            }),
          );
        };
        request.onerror = function () {
          resolve([]);
        };
      } catch (_err) {
        resolve([]);
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
          // SNOW-XXX: a drop zone is its own kind, not a custom area with
          // a particular name — `basemap_manage_core.js` reads the same
          // field for the same reason, and the report groups by it.
          type: record.type === 'dropzone' ? 'dropzone' : 'custom',
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
    var swSupported = 'serviceWorker' in navigator;
    var registration = null;
    if (swSupported) {
      try {
        registration = await navigator.serviceWorker.getRegistration();
      } catch (_err) {
        registration = null;
      }
    }

    var live = await liveShellCacheName();
    var names = await cacheNames();
    var shellNames = live
      ? [live]
      : names.filter(function (name) {
          return name.indexOf(SHELL_CACHE_PREFIX) === 0;
        });
    var shellEntries = shellNames.length ? await readShellEntries(shellNames) : [];

    var db = await openDb();
    var areaRecords = await readAreaRecords(db);
    var areas = [];
    for (var i = 0; i < areaRecords.length; i += 1) {
      var bucket = await readBucket(areaRecords[i].id);
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

    var stores = {};
    if (db) {
      for (var j = 0; j < DATA_STORES.length; j += 1) {
        stores[DATA_STORES[j]] = await countStore(db, DATA_STORES[j]);
      }
    }
    // WHICH overlays are cached, not how many rows there are. The store
    // holds one row per resource — 'favourites', 'community_reports',
    // 'weather', 'routes' — so a count answers nothing a user asked, and
    // the key answers "will the weather show".
    var overlayKeys = db ? await readKeys(db, 'data:map_overlays') : [];
    var panelKeys = db ? await readKeys(db, 'data:panel_rows') : [];
    var mutations = db ? await countStore(db, 'queue:mutations') : null;
    var currentPrincipal = db ? await readMeta(db, 'mutations.principal') : null;
    var networkMode = db ? await readMeta(db, 'network.mode') : null;
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
        if (typeof navigator.storage.persisted === 'function') {
          storage = Object.assign({}, storage, {
            persisted: await navigator.storage.persisted(),
          });
        }
      }
    } catch (_err) {
      storage = null;
    }

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
      overlayKeys: overlayKeys,
      panelKeys: panelKeys,
      mutations: { count: mutations },
      dbAvailable: !!db,
    };
  }

  /**
   * One log row: label, answer. Never more.
   *
   * Two cells and no third, which is what stops per-row explanation
   * growing back. ``data-audit-row`` is the handle the reveal uses to
   * find this row again once its answer is known.
   *
   * @param {Object} check
   * @param {Document} doc
   * @returns {HTMLElement}
   */
  function renderRow(check, doc) {
    var row = doc.createElement('li');
    row.setAttribute('data-audit-check', '');
    row.setAttribute('data-audit-row', check.id);
    row.setAttribute('data-audit-status', check.status);

    var label = doc.createElement('span');
    label.setAttribute('data-audit-label', '');
    label.textContent = check.label;
    // The label is the one field with no length bound (a region name, an
    // area the user named themselves), and the row must stay one line —
    // so CSS truncates and the full text goes in the title.
    label.title = check.label;

    var value = doc.createElement('span');
    value.setAttribute('data-audit-value', '');
    value.textContent = check.value;

    row.appendChild(label);
    row.appendChild(value);
    return row;
  }

  /**
   * Paint the log — every section, every row — with whatever answers the
   * report carries.
   *
   * Called once with the answers blanked and once, implicitly, as each
   * one arrives: the DOM built here is what ``revealRow`` updates in
   * place, so the row list never changes shape mid-build.
   *
   * @param {HTMLElement} target
   * @param {Object} report
   */
  function renderLog(target, report) {
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
        list.appendChild(renderRow(check, doc));
      });
      el.appendChild(list);
      log.appendChild(el);
    });
    target.appendChild(log);
  }

  /**
   * Fill one row's answer into the row already on screen.
   *
   * @param {HTMLElement} target
   * @param {Object} check
   */
  function revealRow(target, check) {
    var row = target.querySelector('[data-audit-row="' + cssEscape(check.id) + '"]');
    if (!row) return;
    row.setAttribute('data-audit-status', check.status);
    var value = row.querySelector('[data-audit-value]');
    if (value) value.textContent = check.value;
  }

  /**
   * Quote a row id for use inside an attribute selector.
   *
   * Area rows are keyed ``area:region-CH-4115``, and a colon in a
   * selector is a pseudo-class. ``CSS.escape`` where the browser has it,
   * a backslash before every non-word character otherwise — this runs on
   * the offline page, which is the one surface with no polyfills at all.
   *
   * @param {string} value
   * @returns {string}
   */
  function cssEscape(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
      return CSS.escape(value);
    }
    return String(value).replace(/[^\w-]/g, '\\$&');
  }

  /**
   * The summary and the count, appended under the finished log.
   *
   * @param {HTMLElement} target
   * @param {Object} report
   * @param {Record<string, string>} t
   */
  function renderSummary(target, report, t) {
    var doc = target.ownerDocument;
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
    counts.textContent = String(t['counts-line'] || FALLBACKS['counts-line'])
      .replace('%(yes)s', String(report.counts.yes))
      .replace('%(total)s', String(report.counts.total));
    target.appendChild(counts);
  }

  /**
   * Paint a finished report in one go, no build.
   *
   * The reduced-motion path, and the one the tests drive: the reveal is
   * decoration over a report that is complete before it starts, so
   * skipping it loses nothing.
   *
   * @param {HTMLElement} target
   * @param {Object} report
   * @param {Record<string, string>} t
   */
  function render(target, report, t) {
    renderLog(target, report);
    if (!report.pending) renderSummary(target, report, t);
  }

  /**
   * Whether this device has asked for less animation.
   *
   * @returns {boolean}
   */
  function prefersReducedMotion() {
    try {
      return !!(
        self.matchMedia && self.matchMedia('(prefers-reduced-motion: reduce)').matches
      );
    } catch (_err) {
      return false;
    }
  }

  /**
   * Wait, as a promise.
   *
   * @param {number} ms
   * @returns {Promise<void>}
   */
  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  /**
   * Paint the log unanswered, then fill each answer in on a fixed
   * cadence, then the summary.
   *
   * The order matters: the whole list of questions is on screen before
   * the first answer lands, so the reader sees what is being asked rather
   * than watching rows appear out of nothing. Every reading was taken
   * before this was called.
   *
   * @param {HTMLElement} target
   * @param {Object} report
   * @param {Record<string, string>} t
   * @returns {Promise<void>}
   */
  async function build(target, report, t) {
    if (prefersReducedMotion()) {
      render(target, report, t);
      return;
    }
    var blanked = {
      sections: report.sections.map(function (section) {
        return {
          id: section.id,
          title: section.title,
          checks: section.checks.map(function (check) {
            return {
              id: check.id,
              label: check.label,
              value: t['answer-pending'] || FALLBACKS['answer-pending'],
              status: 'pending',
            };
          }),
        };
      }),
    };
    renderLog(target, blanked);
    for (var i = 0; i < report.sections.length; i += 1) {
      var checks = report.sections[i].checks;
      for (var j = 0; j < checks.length; j += 1) {
        await wait(ROW_INTERVAL_MS);
        revealRow(target, checks[j]);
      }
    }
    renderSummary(target, report, t);
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
      // The fixed questions, unanswered, before anything is read. The
      // downloads are not among them yet — nothing knows what this device
      // holds — so the list grows once by however many areas there are,
      // and then stops moving.
      output.hidden = false;
      renderLog(output, self.pwaOfflineAuditCore.pendingReport(t));
      var readings = await collect();
      lastReport = self.pwaOfflineAuditCore.buildReport(readings, t);
      output.hidden = false;
      await build(output, lastReport, t);
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
    build: build,
    init: init,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
