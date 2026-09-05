/*
 * static/js/basemap_download_runner.js — the one ordered pinned-basemap
 * download run, shared by both map download controls (SNOW-611).
 *
 * `mapDownloadControlInit` (a focused region) and
 * `mapCustomDownloadControlInit` (a user-framed area) in `static/js/map.js`
 * are two front ends onto one operation, and they ran it twice — roughly
 * 600 and 1,000 lines with five matching members each. Finding D5 in
 * `docs/code-reviews/2026-08-03-js-review.md` has the correspondence.
 *
 * Why the ordering lives here
 * ---------------------------
 * `evictBasemapAreas` destroys ANOTHER area's pinned bucket and its
 * `meta:app` record for good, so it has to be the LAST step before the run
 * — every check that can abort (quota, tile sources, budget ceiling, blob
 * fetch) must have passed by the time the user is asked. A user who
 * answers "yes, evict X" and then watches the run fail is left with
 * neither X nor the download they asked for. SNOW-607 (D1) fixed exactly
 * that ordering in one of the two copies while the other already had it;
 * splitting the sequence across call sites is how it drifted, so it is
 * encoded once, here, and covered by `tests/js/test_basemap_download_runner.js`.
 *
 * Why dependencies are injected
 * -----------------------------
 * The run touches ten of `map.js`'s module-level helpers, all of which
 * reach for the live map, Cache Storage, `navigator.storage` or the
 * service worker. Passing them in as a `deps` bundle — built once in
 * `map.js` — is what lets the ordering be tested against fakes rather than
 * a real MapLibre instance. Same thin-delegator shape `sw.js` uses for
 * `basemap_cache_core.js`.
 *
 * Loaded from `public/home.html` before `map.js` (both `defer`, so
 * document order is execution order).
 */

(function () {
  'use strict';

  /**
   * Run one pinned basemap download end to end: pre-flight, evict, warm.
   *
   * The caller owns everything that differs between the two controls — how
   * its roundel paints, where its blob comes from, and what it records on
   * success — and passes them in. Everything between belongs to this
   * function.
   *
   * `loadBlob` is a callback rather than an already-fetched blob because
   * WHEN the blob is fetched is part of the ordering: the region control
   * fetches it from the network, and that fetch must not happen until the
   * quota, tile-source and budget checks have all passed. The custom-area
   * control has already built its blob and simply hands it back.
   *
   * @param {{
   *   clearError: function(): void,
   *   revealError: function(string|null): void,
   *   fitsQuota: function(number): Promise<boolean>,
   *   core: function(): (Object|null|undefined),
   *   tileSources: function(): (string[][]|null|undefined),
   *   basemapKey: function(): (string|null|undefined),
   *   planBudget: function(string, number): Promise<Object|null>,
   *   confirmEviction: function(Array<Object>): Promise<boolean>,
   *   evict: function(string[]): Promise<void>,
   *   feedUrls: function(): string[],
 *   renderDeps?: function(): string[],
   *   progressGrid: function(Object|null, number): Object,
   *   warmCache: function(string[], Object): (Promise<Object>|null),
   *   isOnline: function(): boolean,
   * }} deps `map.js`'s helpers, bound once at its module level.
   * @param {{
   *   areaId: string,
   *   mb: number,
   *   loadBlob: function(): (Object|Promise<Object>),
   *   paint: function(string, number=, number=): void,
   *   beforeWarm?: function(Object, string, string[][]): Promise<void>,
   *   finish: function(Object|null, Object, Object): Promise<void>,
   * }} options
   *   `areaId` — the pinned bucket this run writes into.
   *   `mb` — worst-case size estimate PER TILE, for the quota and budget
   *     pre-flights. SNOW-843: scaled here by the number of vector sources
   *     the active style fetches, because the blob that produced it — a
   *     server-computed region summary or a locally built custom-area one —
   *     counts ground, not requests, and a two-source style downloads two
   *     tiles for every cell of it.
   *   `loadBlob` — resolves the tile blob; a rejection is a failed download.
   *   `paint` — paints one roundel state, optionally with a busy
   *     percentage and, SNOW-632, the run's on-disk bytes so far
   *     (`paint(state, pct?, bytes?)`). The two controls' own `setState`
   *     differ in arity, so they adapt.
   *   `beforeWarm` — optional last step after eviction, before the warm run
   *     (the custom-area control clears its own bucket when the frame
   *     moved; SNOW-632 widened this to the region control too, clearing a
   *     bucket whose tiles belong to a DIFFERENT basemap than the one this
   *     run is about to fetch). Called as `(blob, areaId, tileSources)` —
   *     the same sources this run itself resolved and is about to build
   *     tile URLs from, so a caller's eviction decision and the URLs that
   *     follow it can never disagree about which basemap is active.
   *   `finish` — the run's tail, called as `(result, blob, extras)` where
   *     `extras` carries `core`, `progressFill`, SNOW-632's `tileSources`
   *     (the same value handed to `beforeWarm`, so a caller recording what
   *     was downloaded records the basemap that was actually fetched, not
   *     whatever happens to be active when `finish` runs) and, SNOW-645,
   *     `basemapKey` — the picker key captured alongside `tileSources` at run
   *     start, display-only and possibly null. SNOW-844 adds `renderDeps` —
 *     the style/sprite/TileJSON URLs this run fetched, resolved at run
 *     start for the same reason `tileSources` is, and recorded so a later
 *     probe can check an area whose basemap is not the one on screen.
 *     `result` is the
   *     worker's report, or `null` when there was no worker at all. SNOW-632:
   *     `result` can now carry `cancelled: true` — the run stopped early on
   *     a `pwaWarmCacheCancel()` request rather than exhausting the URL
   *     list. A cancelled run always has `result.failed === 0` (nothing
   *     skipped by the cancel was ever attempted, so it can't have failed),
   *     so `finish` MUST check `cancelled` before reading a short `ok`
   *     count as a failed download — the two `finish` implementations
   *     (`map.js`) are the next pass's job, not this one.
   * @returns {Promise<void>} Resolves once the run has been DISPATCHED, not
   *   once it has completed — `finish` is called from the warm-cache
   *   promise, matching what both controls did before this extraction.
   */
  async function run(deps, options) {
    const { areaId, mb, loadBlob, paint, beforeWarm, finish } = options;

    // A new attempt clears the previous one's message before it can raise
    // its own (SNOW-568).
    deps.clearError();
    // 'busy' is claimed SYNCHRONOUSLY, before the first await — both
    // controls' re-entrancy guards read this state, so an await ahead of it
    // leaves a window in which a second click starts a second run. It also
    // means the roundel acknowledges the click immediately rather than
    // after the quota round trip.
    paint('busy', 0);

    const core = deps.core();
    const tileSources = deps.tileSources();
    // SNOW-843: what this run will actually spend, which is `mb` once per
    // vector source in the active style. Read before the quota check so no
    // pre-flight ever runs against the single-source figure — the checks
    // themselves keep their order, and an unresolved style leaves the
    // estimate at `mb` (see `sourceScaledMb`) and is refused outright a few
    // lines below.
    const estimateMb = core && core.sourceScaledMb ? core.sourceScaledMb(mb, tileSources) : mb;

    // Refuse a download that cannot fit in the origin's storage quota
    // before spending a single fetch on it (SNOW-568). Without this the run
    // gets most of the way through, starts collecting QuotaExceededErrors
    // from cache.put, and the user waits out a long download to be told it
    // failed.
    if (!(await deps.fitsQuota(estimateMb))) {
      paint('error');
      deps.revealError('quota');
      return;
    }

    // SNOW-645: the picker's basemap key, captured at run start so a
    // mid-download basemap switch still records the basemap that was
    // actually fetched. Display-only — see activeBasemapKey's header
    // comment — so an unresolved key (null) never aborts the run the way a
    // missing source list does; it just means the record below stays keyless.
    const basemapKey = deps.basemapKey();
    // No tile sources (style still settling) means no tiles to warm, and a
    // feeds-only run must never paint 'done' — the area would not in fact
    // be available offline. SNOW-568: reads as a failed download so the
    // user knows to retry (by which point the style will have settled),
    // rather than silently reverting.
    if (!core || !tileSources) {
      paint('error');
      deps.revealError(null);
      return;
    }

    // SNOW-586: refuse a run that no eviction could ever make fit BEFORE
    // spending a fetch on the blob — same "cost a click, not a whole run"
    // reasoning as the quota pre-flight above. The plan's eviction half is
    // acted on below, once nothing is left that can abort the run.
    const budgetPlan = await deps.planBudget(areaId, estimateMb);
    if (budgetPlan && budgetPlan.impossible) {
      paint('error');
      deps.revealError('budget');
      return;
    }

    let blob;
    try {
      blob = await loadBlob();
    } catch (_e) {
      // This never got as far as a warm-cache attempt, but from the user's
      // side a click that quietly returns to idle is the same silent
      // failure SNOW-568 exists to remove — the download they asked for did
      // not happen.
      paint('error');
      deps.revealError(null);
      return;
    }

    // Last step that can abort, and the only destructive one — see the
    // ordering note in the module header.
    if (budgetPlan && budgetPlan.evict.length > 0) {
      const evictAreas = budgetPlan.evict
        .map((id) => budgetPlan.areasById.get(id))
        .filter(Boolean);
      const proceed = await deps.confirmEviction(evictAreas);
      if (!proceed) {
        paint(deps.isOnline() ? 'idle' : 'offline');
        return;
      }
      await deps.evict(budgetPlan.evict);
    }

    if (beforeWarm) await beforeWarm(blob, areaId, tileSources);

    // Tile-grid rework: the tile list comes from the grid plan, not
    // `rangesToTileURLs` — same URLs, but ordered cell by cell so the
    // on-map grid fills in one square at a time rather than sweeping the
    // whole area once per zoom level. A plan is only ever null for a blob
    // with no ranges, which `rangesToTileURLs` would answer with an empty
    // list anyway.
    const gridPlan = core.tileGridPlan(tileSources, blob);
    const feedUrls = deps.feedUrls();
    const urls = [...feedUrls, ...(gridPlan ? gridPlan.urls : [])];

    // SNOW-569: the area's tiles are drawn as an empty grid that fills in
    // as they land.
    const progressFill = deps.progressGrid(gridPlan, feedUrls.length);

    // SNOW-632: `bytes` — the run's on-disk total so far — rides along as
    // a fourth argument from `deps.warmCache`'s `onProgress` and is passed
    // straight through to `paint` as ITS third argument, so a caller can
    // show a live MB readout alongside the percentage. `progressFill`
    // stays on the original three-argument shape — it fills tiles, which
    // have nothing to do with bytes.
    const onProgress = (done, total, settled, bytes) => {
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      paint('busy', pct, bytes);
      progressFill.update(done, total, settled);
    };

    // SNOW-844: the render dependencies this run is fetching — the style
    // document, the sprite and each vector source's TileJSON, all of them
    // already inside `feedUrls` above. Resolved HERE, once, and handed to
    // `finish` for the record, for the same reason `tileSources` is: a
    // basemap switched mid-download would otherwise have the record name
    // documents this run never fetched. A `deps` bundle without the member
    // (an older shell mid-rollout) yields `[]`, which every reader treats
    // as "unknown" rather than "complete".
    const renderDeps = typeof deps.renderDeps === 'function' ? deps.renderDeps() : [];

    const settle = (result) =>
      finish(result, blob, { core, progressFill, tileSources, basemapKey, renderDeps });

    // SNOW-521: `pinned: true` routes the basemap-origin writes into a
    // dedicated pinned bucket, exempt from the passive browsing LRU trim —
    // a deliberate download can't be evicted by casual panning elsewhere.
    // SNOW-586: `areaId` selects WHICH bucket, so one area can never
    // perforate (or be perforated by) another.
    // SNOW-742: `glyphPrefix` lets the worker promote this style's
    // already-cached glyph entries into the pinned bucket once the tiles are
    // down, so the area keeps its labels when the passive cache trims its own
    // copies. Optional — a deps object without it (or a style with no
    // `glyphs`) simply skips the promotion.
    const glyphPrefix = typeof deps.glyphPrefix === 'function' ? deps.glyphPrefix() : '';
    const warming = deps.warmCache(urls, { pinned: true, areaId, onProgress, glyphPrefix });
    if (warming) {
      warming.then(settle).catch(() => settle(null));
    } else {
      // No active worker at all — nothing ran and nothing was cached.
      settle(null);
    }
  }

  self.pwaBasemapDownloadRunner = { run: run };
}());
