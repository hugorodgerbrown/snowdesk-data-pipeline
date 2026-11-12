// @ts-check
/*
 * static/js/map_offline_basemap.js — offline inside a downloaded area saved
 * on Swisstopo, the map shows Swisstopo (SNOW-1478).
 *
 * A download in Switzerland saves the Swisstopo map whatever map is on
 * screen. A reader who has picked another map and goes offline there would
 * otherwise see only what browsing left behind — the sharp line at zoom 13
 * Hugo saw in airplane mode on 8 Nov 2026. So, offline with the centre
 * inside an area that holds Swisstopo, this swaps the style to Swisstopo
 * and shows one line (#offline-basemap-banner); leaving every such area or
 * regaining a connection swaps back to the reader's own map. The decision
 * is map_offline_basemap_core.js `offlineDecision`; this file only reads
 * the page and acts on it.
 *
 * What it never does: write the reader's choice. The picker keeps showing
 * it (its rows are the reader's, `aria-checked`), and localStorage
 * `snowdesk.map.basemap` is the picker's alone. A swap goes out the way
 * the picker's and the 3-D view's do — `snowdesk:basemap-changing`, then
 * `resolveBasemapStyle` and `setStyle` — so map.js reinstalls its layers on
 * the new style as it does for theirs. A swap that resolves after a newer
 * one (a restore, a picker change, the 3-D view) is dropped
 * (`swapGuard`). In 3-D the map is already on Swisstopo, so nothing is done.
 *
 * Offline is `pwaConnectivity.isOnline() === false`, which folds Offline
 * mode in (pwa_offline.js). The areas are this device's own
 * (`pwaBasemapAreas.downloadedAreas({deviceOnly: true})`), read at boot and
 * on `snowdesk:downloads-changed`; the decision runs again on
 * `snowdesk:connectivity-changed`, `snowdesk:map-view-changed` and `moveend`.
 * Only the picker's swap (`detail.source === 'picker'`) counts as the
 * reader choosing a map while switched.
 *
 * Loaded after the map bundle (bundles/map.txt): it reads
 * `window.snowdeskMap` at parse time.
 */
(function mapOfflineBasemapInit() {
  'use strict';

  const win = /** @type {any} */ (window);
  const core = self.pwaOfflineBasemapCore;
  const map = win.snowdeskMap;
  if (!core || !map) return;

  const banner = document.getElementById('offline-basemap-banner');
  const guard = core.swapGuard();

  /** @type {Array<{bbox: (ReadonlyArray<number>|null), keys: string[]}>} */
  let areas = [];
  /** Whether the page is showing Swisstopo in place of the reader's map. */
  let switched = false;
  /** Set while this module dispatches its own swap event. */
  let dispatching = false;
  /**
   * The reader changed map while switched: honour it until the connection
   * changes, rather than swapping straight back on the next move.
   */
  let held = false;
  /** Counts the reads of the areas, so a late one is dropped. */
  let readGeneration = 0;

  /** @returns {boolean} */
  const online = () => {
    if (win.pwaConnectivity && typeof win.pwaConnectivity.isOnline === 'function') {
      return win.pwaConnectivity.isOnline();
    }
    return navigator.onLine !== false;
  };

  /**
   * A picker row's style URL.
   *
   * @param {string} key
   * @returns {string}
   */
  const urlFor = (key) => {
    const rows = document.querySelectorAll('#basemap-menu .basemap-menu-item[data-basemap-key]');
    for (const row of rows) {
      const el = /** @type {HTMLElement} */ (row);
      if (el.dataset.basemapKey === key) return el.dataset.basemapUrl || '';
    }
    return '';
  };

  /** @returns {string|null} The reader's own map: the picker's checked row. */
  const readerKey = () => {
    const row = /** @type {?HTMLElement} */ (
      document.querySelector('#basemap-menu .basemap-menu-item[data-basemap-key][aria-checked="true"]')
    );
    return (row && row.dataset.basemapKey) || null;
  };

  /** @param {boolean} show */
  const paintBanner = (show) => {
    if (banner) banner.classList.toggle('hidden', !show);
  };

  /**
   * Draw `key` without touching the reader's choice.
   *
   * @param {string} key
   * @returns {boolean} Whether a swap started.
   */
  const swapTo = (key) => {
    const url = urlFor(key);
    if (!url) return false;
    dispatching = true;
    try {
      document.dispatchEvent(new CustomEvent('snowdesk:basemap-changing', { detail: { key, url } }));
    } finally {
      dispatching = false;
    }
    const generation = guard.next();
    const resolve = win.resolveBasemapStyle;
    Promise.resolve(typeof resolve === 'function' ? resolve(key, url) : url)
      .then((style) => {
        if (!guard.isCurrent(generation)) return;
        map.setStyle(style);
      })
      .catch((/** @type {unknown} */ err) => console.warn('map_offline_basemap: could not change the basemap', err));
    return true;
  };

  /** Act on the decision for where the map is now. */
  const evaluate = () => {
    if (document.documentElement.dataset.mapView === '3d') return;
    const decision = core.offlineDecision({
      online: online(),
      centre: map.getCenter(),
      activeKey: readerKey(),
      switched: switched,
      areas: areas,
    });
    if (decision === 'switch' && !held) {
      if (swapTo(core.SWISSTOPO_KEY)) {
        switched = true;
        paintBanner(true);
      }
    } else if (decision === 'restore') {
      const key = readerKey();
      switched = false;
      paintBanner(false);
      if (key) swapTo(key);
    }
  };

  /** Read this device's areas and the maps each holds, then decide. */
  const readAreas = async () => {
    readGeneration += 1;
    const generation = readGeneration;
    const store = win.pwaBasemapAreas;
    const downloads = win.pwaBasemapDownloadCore;
    if (!store || typeof store.downloadedAreas !== 'function' || !downloads) return;
    let rows = [];
    try {
      rows = await store.downloadedAreas({ deviceOnly: true });
    } catch (_err) {
      rows = [];
    }
    if (generation !== readGeneration) return;
    areas = (Array.isArray(rows) ? rows : []).map((/** @type {any} */ area) => ({
      bbox: downloads.areaBBox(area),
      keys: downloads.recordBasemaps(area).map((/** @type {any} */ entry) => entry.key),
    }));
    evaluate();
  };

  // Another swap — the picker, the 3-D view — supersedes this one. A reader
  // who picks a map while switched has chosen; it stands until the
  // connection changes. The 3-D view's swap back to the reader's map on
  // leaving 3-D is not a choice (no `source: 'picker'`), so the flat map
  // is judged again once the view has changed.
  document.addEventListener('snowdesk:basemap-changing', (e) => {
    if (dispatching) return;
    guard.next();
    if (switched) {
      const detail = /** @type {CustomEvent} */ (e).detail;
      switched = false;
      if (detail && detail.source === 'picker') held = true;
      paintBanner(false);
    }
  });
  document.addEventListener('snowdesk:map-view-changed', () => {
    evaluate();
  });
  document.addEventListener('snowdesk:connectivity-changed', () => {
    held = false;
    evaluate();
  });
  document.addEventListener('snowdesk:downloads-changed', () => {
    readAreas();
  });
  map.on('moveend', evaluate);

  readAreas();

  win.pwaOfflineBasemap = Object.freeze({
    /** @returns {boolean} Whether Swisstopo is showing in place of the reader's map. */
    switched: () => switched,
    evaluate: evaluate,
    refresh: readAreas,
  });
})();
