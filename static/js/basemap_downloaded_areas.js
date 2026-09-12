/*
 * static/js/basemap_downloaded_areas.js — the one reader of what this
 * device has downloaded (SNOW-860).
 *
 * Extracted from `static/js/map_basemap_downloads.js`, which is 2,500+
 * lines of map-page machinery: it reads `MAP`, `COUNTRY_STATE` and
 * `RATINGS_URL` as bare identifiers from a shared global lexical scope,
 * and carries a load-order contract with `map.js` (see its own header).
 * None of that can be loaded onto an account page — and an account page
 * is exactly what SNOW-860 needs the answer on, because
 * `/account/settings/`'s "Reset local data" row now states what the
 * reset is about to delete.
 *
 * So the reader moved out and nothing else did. `basemapDownloadedAreas`,
 * `pinnedBucketAreaIds` and the two record reads behind them are here;
 * `map_basemap_downloads.js` delegates to this module rather than
 * keeping a second copy, because the whole point of the extraction is
 * that the Manage downloads sheet and the settings-page breakdown can
 * never disagree about what is on the device.
 *
 * Page-agnostic by construction: it reads nothing from another file's
 * lexical scope, touches no DOM, and every dependency it does have is
 * looked up on `self`/`window` at CALL time —
 * `pwaBasemapDownloadCore` (area-id grammar and the pinned-cache
 * prefix), `pwaBasemapManageCore` (`reconcileAreas`), `pwaDb`,
 * `pwaDownloadsSync` (optional) and `pwaDebugLog` (optional). Load order
 * therefore does not matter for THIS file's own boot; it matters only in
 * that `map_basemap_downloads.js` calls into it, so it is loaded before
 * the map bundle on the map page.
 *
 * Best-effort throughout, and deliberately so: this sits on the map's
 * boot path (the download roundel probes it on every page load), so a
 * transient IndexedDB or Cache Storage failure must read as "nothing
 * recorded" rather than throw and take the surface down with it.
 *
 * Exports (frozen `window.pwaBasemapAreas`):
 *
 *   downloadedAreas(options)
 *     Every area this device has downloaded, normalised into the
 *     `[{id, name, bytes, savedAt, basemapKey, …}]` shape
 *     `pwaBasemapManageCore.manageRows` and `planEviction` consume.
 *   pinnedBucketAreaIds()
 *     Every area id with a pinned Cache Storage bucket present — the
 *     ground truth for what is actually stored.
 *   readBaseLayers()
 *     The `meta:app` `basemap.baseLayers` record (SNOW-856).
 *   readCustomAreas()
 *     The `meta:app` `basemap.customAreas` record, migrating the legacy
 *     single-row `basemap.customArea` into it on first read.
 *   CUSTOM_AREAS_KEY / LEGACY_CUSTOM_AREA_KEY / BASE_LAYERS_KEY
 *     The `meta:app` keys above, so the writers left behind in
 *     `map_basemap_downloads.js` name the same rows this reader does.
 */

(function () {
  'use strict';

  // SNOW-635: the array-shaped record replacing the old single-row
  // `basemap.customArea` — see `readCustomAreas`'s docstring for the lazy
  // migration between the two.
  var CUSTOM_AREAS_KEY = 'basemap.customAreas';
  var LEGACY_CUSTOM_AREA_KEY = 'basemap.customArea';

  // SNOW-856: the shared base layers, one entry per basemap that has
  // one — `[{basemapKey, band, bbox, bytes, savedAt}]`. A sibling of the
  // record above rather than an entry in it, because a base layer is not
  // an area: no user chose it, no user can delete it, and it outlives
  // every area that shares it.
  var BASE_LAYERS_KEY = 'basemap.baseLayers';

  // The English fallbacks for the two names this module fills in. The
  // translated forms are passed in by the caller (`options.strings`),
  // which is what keeps this module free of a template id: the map page
  // reads them out of `map-strings-template` and the settings page out of
  // its own panel's template, and neither has to know about the other's.
  var FALLBACK_STRINGS = {
    'default-custom-name': 'Custom area %(n)s',
    'base-layer-name': 'Overview map',
  };

  /**
   * Look one name up, preferring the caller's translated copy.
   *
   * @param {Object<string, string>} strings Caller-supplied strings.
   * @param {string} key
   * @returns {string}
   */
  function stringFor(strings, key) {
    return (strings && strings[key]) || FALLBACK_STRINGS[key];
  }

  /**
   * Every area id with a pinned bucket present in Cache Storage
   * (SNOW-612).
   *
   * The bucket is the ground truth for what is actually stored; the
   * `basemap.regions` / `basemap.customAreas` records are only what
   * COMPLETED runs left behind. A download that failed partway leaves the
   * former without the latter, which is exactly the stranded quota this
   * reader exists to surface — see `downloadedAreas` below.
   *
   * Never throws: Cache Storage being unavailable reads as "no buckets",
   * which degrades to the pre-SNOW-612 behaviour of trusting the records
   * alone rather than blocking anything.
   *
   * @returns {Promise<string[]>}
   */
  async function pinnedBucketAreaIds() {
    var core = self.pwaBasemapDownloadCore;
    if (!core || typeof caches === 'undefined') return [];
    var prefix = core.PINNED_CACHE_PREFIX;
    try {
      var names = await caches.keys();
      return names
        .filter(function (name) {
          return name.startsWith(prefix);
        })
        .map(function (name) {
          return name.slice(prefix.length);
        })
        .filter(Boolean);
    } catch (_e) {
      return [];
    }
  }

  /**
   * SNOW-856: the `basemap.baseLayers` record. Best-effort — a failed
   * read is "no base layer recorded", which makes the next download
   * re-warm one it may already hold. That costs a `cache.keys()` walk and
   * no bytes (`resolveBaseLayerPlan` fetches only what is genuinely
   * missing), which is the right way round for a read that must never
   * throw on the boot path.
   *
   * @returns {Promise<Array<Object>>}
   */
  async function readBaseLayers() {
    if (!window.pwaDb) return [];
    try {
      var row = await window.pwaDb.get('meta:app', BASE_LAYERS_KEY);
      return Array.isArray(row && row.value) ? row.value : [];
    } catch (_e) {
      return [];
    }
  }

  /**
   * SNOW-635: read `basemap.customAreas`, migrating the legacy single-row
   * `basemap.customArea` into it on first read if the new key is absent.
   *
   * Lazy rather than a one-off migration command, because this runs inside
   * `downloadedAreas()` — the boot-path probe the roundel calls on every
   * page load (post-SNOW-634) — so every existing device reaches it
   * without a separate step. Best-effort throughout, and this MUST degrade
   * to "read the legacy row as a one-entry list" rather than throw: a
   * device that cannot write here would otherwise take both the roundel
   * and the manage sheet down with it, since both sit on this same
   * boot-path read.
   *
   * The legacy area keeps id `CUSTOM_AREA_ID` ('custom') and ordinal `1` —
   * its existing `snowdesk-basemap-pinned-custom` Cache Storage bucket has
   * no rename, so the id has to survive unchanged for that bucket to keep
   * resolving (docs/decisions/per-area-pinned-basemap-caches.md).
   *
   * @returns {Promise<Array<Object>>} `[]` when nothing is stored and
   *   there is no legacy row to migrate.
   */
  async function readCustomAreas() {
    if (!window.pwaDb) return [];
    var row;
    try {
      row = await window.pwaDb.get('meta:app', CUSTOM_AREAS_KEY);
    } catch (_e) {
      row = undefined;
    }
    // An empty array is a legitimate "already migrated, nothing left" —
    // return it as-is rather than falling through to the legacy read, or a
    // device that deleted its last custom area would have it re-created
    // from a legacy row that (by then) no longer exists anyway.
    if (Array.isArray(row && row.value)) return row.value;

    var legacyRow;
    try {
      legacyRow = await window.pwaDb.get('meta:app', LEGACY_CUSTOM_AREA_KEY);
    } catch (_e) {
      legacyRow = undefined;
    }
    var legacy = legacyRow && legacyRow.value;
    if (!legacy || !Array.isArray(legacy.bbox)) return [];

    var core = self.pwaBasemapDownloadCore;
    var migrated = [
      Object.assign({}, legacy, { id: core ? core.CUSTOM_AREA_ID : 'custom', ordinal: 1 }),
    ];
    try {
      await window.pwaDb.put('meta:app', { key: CUSTOM_AREAS_KEY, value: migrated });
      await window.pwaDb.delete('meta:app', LEGACY_CUSTOM_AREA_KEY);
    } catch (_e) {
      // Best-effort — see docstring above. The legacy row is untouched, so
      // the next read tries the migration again; this call still returns
      // the migrated shape for ITS OWN caller even though the write didn't
      // land.
    }
    return migrated;
  }

  // SNOW-586: every area currently recorded as downloaded, normalised into
  // planEviction's `[{id, name, bytes, savedAt}]` shape — the union of
  // `basemap.regions` (mapDownloadControlInit's record, one entry per
  // downloaded region) and (SNOW-635) `basemap.customAreas`
  // (mapCustomDownloadControlInit's record, now an array — see
  // `readCustomAreas`). `name` is always populated for a non-orphaned area
  // — stored for a region, stored-or-defaulted-from-ordinal for a custom
  // area (see the inline comment below) — so every downstream reader can
  // treat it uniformly; only `reconcileAreas`' orphan entries (no record at
  // all) ever leave it unset. Best-effort: a failed read contributes
  // nothing rather than throwing — eviction planning degrades to "nothing
  // recorded, so nothing to evict", never to blocking a download outright
  // over a transient IndexedDB error.
  //
  // @param {{strings?: Object<string, string>,
  //   measureBytes?: function(string): Promise<number>}} [options]
  //   `strings` carries the two translated names this reader fills in
  //   (see `FALLBACK_STRINGS`). `measureBytes` measures an ORPHANED
  //   bucket — one with no record to read a byte total off — and is
  //   `map_basemap_downloads.js`'s `measurePinnedBucketBytes`, injected
  //   rather than moved: it walks every entry of a bucket, which is work
  //   the map page is already set up to absorb and an account page has no
  //   business doing on load. Absent, an orphan reads 0 bytes, which is
  //   what that measurement returns in production anyway — the tile
  //   origin sends no `Content-Length` under gzip (see that function's
  //   own docstring).
  //
  // @returns {Promise<Array<{id: string, name?: string, bytes: number,
  //   savedAt: string, basemapKey: string|null}>>} `basemapKey` (SNOW-645)
  //   is the basemap the area was fetched under, null for a record written
  //   before that ticket or for a reconciled orphan — "downloaded, basemap
  //   unknown". SNOW-722: named here because it is load-bearing outside the
  //   eviction path now (map_layer_sync_status.js decides each basemap
  //   row's dot on it), and the abbreviated shape above read as though it
  //   were dropped.
  async function downloadedAreas(options) {
    var opts = options || {};
    var strings = opts.strings || {};
    var core = self.pwaBasemapDownloadCore;
    var areas = [];
    if (!core || !window.pwaDb) return areas;
    try {
      var row = await window.pwaDb.get('meta:app', 'basemap.regions');
      var regions = Array.isArray(row && row.value) ? row.value : [];
      for (var entry of regions) {
        if (!entry || !entry.region_id) continue;
        areas.push({
          id: core.areaIdForRegion(entry.region_id),
          name: entry.name || entry.region_id,
          bytes: Number(entry.bytes) || 0,
          savedAt: entry.savedAt,
          // SNOW-645: absent on a record written before this ticket shipped
          // — reads as "downloaded, basemap unknown" rather than a wrong one.
          basemapKey: entry.basemapKey || null,
          // SNOW-749: the region id, carried so `reconcileAreas` can compare
          // and `downloads_sync.js` can describe this area to the account
          // without re-parsing it back out of the bucket id — that format
          // belongs to `areaIdForRegion` and is deliberately never
          // reverse-engineered elsewhere.
          regionId: entry.region_id,
          // SNOW-844: the render dependencies the run recorded, so the
          // Manage downloads sheet can check a row whose basemap is not the
          // one on screen. Absent on every record written before that ticket
          // — normalised to `[]` here, which the sheet reads as UNKNOWN
          // rather than as "nothing needed".
          deps: Array.isArray(entry.deps) ? entry.deps : [],
          // SNOW-692: the run's own tile row spans, carried so the slope
          // tiles this area should hold can be DERIVED by the probe rather
          // than recorded on it (~273 URLs, 27.4 KB per region, all of it
          // recomputable from this). Null on a record written before
          // SNOW-583, which carried `bbox` and no `z` — the same absence
          // the roundel's own tile probe already reads as "no record".
          z: entry.z || null,
          band: Array.isArray(entry.band) ? entry.band : null,
        });
      }
    } catch (_e) {
      // Best-effort — see docstring.
    }
    try {
      var customAreas = await readCustomAreas();
      for (var custom of customAreas) {
        if (!custom || !custom.id || !Array.isArray(custom.bbox)) continue;
        areas.push({
          id: custom.id,
          // SNOW-635 (review): `name` is set by a rename
          // (map_downloads_manager.js's Rename control) when present; an
          // unrenamed area's default display name ("Custom area N") is
          // filled in HERE, from `ordinal`, in memory only — never
          // persisted, so it stays translatable rather than freezing in
          // whatever language was active at download time. Filling it at
          // THIS single normalising layer, rather than at every
          // downstream reader, is what let the eviction confirm banner's
          // fallback regress to a raw id: the banner (and the sheet, and
          // the rename prompt's pre-fill) can all just read `area.name`
          // uniformly now, with nothing left to distinguish "stored" from
          // "defaulted".
          name:
            custom.name ||
            (Number.isFinite(custom.ordinal)
              ? self.pwaStrings.interpolate(stringFor(strings, 'default-custom-name'), {
                  n: custom.ordinal,
                })
              : custom.id),
          bytes: Number(custom.bytes) || 0,
          savedAt: custom.savedAt,
          // SNOW-645: see the region branch above for the "unknown" fallback.
          basemapKey: custom.basemapKey || null,
          // SNOW-867: which KIND of user-made area this is — a framed box
          // ('custom') or a circle dropped on the user's own position
          // ('dropzone'). The sheet names it in words on every row, and a
          // record written before the field existed is what it was: a
          // framed box.
          type: custom.type === 'dropzone' ? 'dropzone' : 'custom',
          // SNOW-749: a custom area IS its box — it is the only thing that
          // lets another device (or this one after an eviction) fetch the
          // same ground again, so it travels with the area.
          bbox: custom.bbox,
          // SNOW-844: see the region branch above.
          deps: Array.isArray(custom.deps) ? custom.deps : [],
          // SNOW-692: see the region branch above. A custom area has no
          // `z` — its tiles were never server-computed — so the band it
          // was fetched over travels beside `bbox`, and the probe rebuilds
          // the blob from the pair through `buildBlob`, the same
          // client-side twin that produced the tile set in the first place.
          band: Array.isArray(custom.band) ? custom.band : null,
        });
      }
    } catch (_e) {
      // Best-effort — see docstring.
    }

    // SNOW-856: and the shared base layers. They belong in this list for
    // exactly one reason — they are real bytes on the device, and a total
    // that counts what it does not list is worse than one that lists
    // everything. Every consumer for which a base layer is NOT an area
    // excludes it explicitly via `core.isBaseLayerAreaId`: `planEviction`
    // (never a candidate), `manageRows` (SNOW-867 — not a row on the
    // downloads panel and not in its budget either) and
    // `map_layer_sync_status.js` (a basemap with only a base layer has no
    // ground downloaded, so its dot must not go green) and
    // `downloads_sync.js`'s `adopt` (SNOW-860 — a base layer is not one of
    // the user's downloads, and `area_sync`'s `_AREA_ID_RE` rejects the
    // `base-` prefix outright). That last one was MISSED when this list
    // widened, and the cost is the shape to expect if another consumer is
    // ever forgotten: the push 400d, the mutation queue classified it
    // permanent, and every signed-in user with a download carried a dead
    // queue row and a red sync badge. Adding an entry to this list means
    // auditing every reader of it.
    //
    // SNOW-860's settings-page breakdown is the surface that DOES list it,
    // which is the place the app's own storage is stated and cleared.
    //
    // SNOW-863: driven by the BUCKETS on disk, joined to the records for
    // their sizes — not by the records alone, which is what shipped and was
    // wrong. A base layer's record is written by the page once the service
    // worker's warm resolves, and that warm is the tail of a download the
    // roundel has already reported as finished; a reader who closes the tab
    // in between (or reloads, or whose device sleeps) is left with a
    // complete bucket and no record. Reported on staging as a row reading
    // "base-swisstopo_winter" under "Unknown basemap", with a delete button
    // — the reconciliation below had picked the bucket up as an orphan,
    // because nothing in the record-driven pass could name it.
    //
    // The bucket names its own basemap now (`baseLayerBasemapKey`), so a
    // missing record costs only the SIZE, which reads 0 until the next
    // download's top-up writes one. An unsized row is a small lie; an
    // unnamed deletable one was a trap.
    try {
      var byKey = new Map();
      for (var layer of await readBaseLayers()) {
        if (layer && layer.basemapKey) byKey.set(layer.basemapKey, layer);
      }
      for (var baseId of await pinnedBucketAreaIds()) {
        if (!core.isBaseLayerAreaId(baseId)) continue;
        var basemapKey = core.baseLayerBasemapKey(baseId);
        if (!basemapKey) continue;
        var record = byKey.get(basemapKey);
        areas.push({
          id: baseId,
          name: stringFor(strings, 'base-layer-name'),
          bytes: Number(record && record.bytes) || 0,
          savedAt: (record && record.savedAt) || '',
          basemapKey: basemapKey,
          bbox: record && record.bbox,
          // SNOW-929: the documents this base layer needs to DRAW — the
          // style, each source's TileJSON, the sprite pair and the glyph
          // ranges, fetched into this very bucket beside the band. This
          // used to read `deps: []` under a comment asserting "the tiles
          // ARE the layer", which was the bug: the tiles are not the
          // layer, they are unreadable without those four, and they sat
          // in an evictable passive cache instead.
          //
          // An empty list here now means a record written BEFORE
          // SNOW-929, not a layer that needs nothing. The next warm of
          // this basemap writes one; `areaState` (offline_audit_core.js)
          // is the reader that has to keep telling those two apart.
          deps: Array.isArray(record && record.deps) ? record.deps : [],
        });
      }
    } catch (_e) {
      // Best-effort — see docstring.
    }

    // SNOW-612: union in the pinned buckets actually on disk. A record is
    // only written when a run COMPLETES, so a download that failed partway
    // left a bucket the budget never counted and the manage sheet could not
    // delete — quota that accumulated silently across failed attempts.
    // Without the manage core there is no reconciliation to run, so this
    // degrades to the records alone rather than to nothing.
    var manage = self.pwaBasemapManageCore;
    if (!manage || typeof manage.reconcileAreas !== 'function') return areas;
    var storedIds = await pinnedBucketAreaIds();
    var recordedIds = new Set(
      areas.map(function (area) {
        return area.id;
      }),
    );
    var orphanIds = storedIds.filter(function (id) {
      return !recordedIds.has(id);
    });
    // SNOW-812: the two halves of "is this area downloaded" side by side —
    // the records the page keeps in meta:app, and the pinned buckets
    // actually on disk. `missing` is the one that matters for a blank map:
    // an area the UI reports as downloaded with no bucket behind it. It is
    // the same comparison sw.js's `pinned.buckets` line records from the
    // other side, so the two can be read against each other.
    window.pwaDebugLog?.record('cache', 'areas.reconcile', {
      recorded: [...recordedIds],
      onDisk: storedIds,
      orphans: orphanIds,
      missing: [...recordedIds].filter(function (id) {
        return !storedIds.includes(id);
      }),
    });
    // Measured one bucket at a time rather than in parallel: an orphan is
    // rare, and a concurrent walk of several thousand cache entries each is
    // the kind of burst that makes a slow device feel broken.
    var bytesById = {};
    for (var orphanId of orphanIds) {
      bytesById[orphanId] =
        typeof opts.measureBytes === 'function' ? await opts.measureBytes(orphanId) : 0;
    }
    // SNOW-749: and union in the areas on the ACCOUNT, in this same one
    // normalising layer — exactly where SNOW-612's orphans already join,
    // and for the same reason: a second reader somewhere else would be free
    // to disagree with this one about what exists.
    //
    // `accountAreas()` resolves `[]` for an anonymous visitor, a flag-off
    // page, an offline device and any failure alike, so this never waits on
    // a network it cannot reach and never turns a read of local storage
    // into a rejection. With `[]` the reconciliation output is what it was
    // before this ticket, which is the path every existing caller takes.
    var accountAreas = window.pwaDownloadsSync
      ? await window.pwaDownloadsSync.accountAreas()
      : [];
    return manage.reconcileAreas(areas, storedIds, bytesById, accountAreas);
  }

  window.pwaBasemapAreas = Object.freeze({
    CUSTOM_AREAS_KEY: CUSTOM_AREAS_KEY,
    LEGACY_CUSTOM_AREA_KEY: LEGACY_CUSTOM_AREA_KEY,
    BASE_LAYERS_KEY: BASE_LAYERS_KEY,
    downloadedAreas: downloadedAreas,
    pinnedBucketAreaIds: pinnedBucketAreaIds,
    readBaseLayers: readBaseLayers,
    readCustomAreas: readCustomAreas,
  });
})();
