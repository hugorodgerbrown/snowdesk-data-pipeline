/*
 * static/js/map_region_download.js — the per-region "Download basemap"
 * control in the season-ribbon header.
 *
 * SNOW-610, step 3: extracted verbatim from map.js. One of the two
 * download controls the SNOW-606 review found duplicating each other —
 * "two download-control IIFEs with five parallel members each". Their
 * shared run had already been lifted into `basemap_download_runner.js`
 * (SNOW-611); putting the two callers in separate files is what makes any
 * remaining parallel member visible as one, rather than as two paragraphs
 * 600 lines apart in the same file.
 *
 * The other caller is static/js/map_custom_download.js. Everything both
 * need — `runPinnedDownload`, the pinned-cache probes, the byte budget,
 * the failure toasts, the progress grid — lives in
 * static/js/map_basemap_downloads.js, which both load after.
 *
 * LOAD ORDER: this IIFE runs at parse time and binds to DOM that
 * _season_ribbon.html renders, so it loads AFTER map.js — preserving the
 * execution order it had when it was the sixth IIFE in that file.
 */

// SNOW-521 final shape: a single "Download basemap" control for the
// focused MICRO region — replaces the viewport-anchored cacheNowInit
// (SNOW-492/493). An earlier iteration of this rework also had
// major/minor crumb icons; they were dropped because their own
// shallower detail floors made the sizes non-monotonic with containment
// (an L1 region could read smaller than an L2 it contains), which read
// as a bug.
//
// The control has moved twice since. SNOW-521 moved it out of the
// #region-readout chip into the bottom-right stack, beside the layers
// pill — the layers menu is the cache-state dashboard and this is the
// other control that writes to that cache. SNOW-522 moved it BACK into
// the #region-readout chip (public/partials/_season_ribbon.html), and
// handed the bottom-right slot to a second, distinct control — a
// user-framed custom-area download (mapCustomDownloadControlInit,
// below) — that this one does not need to know about; this IIFE finds
// #map-download-control by id wherever it currently renders and is
// otherwise unchanged by either move.
//
// Data source — no client-side tile enumeration. Region tile coverage
// is precomputed server-side (regions/services/basemap_tiles.py) and
// already sits on FEATURE_BY_REGION_ID[regionId].properties.download
// once regions.geojson has loaded (the same payload the choropleth
// itself needs), so showing the size is a pure in-memory lookup — no
// extra fetch until the user actually clicks Download.
//
// Show/size — the control becomes actionable whenever a region is focused
// (snowdesk:region-selected moves it from 'no-region' to an actionable
// state), independent of which overlay tiers (L1/L2) are toggled on.
// SNOW-642: it is always VISIBLE, holding the inert 'no-region' state until
// then. It used to be hidden until focus by the same CSS sibling rule as its
// neighbour, the since-removed view-bulletin roundel, which
// meant those two and the readout all disappeared together and the ribbon
// header collapsed to an empty row. 'no-region' also covers the rarer case
// of a focused region with no computed download summary
// (properties.download is null).
//
// State (no-region/idle/busy/done/disabled/offline/other-basemap/signin,
// data-download-state) — idle/done/other-basemap are
// derived from a real pinned-cache probe (every per-area bucket, unioned
// — SNOW-586) every time the icon is (re)shown, never a stored flag (the
// "layers menu is a live cache-state dashboard" invariant — see
// docs/offline-map.md); disabled is the
// server-flagged over_ceiling backstop. That probe needs the active
// basemap's tile template, which isn't resolvable while the style is
// still settling (the boot case) — so a probe that can't tell yet is
// re-run on the next MapLibre 'idle' rather than left reading idle
// (see _retryWhenStyleSettles).
//
// SNOW-645: 'other-basemap' — the region IS downloaded, just for a
// DIFFERENT basemap than the one on screen. Before this, switching
// basemap silently reverted an already-downloaded region's roundel to
// plain 'idle', indistinguishable from never having downloaded it at all
// — nothing was actually lost (the tiles and record both survive the
// switch untouched; SNOW-632 only evicts on a same-region RE-download,
// never on a basemap change alone), but the control gave no way to tell
// that from data loss. `_probeDone` now reads the stored record's own
// `template`/`basemapKey` to tell the two apart, and only claims
// 'other-basemap' once it has verified the OTHER basemap's tiles are
// still actually on disk (a stale, evicted record falls through to plain
// 'idle', same as never downloaded). It is actionable, like 'idle': a tap
// downloads the region under the ACTIVE basemap, and the existing
// `beforeWarm` eviction (below) already handles the mismatched bucket.
//
// SNOW-749: 'signin' — the visitor is signed out, so no download can
// START. It replaces every ACTIONABLE state
// ('idle', and 'other-basemap' with its "download it for this basemap too"
// invitation) and no other: 'done' still paints its green circle, because
// reading a region already on this device is not gated and never will be —
// working with no signal is the whole point of having downloaded it. The
// control stays visible and tappable, and the tap goes to sign-in.
//
// Click (idle/error/other-basemap) — fetches the region's full blob (incl. z tile
// ranges) from /api/region-basemap-tiles/, assembles the URL list
// (rangesToTileURLs + same-origin data feeds + active basemap style +
// sprites — mirrors SNOW-492/493's assembly, minus tile enumeration) and
// hands it to the SW's warm-cache handler with `pinned: true`, updating
// the roundel's live fill from onProgress. On completion the icon
// itself carries the outcome (green offline circle on a clean success,
// idle otherwise) — no toast.
(function mapDownloadControlInit() {
  const btn = document.getElementById('map-download-control');
  if (!btn) return;

  const ribbonEl = document.getElementById('season-ribbon');

  // SNOW-749: the sign-in gate's configuration, read off
  // #map-custom-download-control — the downloads roundel in the
  // bottom-right stack, NOT this control's own button. The two controls
  // start the same kind of work under the same gate, so one element
  // carries the wiring for both rather than the same six attributes being
  // rendered twice; both partials live inside
  // public/partials/_map_embed.html, so it is on the page whenever this
  // button is. A page missing it leaves every constant below falsy, which
  // is the ungated pre-SNOW-749 behaviour — the safe direction for a
  // rollout gate that has not been wired up.
  const DOWNLOADS_CONFIG_EL = document.getElementById('map-custom-download-control');
  const DOWNLOADS_CONFIG = DOWNLOADS_CONFIG_EL ? DOWNLOADS_CONFIG_EL.dataset : {};
  // Authentication is the whole gate. SNOW-749 briefly paired this with a
  // `downloadsSync` rollout-flag attribute, so the client could tell "the
  // gate is switched off" from "this visitor passes it"; the flag was
  // dropped before merge on query cost, and with it the only state that
  // made those two distinguishable.
  const DOWNLOADS_ELIGIBLE = DOWNLOADS_CONFIG.downloadsEligible === 'true';
  const DOWNLOADS_SIGNIN_URL = DOWNLOADS_CONFIG.signinUrl || '';
  // A page with no downloads surface leaves this false, which reads as
  // eligible and applies no gate. That is the safe direction: the gate is
  // a product decision belonging to the map, and a page that does not
  // render the map has no download for it to stop.
  const NEEDS_SIGNIN = !!DOWNLOADS_CONFIG_EL && !DOWNLOADS_ELIGIBLE;

  /**
   * SNOW-749: the state to paint where the control would otherwise be
   * ACTIONABLE — 'signin' for a gated, signed-out visitor, and the state
   * itself for everybody else.
   *
   * Only the actionable states are substituted. 'done', 'no-region',
   * 'disabled' and 'offline' pass through untouched, because reading what
   * is already downloaded is never gated: a signed-out user on a device
   * holding this region's tiles must still see the green circle that says
   * so. The gate is on STARTING a download, and nothing else.
   *
   * @param {string} state The state the probe arrived at.
   * @returns {string}
   */
  function gateState(state) {
    return NEEDS_SIGNIN ? 'signin' : state;
  }

  /**
   * Send the visitor to sign in.
   *
   * A plain navigation rather than a modal or an in-place panel: this
   * control lives in the ribbon header with no surface of its own to put
   * a CTA in, and the sign-in page returns the user to where they were.
   * With no URL configured this does nothing, which leaves the tap inert
   * rather than sending the visitor somewhere wrong.
   *
   * @returns {void}
   */
  function goToSignIn() {
    if (DOWNLOADS_SIGNIN_URL) window.location.assign(DOWNLOADS_SIGNIN_URL);
  }

  // SNOW-570: the regions the user has downloaded, in meta:app under
  // 'basemap.regions' as [{region_id, band, z, savedAt}] — SNOW-632 added
  // `template`, SNOW-645 `basemapKey`.
  //
  // SNOW-583 repurposes this record. It used to carry the region's `bbox`
  // (derived from its boundary geometry) so both the "Downloaded areas"
  // overlay and this control's own done-probe could recompute the
  // download's tile set client-side, cheaply, with no per-region fetch.
  // That recomputation assumed a region's download WAS its bbox rectangle
  // — true before this ticket, false after: a region's tiles are now
  // CLIPPED to its real boundary server-side
  // (apps.regions.services.basemap_tiles.build_region_blob), and the clip
  // depends on the boundary polygon in a way `bbox` alone can't
  // reconstruct. So the record now carries the run's actual `z` (the
  // blob's own tile ranges, whichever shape — rectangle or clipped row
  // spans) instead of `bbox`, and `_probeDone` below reads it back
  // directly rather than recomputing anything. SNOW-587 removed the
  // "Downloaded areas" overlay's per-region ring, the other consumer of
  // the old bbox shape — this record's only reader now is this control's
  // own done-probe.
  //
  // This is the record half only — it says the user asked for this region,
  // never that the tiles are still there. The probe still checks real
  // cache contents before claiming `done`, so an evicted download reads
  // `idle` again. That is exactly the split the custom area has always
  // had, where 'basemap.customAreas' records WHERE each frame was and the
  // done state is probed; the region download simply never had the first
  // half.
  const DOWNLOADED_REGIONS_KEY = 'basemap.regions';

  /**
   * SNOW-748: true when the app is actually using the network — the interface
   * is up AND no offline mode is in force.
   *
   * Reading ``navigator.onLine`` alone is what let this control offer (and
   * start) a download while the header symbol said the app was offline: the
   * user forces that mode precisely when they have a connection and do not
   * want it spent, so ``onLine`` stays true throughout. ``pwa_offline.js``
   * owns the answer and broadcasts a change as
   * ``snowdesk:connectivity-changed``, which is what re-renders this control.
   *
   * ``navigator.onLine`` remains the fallback for a page where that module
   * has not run — the same value this control read before.
   *
   * @returns {boolean}
   */
  function networkInUse() {
    const connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
  }

  /**
   * Record `regionId` as downloaded, replacing any earlier entry for it.
   *
   * Best-effort throughout: this runs inside a download's finish handler,
   * where a failed IndexedDB write must never surface as an error. The
   * cost of losing it is a missing record, not a wrong one — a fallback
   * fetch in `_probeDone` below still answers correctly, just over the
   * network instead of from IndexedDB.
   *
   * SNOW-632: `bytes` is this run's OWN reported total — from
   * `_warmCache`'s ``warm-cache-done`` reply — recorded outright, never
   * accumulated onto the previous record and never re-measured from the
   * bucket. A same-bbox, same-basemap RETRY re-fetches identical URLs into
   * the same bucket (`cache.put` OVERWRITES each key rather than adding to
   * it, so the bucket doesn't grow), so replacing the record with this
   * run's own figure already lands on the right answer without reading
   * the bucket at all. Re-measuring the bucket after every run (an earlier
   * version of this fix) looked more exact, but a live tile response
   * carries no `Content-Length` under the gzip encoding every browser
   * requests, so it measured 0 in production and silently fell back to
   * this same figure anyway — see
   * docs/decisions/per-area-pinned-basemap-caches.md for the curl
   * evidence.
   *
   * A basemap SWITCH at the same region used to need accumulation,
   * because the bucket is keyed on region id alone and a switch adds
   * genuinely new tiles (different URLs, different origin) to it — but
   * that arithmetic couldn't tell a switch apart from a retry, doubling
   * the recorded total on every repeat. `mapDownloadControlInit`'s
   * `handleClick` now sidesteps the ambiguity instead of resolving it
   * numerically: its `beforeWarm` deletes the bucket outright whenever the
   * `template` this run is about to fetch differs from the one recorded
   * for it, so by the time this function runs the bucket always holds
   * exactly one basemap's tiles and this run's own total is the bucket's
   * whole total.
   *
   * @param {string} regionId
   * @param {string} template The tile URL template this run fetched
   *   (`basemap_download_runner.js`'s `run` resolves it once and hands it
   *   to both `beforeWarm` and `finish`'s `extras`) — stored so a LATER
   *   run can tell whether the bucket still matches the active basemap.
   * @param {Object | null} z The downloaded blob's own tile ranges
   *   (`blob.z` — rectangle or clipped row spans), or null when the run's
   *   blob carried none — in which case nothing is recorded, since an
   *   entry with no `z` could never be verified against the cache.
   * @param {number[]} band The zoom band the run actually fetched.
   * @param {number} bytes This run's own on-disk size, from `_warmCache`'s
   *   ``warm-cache-done`` reply.
   * @param {string | null} basemapKey SNOW-645: the picker key captured
   *   alongside `template` at run start (`basemap_download_runner.js`'s
   *   `run`), display-only — stored so the Manage downloads sheet and this
   *   control's own roundel can show which basemap the region was
   *   downloaded under. Null on an unresolved picker; never used for the
   *   template-eviction decision above, which stays `template`-only.
   * @returns {Promise<void>}
   */
  async function _recordRegionDownload(regionId, template, z, band, bytes, basemapKey) {
    if (!z || !window.pwaDb) return;
    try {
      const row = await window.pwaDb.get('meta:app', DOWNLOADED_REGIONS_KEY);
      const existing = Array.isArray(row && row.value) ? row.value : [];
      const next = existing.filter((entry) => entry && entry.region_id !== regionId);
      const feature = FEATURE_BY_REGION_ID[regionId];
      const name = (feature && feature.properties && feature.properties.name) || regionId;
      next.push({
        region_id: regionId,
        band: band,
        z: z,
        name: name,
        template: template,
        basemapKey: basemapKey || null,
        bytes: Number(bytes) || 0,
        savedAt: new Date().toISOString(),
      });
      await window.pwaDb.put('meta:app', { key: DOWNLOADED_REGIONS_KEY, value: next });
      // SNOW-749: and record the DEFINITION against the account, from the
      // same moment the device record is written so the two cannot
      // disagree about what was downloaded. Enqueued through the mutation
      // queue, so a region downloaded with no signal is recorded when the
      // device surfaces; a no-op for a signed-out visitor.
      //
      // No bbox: a region's tiles are computed server-side from its real
      // boundary (SNOW-583 clipped them to it), so a box would be a
      // second, coarser answer to a question already answered — the region
      // id is the whole definition.
      // The bucket id, minted by the core's own `areaIdForRegion` — the
      // `region-<id>` format is deliberately never assembled by hand
      // anywhere outside that function, and it is the key the account row
      // is stored under.
      const core = self.pwaBasemapDownloadCore;
      await window.pwaDownloadsSync?.push({
        areaId: core ? core.areaIdForRegion(regionId) : '',
        regionId: regionId,
        basemapKey: basemapKey,
        name: name,
      });
    } catch (err) {
      // Still non-fatal — see the docstring — but no longer silent
      // (SNOW-612). This is the write whose absence leaves a completed
      // download with a pinned bucket and no record, and the bucket then
      // reads as an orphan: the reconciliation above makes that visible
      // and deletable, and this says why it happened.
      console.warn('basemap download record write failed', err);
      window.pwaTelemetry?.emit('map.basemap.record_write_failed', {
        region_id: regionId,
      });
    }
  }

  /**
   * The stored ``basemap.regions`` record for `regionId`, or null.
   *
   * A PRE-SNOW-583 record carries `bbox` and no `z` — the shape this
   * region's own download last wrote before this ticket shipped. Treated
   * as "no record" rather than read literally: its `bbox` is the old
   * bounding-box rectangle, not the clipped tile set the server would
   * compute today, so trusting it would claim tiles the run never
   * fetched. `_probeDone` falls back to a fresh server fetch in that case
   * — the same path a region with no record at all takes.
   *
   * Best-effort: a failed read is "no record", never an error.
   *
   * @param {string} regionId
   * @returns {Promise<{region_id: string, band: number[], z: Object,
   *   template?: string, basemapKey?: string | null, savedAt: string} |
   *   null>} `template` is absent on a record written before SNOW-632 —
   *   callers deciding whether to evict the region's bucket on a template
   *   mismatch treat that absence as "unknown, so different" (see
   *   `handleClick`'s `beforeWarm`). `basemapKey` is absent (or `null`) on
   *   a record written before SNOW-645 — `_probeDone` reads that the same
   *   way, as "another basemap, unnamed" rather than a wrong one.
   */
  async function _storedRegionRecord(regionId) {
    try {
      const row = await window.pwaDb?.get('meta:app', DOWNLOADED_REGIONS_KEY);
      const value = Array.isArray(row && row.value) ? row.value : [];
      const record = value.find((entry) => entry && entry.region_id === regionId);
      return record && record.z ? record : null;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Backfill absent `template` / `basemapKey` on `regionId`'s stored
   * record, from values the caller has just PROVEN by probing real cache
   * contents. Never overwrites a value that is already there.
   *
   * A record written before SNOW-632 (no `template`) or SNOW-645 (no
   * `basemapKey`) is missing exactly the two fields every other surface
   * keys off, and nothing rewrites it: the record is only ever written by
   * a fresh download, so a region downloaded once and never re-downloaded
   * stays incomplete for the life of the install. That is what made the
   * roundel and the map disagree — see `basemapDownloadedTemplates()`
   * (map_basemap_downloads.js) for the other half.
   *
   * Called only from `_probeDone`'s same-template branch, and only when
   * the probe came back `done`: at that moment the region's whole tile set
   * has been found in the pinned cache UNDER THE ACTIVE TEMPLATE, so
   * writing the active template and key records something verified, not
   * assumed. A `false` probe proves nothing about which basemap the record
   * belongs to and heals nothing.
   *
   * Best-effort and non-fatal, like `_recordRegionDownload`: a failed heal
   * leaves the record as it was and the next probe tries again.
   *
   * @param {string} regionId
   * @param {{template: string, basemapKey: string | null}} fields
   * @returns {Promise<void>}
   */
  async function _healRegionRecord(regionId, fields) {
    if (!window.pwaDb) return;
    try {
      const row = await window.pwaDb.get('meta:app', DOWNLOADED_REGIONS_KEY);
      const value = Array.isArray(row && row.value) ? row.value : [];
      let changed = false;
      for (const entry of value) {
        if (!entry || entry.region_id !== regionId) continue;
        for (const [name, next] of Object.entries(fields)) {
          if (!next || entry[name]) continue;
          entry[name] = next;
          changed = true;
        }
      }
      if (!changed) return;
      await window.pwaDb.put('meta:app', { key: DOWNLOADED_REGIONS_KEY, value: value });
    } catch (err) {
      console.warn('basemap download record heal failed', err);
    }
  }

  /**
   * The basemap key some OTHER recorded area already associates with
   * `template`, or `''` when nothing on record names it.
   *
   * A pre-SNOW-645 record carries a template but no key, so the ring it
   * drives has no identity colour to take and falls back to the "basemap
   * unknown" green — which reads as a different, wronger thing than the
   * rust/blue/teal the same download shows once it IS named. Any other
   * record fetched under that same template answers the question, and
   * `basemapDownloadedTemplates()` already collects exactly those
   * (template, key) pairs and already prefers a named key over an unnamed
   * one for the same template. No new storage, and no guessing from URL
   * shape: a template on record under a named basemap IS that basemap.
   *
   * @param {string} template
   * @returns {Promise<string>} `''` when unresolvable — `setState` reads
   *   that as "another basemap, unnamed", the same as before.
   */
  async function _basemapKeyForTemplate(template) {
    try {
      const pairs = await basemapDownloadedTemplates();
      const named = pairs.find((pair) => pair.template === template && pair.basemapKey);
      return named ? named.basemapKey : '';
    } catch (_e) {
      return '';
    }
  }

  // Memoised full-blob fetches for `_probeDone`'s fallback path (no local
  // record, or a pre-SNOW-583 one) — keyed by region id, successes and
  // in-flight promises only. A region's blob never changes once computed
  // (region geometry is static reference data), so caching a settled
  // fetch for the rest of the session is exact, not just an optimisation.
  // Failures are deliberately NOT cached: a network hiccup or a
  // still-offline session must not poison every later probe for the
  // region — the next call retries the fetch instead of repeating the
  // same failure from memory.
  const _regionBlobCache = new Map();

  /**
   * The region's full ``basemap_download`` blob, fetched once and
   * memoised for the rest of the session.
   *
   * @param {string} regionId
   * @returns {Promise<Object>} Rejects on a network/HTTP failure — the
   *   caller decides what "can't tell" means for its own state machine.
   */
  function _fetchRegionBlob(regionId) {
    const cached = _regionBlobCache.get(regionId);
    if (cached) return cached;
    // Cache the in-flight promise immediately, so two probes racing the
    // same never-yet-fetched region share one request rather than firing
    // two. The .catch removes it again on failure — so the next call
    // starts a fresh fetch instead of replaying the rejection — while
    // still re-throwing, so THIS call's caller sees the failure.
    const promise = fetch('/api/region-basemap-tiles/?id=' + encodeURIComponent(regionId))
      .then((response) => {
        if (!response.ok) throw new Error(`region-basemap-tiles ${response.status}`);
        return response.json();
      })
      .catch((err) => {
        _regionBlobCache.delete(regionId);
        throw err;
      });
    _regionBlobCache.set(regionId, promise);
    return promise;
  }

  // { regionId, summary } for the currently-focused region, or null
  // when it has no computed data. `summary` is the small {count, mb,
  // over_ceiling, centre_tile} shape carried on regions.geojson's
  // properties.download.
  let regionData = null;

  // SNOW-656: starts EMPTY, like the ribbon's own focus. This used to seed
  // from ``data-default-region-id``, which armed the download roundel for
  // Martigny-Verbier on every first paint — offering to download a region
  // the visitor had not chosen and could not see was chosen. `setState`
  // renders the 'no-region' variant until a region-selected event arrives,
  // which is the honest reading of "nothing is focused yet".
  let currentRegionId = null;

  /**
   * True when EVERY tile of `data`'s region is present in the pinned
   * cache — "is this region actually available offline?".
   *
   * SNOW-583: a region's tile set is now CLIPPED to its real boundary
   * server-side (`build_region_blob`), which a client only has an
   * approximation of (`FEATURE_BY_REGION_ID`'s geometry, unbuffered) — so
   * this can no longer recompute the tile set from the region's own
   * boundary the way it (and the "Downloaded areas" overlay) briefly did
   * under SNOW-570. It instead checks the ACTUAL tile set: the run's own
   * stored `basemap.regions` record when there is one (works fully
   * offline — no network involved), falling back to a fresh fetch of the
   * region's blob when there is none (a region downloaded in an earlier
   * session before this record shape existed, or one this control has
   * simply never focused before). Either way `blobFullyCached` checks the
   * real blob's tiles against the real cache, never a recomputed
   * approximation of them.
   *
   * This used to be a centre-tile probe: one `cache.match` on the tile at
   * the region's centre, taken as a witness that the region's own
   * download had completed. That reasoning only holds for a region the
   * user downloaded AS A REGION, and the roundel does not get to assume
   * that — it probes whatever region is selected. Both download shapes
   * write to one pinned cache over the same band with the same URL
   * template, so a neighbouring region's download, or a custom area that
   * merely overlapped, caches this region's centre tile without covering
   * it. The roundel then painted `done` for a region holding a handful of
   * tiles, and because `handleClick` only acts on `idle`/`error`, the
   * region could no longer be downloaded at all. Full coverage is the
   * honest question, and — since the tile set now comes from a real blob
   * rather than a recomputed rectangle — it is exact rather than a bbox
   * approximation.
   *
   * Returns `null` for "can't tell yet": the active basemap's tile
   * template isn't resolvable (see `_retryWhenStyleSettles`), or there is
   * no stored record AND the fallback fetch failed (typically: offline,
   * with nothing recorded for this region yet — `renderControl` reads
   * `networkInUse()` to choose `idle` vs `offline` in that case). Both
   * are deliberately distinct from `false` ("looked, not there").
   *
   * SNOW-645: also distinguishes "never downloaded" from "downloaded, but
   * under a DIFFERENT basemap" — the record's own `template`/`basemapKey`
   * (both added by SNOW-632/SNOW-645) make that answerable. Returned as
   * `otherBasemapKey` alongside `done` rather than as a second probe: this
   * function already awaits `pinnedBasemapCacheURLs()`, a walk of
   * potentially several thousand cache entries, and a second independent
   * probe would walk it twice for one render. `otherBasemapKey` is:
   *   - `null` when not in that state — no record, no `record.template`
   *     (a pre-SNOW-632 record — "unknown, so treat as the active
   *     basemap", the same direction `handleClick`'s `beforeWarm` takes),
   *     `record.template` matching the active one, or a stale record whose
   *     bucket has since been evicted (never promise tiles that aren't
   *     there — falls through to `idle` exactly as "never downloaded"
   *     does);
   *   - the record's own `basemapKey` when it IS that state; for a
   *     pre-SNOW-645 record with no key, whatever `_basemapKeyForTemplate`
   *     can name from another record sharing the same template, and only
   *     `''` ("another basemap", unnamed) when nothing on record names it.
   *     The resolved key is used for DISPLAY only and never written back —
   *     same rule `orphanBasemapKey` (map_basemap_downloads.js) sets for
   *     an inferred basemap. Only `_healRegionRecord` writes, and only
   *     from a value the cache itself has just proven.
   *
   * @param {{regionId: string, summary: Object}} data
   * @returns {Promise<{done: boolean, otherBasemapKey: string | null} | null>}
   */
  async function _probeDone(data) {
    const core = self.pwaBasemapDownloadCore;
    const template = activeBasemapTileTemplate(MAP);
    if (!core || !template) return null;
    // SNOW-586: the cached set is unioned across every per-area pinned
    // bucket, not read from one shared cache. That is the ONLY thing this
    // ticket changes here — SNOW-583's stored-`z` strategy below is kept
    // whole, because it asks about the tile ranges the run actually
    // fetched. Deriving them from the region's bbox instead (as this
    // probe did before SNOW-583 clipped downloads to the region plus a
    // tile of margin) would demand tiles a clipped download never wrote,
    // so every region would read as permanently un-downloaded.
    const cached = await pinnedBasemapCacheURLs();

    const stored = await _storedRegionRecord(data.regionId);
    if (stored) {
      if (!stored.template || stored.template === template) {
        const done = core.blobFullyCached(template, { z: stored.z }, cached);
        // The tile set is verified present under the ACTIVE template, so
        // an incomplete record can be completed from what was just proven
        // rather than left for the overlay to trip over. Awaited, not
        // fire-and-forget: the caller's next paint (and the overlay
        // refresh that follows a render) should read the healed record,
        // not race the write.
        if (done) {
          await _healRegionRecord(data.regionId, {
            template: template,
            basemapKey: activeBasemapKey(),
          });
        }
        return { done: done, otherBasemapKey: null };
      }
      // SNOW-645: a record for a DIFFERENT basemap. Verify its tiles are
      // still actually on disk before promising them back — an evicted
      // bucket makes this a stale record, which must read exactly like
      // "never downloaded" rather than a lie the user can tap into.
      const otherStillCached = core.blobFullyCached(
        stored.template,
        { z: stored.z },
        cached,
      );
      if (!otherStillCached) return { done: false, otherBasemapKey: null };
      // A keyless record's basemap can still be named, from any other
      // record sharing its template — so the ring takes that basemap's
      // identity colour rather than the "unknown" green, which is what
      // made one download read rust on its own basemap and green from
      // another. Only consulted when the record itself is silent.
      const key = stored.basemapKey || (await _basemapKeyForTemplate(stored.template));
      return { done: false, otherBasemapKey: key || '' };
    }

    try {
      const blob = await _fetchRegionBlob(data.regionId);
      return { done: core.blobFullyCached(template, blob, cached), otherBasemapKey: null };
    } catch (_e) {
      return null;
    }
  }

  /**
   * Paint `state` onto the download icon: data-download-state, the
   * identity-colour attribute, the busy fill percentage, and an
   * aria-label/title carrying the region's size.
   *
   * @param {string} state - 'no-region' | 'idle' | 'busy' | 'done' |
   *   'error' | 'disabled' | 'offline' | 'other-basemap' | (SNOW-749)
   *   'signin'.
   * @param {number} mb
   * @param {number} [pct] - Only meaningful for state 'busy'.
   * @param {string} [basemapKeyOverride] - SNOW-645: overrides the
   *   colour `data-basemap-key` would otherwise take from
   *   `activeBasemapKey()`. Only 'other-basemap' passes this — its whole
   *   point is showing the OTHER basemap's colour, the one that actually
   *   holds the tiles, not the one currently on screen. An explicit
   *   override rather than `setState` inferring it from `state`, so state
   *   and "whose colour" can never silently drift apart. `''` (a
   *   pre-SNOW-645 record with no stored key) is deliberately falsy here,
   *   same as `activeBasemapKey()` returning `null` — both clear the
   *   attribute rather than write an empty one.
   * @returns {void}
   */
  function setState(state, mb, pct, basemapKeyOverride) {
    btn.dataset.downloadState = state;
    // SNOW-645: paint the roundel with the ACTIVE basemap's identity colour
    // (map.css's data-basemap-key override) by default — `_probeDone`
    // builds its cached-tiles check from the active template, so 'done'
    // already means "downloaded under the basemap showing now", and the
    // two can never disagree. 'other-basemap' is the one exception: see
    // `basemapKeyOverride` above.
    const basemapKey =
      basemapKeyOverride !== undefined ? basemapKeyOverride : activeBasemapKey();
    if (basemapKey) {
      btn.dataset.basemapKey = basemapKey;
    } else {
      delete btn.dataset.basemapKey;
    }
    // Non-runnable states are announced as disabled rather than removed, so
    // the control keeps its place in the stack (see renderControl). 'idle',
    // (SNOW-568) 'error' and (SNOW-645) 'other-basemap' are the actionable
    // states — handleClick returns immediately for every other one,
    // including 'busy' (a run is already going) and 'done' (an
    // informational success state), so those are announced as disabled too.
    //
    // SNOW-749: 'signin' is actionable too — the tap navigates to the
    // sign-in page. Announcing it as disabled would be the same mistake as
    // hiding the control: the visitor CAN act on it, they just get a
    // different outcome than a signed-in one does.
    btn.setAttribute(
      'aria-disabled',
      state === 'idle' ||
        state === 'error' ||
        state === 'other-basemap' ||
        state === 'signin'
        ? 'false'
        : 'true',
    );
    // Busy progress renders as a bottom-up fill of the roundel (map.css),
    // driven by --download-progress rather than a numeric readout.
    if (state === 'busy') {
      btn.style.setProperty('--download-progress', `${pct || 0}%`);
    } else {
      btn.style.removeProperty('--download-progress');
    }
    // SNOW-620/SNOW-645: every label below is server-translated into
    // map-strings-template (_map_embed.html) and read back via MAP_STRINGS
    // (map_state.js) — makemessages cannot see a JS string literal, so
    // these used to ship as English to every locale regardless of the
    // visitor's own. See MAP_STRINGS' own module-header note, and
    // bin/i18n-lint's widened check for why nothing caught it here sooner.
    //
    // The control is permanently in the bottom-right stack rather than
    // appearing beside the region name, so it can be read with nothing
    // selected — and it no longer sits next to the name that told the user
    // which region it meant. Every label therefore has to say so itself.
    //
    // 'no-region' covers two distinct causes that the old hidden-icon
    // behaviour let us conflate: nothing is focused, or the focused region
    // has no precomputed download summary (properties.download is null —
    // compute_basemap_download hasn't run for it). The control is always
    // on screen now, so a single label would be wrong in one of the two
    // cases — hence two distinct strings, not one templated by a boolean.
    //
    // 'other-basemap': named when the OTHER basemap's picker label is
    // known (a real basemapKey), unnamed for a pre-SNOW-645 record (an
    // empty-string basemapKey, read off `_probeDone`) — never interpolates
    // an empty name into the named string.
    const otherBasemapName = state === 'other-basemap' ? basemapLabel(basemapKey) : '';
    const text = {
      'no-region': currentRegionId
        ? MAP_STRINGS['download-no-region-unavailable']
        : MAP_STRINGS['download-no-region-select'],
      idle: self.pwaStrings.interpolate(MAP_STRINGS['download-idle'], { mb: mb }),
      busy: self.pwaStrings.interpolate(MAP_STRINGS['download-busy'], { pct: `${pct || 0}%` }),
      done: MAP_STRINGS['download-done'],
      // SNOW-568: the toast carries the reason; the roundel just has to
      // say the run failed and is retryable.
      error: MAP_STRINGS['download-error'],
      disabled: MAP_STRINGS['download-disabled'],
      // Offline-integrity: no downloading of layers while offline.
      offline: MAP_STRINGS['download-offline'],
      'other-basemap': otherBasemapName
        ? self.pwaStrings.interpolate(MAP_STRINGS['download-other-basemap'], {
            basemap: otherBasemapName,
          })
        : MAP_STRINGS['download-other-basemap-unnamed'],
      // SNOW-749: carries no size. The figure answers "is this worth
      // downloading", which is not the question in front of a visitor who
      // cannot start a download at all yet.
      signin: MAP_STRINGS['download-signin'],
    }[state];
    btn.setAttribute('aria-label', text);
    btn.title = text;
  }

  // SNOW-611: the shared retry-when-the-style-settles callback — see
  // `makeStyleSettleRetry`. SNOW-522's custom-area control carried a
  // byte-identical copy of this; SNOW-634 deleted its own use of it —
  // that control's "done" no longer depends on the active basemap's tile
  // template at all (see mapCustomDownloadControlInit's `_renderControl`).
  const _retryWhenStyleSettles = makeStyleSettleRetry(() => renderControl());

  /**
   * (Re)probe the control against the current regionData. A stale async
   * resolution (regionData changed, or a run started, while the probe was
   * in flight) is discarded rather than clobbering a newer state.
   *
   * With no region focused this used to set btn.hidden, which made the
   * bottom-right stack grow and shrink under the user as regions were
   * selected and deselected — and, once the control moved into that stack,
   * would have read as the feature disappearing rather than being
   * unavailable. It now paints the inert 'no-region' state instead and the
   * control keeps its slot.
   *
   * @returns {Promise<void>}
   */
  async function _renderControl() {
    const data = regionData;
    if (!data) {
      setState('no-region');
      return;
    }
    if (btn.dataset.downloadState === 'busy') return;
    if (data.summary.over_ceiling) {
      setState('disabled', data.summary.mb);
      return;
    }
    // SNOW-583: `_probeDone` can now be a network round trip (its fallback
    // fetch, when there's no stored record for this region), where it used
    // to be a single synchronous cache read wrapped in one await. Without
    // this, a slow probe would leave the PREVIOUSLY-focused region's
    // `done` painted on screen for the whole of that round trip — this
    // region hasn't been checked yet, so it must not borrow the last
    // region's answer.
    setState(networkInUse() ? gateState('idle') : 'offline', data.summary.mb);
    const probe = await _probeDone(data);
    if (regionData !== data || btn.dataset.downloadState === 'busy') return;
    // "Can't tell yet" (null): paint the actionable idle state so the icon
    // still carries this region's size, but come back once the style has
    // settled — the region may well already be downloaded.
    if (probe === null) {
      setState(networkInUse() ? gateState('idle') : 'offline', data.summary.mb);
      _retryWhenStyleSettles();
      return;
    }
    const { done, otherBasemapKey } = probe;
    // Offline-integrity: a region already downloaded (done) still reads as
    // the green offline circle; one that isn't can't be fetched now, so it
    // shows the offline-disabled state instead of an actionable idle —
    // including SNOW-645's 'other-basemap', which is just as much "start a
    // new download" as 'idle' is, and so just as unavailable offline.
    if (!networkInUse() && !done) {
      setState('offline', data.summary.mb);
      return;
    }
    if (done) {
      setState('done', data.summary.mb);
      return;
    }
    if (otherBasemapKey !== null) {
      // SNOW-749: 'other-basemap' is an INVITATION to start a second
      // download ("tap to download it for this basemap too"), so a gated
      // signed-out visitor gets 'signin' instead — the label would
      // otherwise promise an action the tap cannot perform. What is
      // already on disk for the other basemap is unaffected and still
      // reads offline; only the invitation goes.
      if (NEEDS_SIGNIN) {
        setState('signin', data.summary.mb);
        return;
      }
      // SNOW-645: the whole point of this state is showing the OTHER
      // basemap's colour, not the active one — setState's 4th argument
      // overrides its usual activeBasemapKey() inference.
      setState('other-basemap', data.summary.mb, undefined, otherBasemapKey);
      return;
    }
    setState(gateState('idle'), data.summary.mb);
  }

  // SNOW-613: overlapping renders coalesce onto one trailing pass — see
  // `coalesceRenders`. Every trigger below calls this, not `_renderControl`.
  const renderControl = coalesceRenders(_renderControl);

  /**
   * Adopt `regionId` as the focused region: pull its download summary
   * straight off the already-loaded
   * FEATURE_BY_REGION_ID[regionId].properties.download (no fetch) and
   * re-render the icon.
   *
   * @param {string | null} regionId
   * @returns {void}
   */
  function applyRegion(regionId) {
    currentRegionId = regionId;
    const feature = regionId ? FEATURE_BY_REGION_ID[regionId] : null;
    const summary = (feature && feature.properties && feature.properties.download) || null;
    regionData = summary ? { regionId: regionId, summary: summary } : null;
    renderControl();
  }

  /**
   * Run the download for the focused region.
   *
   * SNOW-611: the ordered pre-flight, the eviction and the warm-cache
   * dispatch all live in the shared `runPinnedDownload` — this supplies
   * only what is this control's own: how the roundel paints, where the
   * blob comes from, and what a successful run records.
   *
   * @returns {Promise<void>}
   */
  async function handleClick() {
    const data = regionData;
    // SNOW-568: 'error' is retryable, so it starts a run like 'idle'.
    // SNOW-645: so is 'other-basemap' — tapping it downloads the region
    // under the ACTIVE basemap; beforeWarm below already evicts the
    // mismatched bucket (the SAME branch a same-basemap re-download takes
    // when the template it reads has changed), so no new eviction logic
    // is needed for this state.
    const state = btn.dataset.downloadState;
    // SNOW-749: the gate, before anything else. Checked twice on purpose —
    // once on the painted state (the visitor tapped what the label said)
    // and once on NEEDS_SIGNIN itself, which covers a race where a repaint
    // has not landed yet. Neither starts a run; both send the visitor
    // somewhere they can do something about it.
    if (state === 'signin' || NEEDS_SIGNIN) {
      goToSignIn();
      return;
    }
    if (!data || (state !== 'idle' && state !== 'error' && state !== 'other-basemap')) return;
    // Offline-integrity: never start a download offline, even if a race left
    // the icon on 'idle' at the moment of the click. SNOW-748: "offline"
    // includes a mode the user forced while the radio was up — the worker
    // refuses such a run anyway, and a control that dispatches one and paints
    // busy until the refusal comes back is a worse way to say no.
    if (!networkInUse()) {
      setState('offline', data.summary.mb);
      return;
    }

    const core = self.pwaBasemapDownloadCore;
    // `runPinnedDownload` re-reads the core itself and fails the run
    // properly if it is missing; this only needs it for the area id, so a
    // missing core simply yields none and the runner handles the rest.
    const areaId = core ? core.areaIdForRegion(data.regionId) : '';

    await runPinnedDownload({
      areaId: areaId,
      mb: data.summary.mb,
      // This control's roundel carries the region's size in every state,
      // so the shared runner's (state, pct) pair is widened here.
      paint: (nextState, pct) => setState(nextState, data.summary.mb, pct),
      loadBlob: async () => {
        const response = await fetch(
          '/api/region-basemap-tiles/?id=' + encodeURIComponent(data.regionId),
        );
        if (!response.ok) throw new Error(`region-basemap-tiles ${response.status}`);
        return response.json();
      },
      // SNOW-632: a region's pinned bucket is keyed on the region id ALONE
      // (see `_recordRegionDownload`'s docstring), so downloading the same
      // region under a DIFFERENT basemap would otherwise leave the old
      // basemap's tiles sitting in the bucket alongside the new run's —
      // the bucket's real size stops matching what gets recorded, and
      // `planBasemapDownloadBudget` under-charges the area for it. No
      // confirmation needed: this replaces the user's own prior download
      // of the SAME region, not another area — same reasoning as the
      // custom-area control's own `beforeWarm`, which this mirrors.
      // `template` is what the runner is about to build THIS run's URLs
      // from, so the comparison and the fetch can never disagree about
      // which basemap is active.
      //
      // Accepted trade-off: evicting BEFORE the warm means a run that
      // then fails leaves the user with neither the old download nor a
      // new one. Bucket and record go together, so the state stays
      // consistent (empty bucket, no record, roundel on 'error') rather
      // than stale — and the evicted tiles were the previous basemap's,
      // which the done-probe already ignored under the new one, so there
      // was nothing usable to lose.
      beforeWarm: async (_blob, evictAreaId, template) => {
        const previous = await _storedRegionRecord(data.regionId);
        // No usable record (never downloaded, or a pre-SNOW-632 record
        // with no `template`) is treated the same as a mismatch — the
        // safe direction, since it costs one redundant eviction rather
        // than risking a stale bucket read as bigger than it is.
        if (!previous || previous.template !== template) {
          await evictBasemapAreas([evictAreaId]);
        }
      },
      finish: async (result, blob, { core: runCore, progressFill, template, basemapKey }) => {
        // "done" (the green offline circle) requires at least one success
        // and no failures; a partial, vacuous, or absent result must not
        // claim the region is downloaded.
        //
        // SNOW-568: a run that didn't succeed paints 'error' and raises the
        // shared toast, rather than reverting to 'idle' — which was
        // indistinguishable from never having clicked.
        //
        // SNOW-632: `result.cancelled` is checked too, even though this
        // control has no Cancel affordance of its own to trigger it — the
        // shared runner's contract permits a cancelled result from ANY
        // caller (basemap_download_runner.js's `finish` docstring), and a
        // cancelled run's `failed` is always 0, which would otherwise
        // misread as a clean success.
        //
        // SNOW-649: the predicate itself lives in the core, so this control
        // and map_custom_download.js cannot drift apart on what "done"
        // means — see downloadSucceeded's docstring for why each clause is
        // there.
        //
        // SNOW-748: and cancellation is now reachable here. Toggling the
        // header switch into a forced offline mode aborts an in-flight run,
        // so `finish` receives a cancelled result with nobody having pressed
        // anything this control owns. `cancelled` is read into its own local
        // and branched on BEFORE `ok`, matching map_custom_download.js: the
        // user asking a run to stop is neither a success nor a failure, and
        // painting 'error' with the shared toast reported a fault where
        // there was none.
        const cancelled = !!(result && result.cancelled);
        const ok = runCore.downloadSucceeded(result);
        // SNOW-570: record what was downloaded before anything is painted.
        // SNOW-583: records the blob's own `z` (the clipped tile set the run
        // actually fetched) rather than a bbox — `_probeDone` reads this
        // record back directly, with no recomputation, so it is exactly
        // right for whatever shape the blob was.
        if (ok) {
          await _recordRegionDownload(
            data.regionId,
            template,
            blob.z,
            blob.band || runCore.MICRO_BAND,
            result.bytes,
            basemapKey,
          );
        }
        // SNOW-569: await the on-map pulse before flipping the roundel — the
        // two are one gesture, the region finishes filling, pulses, and only
        // then does the icon go green. The control stays 'busy' throughout,
        // which also keeps renderControl from repainting underneath the
        // pulse. A failed run clears the fill without pulsing, so the error
        // state and its toast arrive with no delay.
        await progressFill.finish(ok);
        if (cancelled) {
          // Back to rest, not to error. This can never paint 'done': tiles
          // may well have landed before the worker honoured the cancel, but
          // the region's tile set is incomplete by definition, and the
          // done-probe checks the whole of it. 'offline' is the resting
          // state when the abort came from the account menu's "Offline
          // mode" row — which is the only way to reach this branch today;
          // `renderControl`'s own
          // connectivity listener already ran while this control was still
          // 'busy' (and so early-returned), so the resting paint is this
          // callback's job.
          setState(networkInUse() ? 'idle' : 'offline', data.summary.mb);
        } else if (ok) {
          setState('done', data.summary.mb);
        } else {
          setState('error', data.summary.mb);
          revealBasemapDownloadError(result ? result.reason : null);
        }
        // SNOW-505: the warm-cache run has just warmed the shell + pinned
        // basemap caches (the SW's warm-cache handler awaits its
        // cache.put calls before replying, so this re-probe races
        // nothing). Re-probe every sync dot against real cache state so
        // the layers popover reflects the newly-warmed feeds/tiles.
        window.pwaLayerSyncStatus?.refresh();
        // SNOW-570/SNOW-587: and the cached-tiles overlay, so tiles that
        // just finished downloading appear immediately rather than at the
        // next basemap swap or reload.
        window.pwaDownloadedOverlay?.refresh();
      },
    });
  }

  btn.addEventListener('click', () => handleClick());

  document.addEventListener('snowdesk:region-selected', (e) => {
    applyRegion((e.detail && e.detail.region_id) || null);
  });

  // Per-basemap download state: the "done" probe (_probeDone) keys off the
  // ACTIVE basemap's tile template, so switching basemap changes whether
  // this region reads as downloaded. Re-render on every basemap swap (the
  // main IIFE fires this once the new style's overlays are back) so the icon
  // flips done↔idle to match the basemap you're now on — e.g. download on
  // Standard, switch to Swisstopo, and the icon reverts to "download".
  document.addEventListener('snowdesk:basemap-changed', () => renderControl());

  // Offline-integrity: re-render on every connectivity transition so the
  // icon greys out (offline, not yet downloaded) or becomes actionable
  // again (back online) without needing the region re-selected.
  document.addEventListener('snowdesk:connectivity-changed', () => renderControl());

  // Pick up the homepage's server-rendered default focus once its
  // geojson feature (and download data) has loaded. The initial CH
  // load populates FEATURE_BY_REGION_ID directly in the main IIFE's
  // map.on('load') handler WITHOUT dispatching snowdesk:regions-loaded
  // (that event is SNOW-172-lazy-country-load-only) — MAP_READY_PROMISE
  // resolves right after that handler's install step, by which point
  // FEATURE_BY_REGION_ID is populated, so it's the reliable signal for
  // the initial default-region case. snowdesk:regions-loaded is also
  // listened for as a safety net (a deep-linked/default region in a
  // country loaded lazily later), mirroring seasonRibbonInit's own
  // setHighlight listener for that event.
  MAP_READY_PROMISE.then(() => {
    if (currentRegionId) applyRegion(currentRegionId);
  });
  document.addEventListener('snowdesk:regions-loaded', () => {
    if (currentRegionId) applyRegion(currentRegionId);
  });

  if (currentRegionId) applyRegion(currentRegionId);

  /**
   * SNOW-749: focus `regionId` and start its download.
   *
   * The "Download here" path for a region that exists on the user's
   * ACCOUNT but not on this device — the Manage downloads sheet
   * (map_downloads_manager.js) has an area id and a region id, and this
   * control has everything else. Exposed as a bridge rather than
   * reimplemented there for the same reason `window.pwaBasemapDownloads`
   * exists: a second copy of the pre-flight, the eviction and the
   * recording would be free to drift from this one.
   *
   * Awaits a fresh probe before acting: the caller has just come from a
   * list, so this control is very likely still painted for whatever region
   * was last focused, and `handleClick` reads the painted state to decide
   * whether a run is allowed at all.
   *
   * @param {string} regionId
   * @returns {Promise<boolean>} Whether a run was started. False when the
   *   region is unknown to this page, when the control settled on a state
   *   that cannot run (offline, over the ceiling, already downloaded), or
   *   when the visitor needs to sign in — in which case they have been
   *   sent there.
   */
  async function startRegionDownload(regionId) {
    if (NEEDS_SIGNIN) {
      goToSignIn();
      return false;
    }
    if (!regionId || !FEATURE_BY_REGION_ID[regionId]) return false;
    applyRegion(regionId);
    await renderControl();
    const state = btn.dataset.downloadState;
    if (state !== 'idle' && state !== 'error' && state !== 'other-basemap') return false;
    await handleClick();
    return true;
  }

  window.pwaRegionDownload = Object.freeze({
    start: startRegionDownload,
  });
})();
