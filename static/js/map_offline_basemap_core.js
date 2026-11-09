// @ts-check
/*
 * static/js/map_offline_basemap_core.js — when the map shows Swisstopo in
 * place of the reader's own choice, offline (SNOW-1478).
 *
 * A download in Switzerland saves the Swisstopo map whatever map is on
 * screen (basemap_download_core.js `basemapsForGround`). A reader who has
 * picked another map and goes offline inside such an area would otherwise
 * see only what browsing left behind, so map_offline_basemap.js shows
 * Swisstopo there for as long as the page stays offline and inside, and
 * never writes the reader's own choice. The decisions are pure and here,
 * so Vitest can pin them:
 *
 *   offlineDecision(input) → 'switch' | 'restore' | 'stay'
 *   swapGuard() → {next(), isCurrent(generation)}, so a style that loads
 *     after a newer swap (a restore, a picker change) is dropped — the
 *     generation pattern map_view_3d.js uses for its own style swap.
 *
 * Exports (frozen `self.pwaOfflineBasemapCore`).
 */
(function () {
  'use strict';

  /** The map a download in Switzerland saves (basemap_download_core.js `ELEVATION_BASEMAP_KEY`). */
  const SWISSTOPO_KEY = 'swisstopo_winter';

  /**
   * Whether a point is inside a box.
   *
   * @param {{lng: number, lat: number}|null|undefined} centre
   * @param {ReadonlyArray<number>|null|undefined} box `[west, south, east, north]`.
   * @returns {boolean}
   */
  function covers(centre, box) {
    if (!centre || !Array.isArray(box) || box.length !== 4) return false;
    return (
      centre.lng >= box[0] && centre.lng <= box[2] && centre.lat >= box[1] && centre.lat <= box[3]
    );
  }

  /**
   * What the offline switch does next.
   *
   * @param {{online: boolean, centre: ({lng: number, lat: number}|null),
   *   activeKey: (string|null), switched: boolean,
   *   areas: ReadonlyArray<{bbox: (ReadonlyArray<number>|null), keys: ReadonlyArray<string>}>}} input
   *   `activeKey` is the reader's own choice, `switched` whether the page
   *   is showing Swisstopo in its place, `areas` the downloaded areas with
   *   the maps each holds.
   * @returns {'switch'|'restore'|'stay'}
   */
  function offlineDecision(input) {
    const inside = (input.areas || []).some(
      (area) => area.keys.indexOf(SWISSTOPO_KEY) >= 0 && covers(input.centre, area.bbox),
    );
    if (input.switched) return input.online || !inside ? 'restore' : 'stay';
    if (input.online || !input.activeKey || input.activeKey === SWISSTOPO_KEY) return 'stay';
    return inside ? 'switch' : 'stay';
  }

  /**
   * A counter of style swaps. Each swap takes `next()`; its load handler
   * applies only while `isCurrent` says it is still the latest.
   *
   * @returns {{next: function(): number, isCurrent: function(number): boolean}}
   */
  function swapGuard() {
    let generation = 0;
    return {
      next: () => {
        generation += 1;
        return generation;
      },
      isCurrent: (value) => value === generation,
    };
  }

  self.pwaOfflineBasemapCore = Object.freeze({
    SWISSTOPO_KEY: SWISSTOPO_KEY,
    covers: covers,
    offlineDecision: offlineDecision,
    swapGuard: swapGuard,
  });
})();
