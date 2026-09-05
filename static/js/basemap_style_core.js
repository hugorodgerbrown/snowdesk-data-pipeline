/*
 * static/js/basemap_style_core.js — resolving which basemap to draw on, and
 * noticing when it drew nothing (SNOW-829).
 *
 * Three pure functions, shared by the map page and the trip page because
 * both now answer the same question — which style does THIS reader want —
 * and only one of them has a picker to answer it with.
 *
 * ## The reader's choice is per-origin, not per-page
 *
 * `localStorage` is scoped per ORIGIN. The trip page can read
 * `snowdesk.map.basemap` (the key `map_state.js` writes) exactly as the map
 * page can, so a reader who picked swisstopo because it is dramatically
 * clearer for Swiss terrain keeps it on the one page where the route is
 * actually being read. `resolveBasemap` is the same resolution `map.js`
 * already does at boot: the stored key if it still names something in the
 * catalogue, the site default otherwise.
 *
 * ## Coverage, and why this file has no table of it
 *
 * The national basemaps cover one country each — `swisstopo_*` is
 * Switzerland, `ign_plan` France, `basemap_at` Austria — and outside it
 * they render blank. A reader who picked swisstopo and opens a trip in
 * Chamonix gets an empty canvas, and on a trip page there is no picker in
 * front of them to fix it.
 *
 * The obvious answer is a coverage rectangle per style, the way
 * `slope_overlay_core.js` carries one. It was rejected because the
 * providers' own numbers cannot seed it and the hand-written alternative
 * detects the wrong thing:
 *
 *   basemap.at  declares [8.8587, 45.7823, 17.1608, 49.5752] — honest.
 *   ign_plan    declares [-179.9, -80, 179.9, 80] — the whole world.
 *   swisstopo   declares [3.57, 44.18, 13.66, 48.88] — a box that CONTAINS
 *               Chamonix, so it would stay silent in the very case above.
 *
 * So `drewNothing` asks the canvas what it actually painted instead. After
 * MapLibre reports itself idle, `queryRenderedFeatures()` returns
 * everything drawn; subtract the page's own sources and if nothing remains,
 * the basemap genuinely drew nothing — whatever any provider claims about
 * its extent, and still true the day a provider's coverage changes. All
 * five basemaps in the catalogue are vector, so "no features" is meaningful
 * for every one of them.
 *
 * The one trap it has to avoid is believing an empty answer. A map that is
 * still loading renders nothing, and so does a `queryRenderedFeatures` that
 * is not answering at all — both look identical to a blank basemap, and
 * both would put the notice over a working map. So the predicate requires
 * the page's OWN features in the result before it will call anything
 * blank: they are drawn from data the page already holds, so their
 * presence is the proof that the query is live.
 *
 * ## Exports (frozen `self.pwaBasemapStyleCore`)
 *
 *   STORAGE_KEY                      — where the reader's choice lives
 *   resolveBasemap(stored, cat, def) → {key, url}, or null
 *   resolveStyle(key, url)           → a value setStyle takes (may be async)
 *   drewNothing(features, ourIds)    → true when our own drew and nothing else
 *
 * Every function here is pure, so all of it is unit-tested directly
 * (tests/js/test_basemap_style_core.js) with no browser and no map.
 */

(function () {
  'use strict';

  /**
   * Where the basemap picker persists the reader's choice.
   *
   * The literal `map_state.js` declares as BASEMAP_STORAGE_KEY. Named twice
   * rather than shared through this module because that file is a classic
   * script on the map page only, and a trip page must be able to read the
   * key without loading the map bundle.
   */
  var STORAGE_KEY = 'snowdesk.map.basemap';

  /**
   * basemap.at ships an ESRI ArcGIS VectorTileServer style whose vector
   * source uses a relative `tile/{z}/{y}/{x}.pbf` path MapLibre cannot
   * resolve, so nothing paints. IGN and swisstopo publish MapLibre-native
   * styles that load straight from their URL, so this only applies here.
   *
   * The twin of `map_state.js`'s ESRI_BASEMAP_KEYS and its
   * `resolveBasemapStyle`, which still carry their own copy. Kept separate
   * rather than collapsed: that file is part of the map page's classic-
   * script bundle and delegating would mean loading this module into it,
   * which is a change to the map page for a trip page's benefit. If a
   * third caller ever appears, collapse them then — and change both when
   * you change either.
   */
  var ESRI_BASEMAP_KEYS = ['basemap_at'];

  /**
   * Pick the basemap this reader should get.
   *
   * The resolution `map.js` performs at boot, extracted so the trip page
   * performs the same one: the stored key when it still names something in
   * the catalogue, and the site default otherwise. An unknown stored key is
   * a basemap that has since been removed, not an error.
   *
   * @param {?string} storedKey The value read from localStorage, or null.
   * @param {?Object} catalogue A `{key: url}` map of available basemaps.
   * @param {?string} defaultKey The site default's key.
   * @returns {?Object} `{key, url}`, or null when nothing resolves.
   */
  function resolveBasemap(storedKey, catalogue, defaultKey) {
    if (!catalogue) return null;
    var key =
      storedKey && catalogue[storedKey] ? storedKey : defaultKey;
    if (!key || !catalogue[key]) return null;
    return { key: key, url: catalogue[key] };
  }

  /**
   * Turn a resolved (key, url) into a value `setStyle` / the Map
   * constructor accepts.
   *
   * Native styles pass through as the URL string. An ESRI style is fetched
   * and each vector source's relative `url` swapped for an absolute `tiles`
   * template following the ArcGIS VectorTileServer convention, which
   * returns a Promise — so callers must await the result rather than pass
   * it straight to `setStyle`, which does not accept one. See
   * `map_state.js::resolveBasemapStyle`, the map page's own copy of this.
   *
   * @param {string} key The basemap key.
   * @param {string} url Its style URL.
   * @returns {(string|Promise<Object>)} A style URL, or a style object.
   */
  function resolveStyle(key, url) {
    if (ESRI_BASEMAP_KEYS.indexOf(key) === -1) return url;
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (style) {
        var sources = style.sources || {};
        Object.keys(sources).forEach(function (name) {
          var src = sources[name];
          if (src && src.type === 'vector' && src.url && !src.tiles) {
            src.tiles = [src.url.replace(/\/?$/, '/') + 'tile/{z}/{y}/{x}.pbf'];
            delete src.url;
          }
        });
        return style;
      });
  }

  /**
   * Did the basemap paint anything, given that the PAGE's own layers did?
   *
   * Takes the features `queryRenderedFeatures()` returned and the ids of
   * the sources the page added itself. Everything left over came from the
   * basemap style, so an empty remainder means the reader is looking at a
   * blank canvas — the out-of-coverage case, and equally a style that
   * failed to load.
   *
   * IT REQUIRES POSITIVE EVIDENCE THAT THE QUERY WORKS. An empty result
   * answers false, not true, and so does any result carrying none of our
   * own sources. "Nothing at all is rendered" is not the same claim as
   * "the basemap rendered nothing": it is what a map that is still loading
   * looks like, and what a `queryRenderedFeatures` that is not answering
   * looks like — both of which would otherwise put a "your map has no
   * detail here" notice over a perfectly good map. Our own route line is
   * drawn from data the page already holds, so its presence is the proof
   * that the query is live and the basemap's absence is real.
   *
   * Call it after MapLibre reports itself idle. Called earlier it sees a
   * map that is merely still loading, and answers false — which is the
   * safe direction, but it is not an answer.
   *
   * @param {?Array<Object>} features A queryRenderedFeatures() result.
   * @param {?Array<string>} ourSourceIds Source ids the page added.
   * @returns {boolean} True only when our features drew and nothing else.
   */
  function drewNothing(features, ourSourceIds) {
    if (!Array.isArray(features) || !features.length) return false;
    var ours = ourSourceIds || [];
    var sawOurs = false;
    for (var i = 0; i < features.length; i += 1) {
      var f = features[i];
      if (!f) continue;
      if (ours.indexOf(f.source) === -1) return false;
      sawOurs = true;
    }
    return sawOurs;
  }

  self.pwaBasemapStyleCore = Object.freeze({
    STORAGE_KEY: STORAGE_KEY,
    resolveBasemap: resolveBasemap,
    resolveStyle: resolveStyle,
    drewNothing: drewNothing,
  });
})();
