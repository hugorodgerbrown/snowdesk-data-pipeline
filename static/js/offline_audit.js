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
 * its core are in the worker's ``PRECACHE_URLS``, and the offline page
 * loads them by their unhashed ``/static/`` path.
 *
 * Both hosts provide the same markup contract and style it themselves —
 * this module writes classless semantic HTML carrying
 * ``data-audit-status`` and nothing else. A Tailwind class string would
 * be inert on the offline page, whose stylesheet is inline and whose
 * whole design rule is "no external assets".
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
    'sw-unsupported': 'not supported by this browser',
    'detail-service-worker':
      'Nothing can be read offline until this is active. Reload the page while connected.',
    'label-update': 'Update',
    'update-waiting': 'waiting to install',
    'detail-update': 'A newer version is ready. Reload to install it.',
    'label-network-mode': 'Connection',
    'mode-forced': 'offline mode is on',
    'mode-latched': 'no usable connection',
    'detail-forced': 'You turned offline mode on. Turn it off to fetch anything new.',
    'detail-latched':
      'The app stopped trying after repeated timeouts. It retries on its own when a connection returns.',
    'label-storage': 'Space used',
    'storage-of': '%(used)s of %(total)s',
    'detail-storage-tight':
      'Close to the browser limit. Remove a downloaded area to stop the browser evicting one for you.',
    'detail-storage-unknown':
      'This browser does not report how much space the app is using.',
    'label-persisted': 'Protected from cleanup',
    'persisted-yes': 'yes',
    'persisted-no': 'no',
    'detail-persisted-no':
      'The browser may delete downloads when space runs low. Installing Snowdesk to the home screen usually grants protection.',

    'label-map-page': 'The map page',
    'page-saved': 'saved',
    'page-not-saved': 'not saved',
    'page-other-account': 'saved for another account',
    'detail-page-not-saved': 'Open the map once while connected and it will be saved.',
    'detail-page-other-account':
      'The saved copy belongs to %(stamped)s and you are signed in as %(current)s. Open the map once while connected to save your own copy.',
    'label-pages-saved': 'Pages you can open offline',
    'detail-pages-saved': '%(total)s saved on this device in total.',
    'page-entry-usable': 'ready',
    'page-entry-other': 'another account',

    'label-scripts': 'Program files',
    'label-styles': 'Styling',
    'label-feeds': 'Data feeds',
    'label-other-files': 'Fonts and images',
    'detail-no-scripts':
      'Without these a saved page opens blank. Reload while connected.',
    'detail-no-styles': 'Pages will open unstyled.',
    'detail-no-feeds':
      'Danger ratings may be missing offline. Open the map while connected.',

    'label-areas': 'Downloaded areas',
    'areas-none': 'none',
    'detail-areas-none': 'Download an area from the map to see it here without a signal.',
    'area-tiles': '%(tiles)s tiles, %(size)s',
    'area-incomplete': 'incomplete — %(count)s files missing',
    'area-missing': 'not on this device',
    'area-empty': 'empty',
    'detail-area-incomplete':
      'The map will not draw without them. Use Repair in the map’s Manage downloads sheet.',
    'detail-area-missing':
      'The record says this was downloaded but nothing is stored. Download it again.',
    'detail-area-unverifiable':
      'Downloaded before the app recorded what each area needs, so it cannot be fully checked. Download it again to verify it.',
    'label-base-layer': 'Zoomed-out overview (%(basemap)s)',
    'label-orphan': 'Unnamed area %(id)s',
    'orphan-value': 'stored, no record',
    'detail-orphan':
      'Tiles are taking up space with nothing pointing at them. Manage downloads on the map can remove them.',

    'label-favourites': 'Saved places',
    'label-overlays': 'Map overlays',
    'label-panel-rows': 'Reports',
    'label-mutations': 'Changes waiting to send',
    'detail-mutations': 'These will be sent the next time you have a connection.',
    'label-db': 'Local database',
    'detail-db-unavailable':
      'The local database could not be opened, so saved data cannot be checked. Private browsing blocks it on some browsers.',

    'principal-anonymous': 'a signed-out visitor',
    'principal-account': 'account %(id)s…',
    'principal-unknown': 'an unknown account',
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

    running: 'Checking…',
    copied: 'copied',
    'copy-failed': 'copy failed',
    saving: 'Saving the map page…',
    saved: 'Saved. Re-checking…',
    'save-failed': 'That could not be saved. Try again while connected.',
    'generated-at': 'Checked %(time)s',
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
        if (event.data && event.data.type === 'version')
          finish(event.data.version || null);
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
   *   which overstates rather than understates — noted on the report by
   *   the caller.
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
          // carry those. An empty list is the core's UNKNOWN, which is the
          // honest answer rather than a pass.
          deps: [],
        });
      });
    }
    return areas;
  }

  /**
   * Take every reading the report is built from.
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
      mutations: { count: mutations },
      dbAvailable: !!db,
    };
  }

  /**
   * One check, as a definition-list row.
   *
   * @param {Object} check
   * @param {Document} doc
   * @returns {HTMLElement}
   */
  function renderCheck(check, doc) {
    var row = doc.createElement('div');
    row.setAttribute('data-audit-check', '');
    row.setAttribute('data-audit-status', check.status);
    var term = doc.createElement('dt');
    term.textContent = check.label;
    var value = doc.createElement('dd');
    value.textContent = check.value;
    row.appendChild(term);
    row.appendChild(value);
    if (check.detail) {
      var detail = doc.createElement('p');
      detail.setAttribute('data-audit-detail', '');
      detail.textContent = check.detail;
      row.appendChild(detail);
    }
    return row;
  }

  /**
   * Paint the report into ``target``, replacing whatever was there.
   *
   * @param {HTMLElement} target
   * @param {Object} report
   * @param {Record<string, string>} t
   */
  function render(target, report, t) {
    var doc = target.ownerDocument;
    target.textContent = '';

    var verdict = doc.createElement('p');
    verdict.setAttribute('data-audit-verdict', '');
    verdict.setAttribute('data-audit-status', report.verdict.status);
    verdict.textContent = report.verdict.text;
    target.appendChild(verdict);

    report.sections.forEach(function (section) {
      var el = doc.createElement('section');
      el.setAttribute('data-audit-section', '');
      el.setAttribute('data-audit-status', section.status);
      var heading = doc.createElement('h3');
      heading.textContent = section.title;
      el.appendChild(heading);
      var list = doc.createElement('dl');
      section.checks.forEach(function (check) {
        list.appendChild(renderCheck(check, doc));
      });
      el.appendChild(list);
      target.appendChild(el);
    });

    var stamp = doc.createElement('p');
    stamp.setAttribute('data-audit-generated', '');
    stamp.textContent = String(t['generated-at'] || FALLBACKS['generated-at']).replace(
      '%(time)s',
      new Date(report.generatedAt).toLocaleString(),
    );
    target.appendChild(stamp);
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
