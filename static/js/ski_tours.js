/*
 * static/js/ski_tours.js — "Find a route": choose an SAC ski tour on the
 * map and save it as a route (SNOW-1278, behind the `route_drawing` flag).
 *
 * The DOM and map half; the pure half (what a tap means, the rectangle a
 * view loads, the URLs) is ski_tours_core.js. The surface is
 * public/partials/_ski_tours_surface.html, which explains the interaction.
 *
 * THE TOURS AND THE SUMMITS ARE THE SKI-TOURING LAYER (SNOW-1285): two
 * parts, each with its own switch in the layers menu (map.js keeps the
 * switches, window.pwaSkiRoutes; the layer ids are OVERLAY_LAYERS.ski_tours
 * and .ski_summits). Both are drawn from z9 and tapped the same way at
 * every zoom: below z12 from every tour at once, each line thinned
 * (/api/ski-tours.geojson?overview=1, loaded once), and from z12 from the
 * view's own rectangle at full detail. swisstopo's raster of its whole
 * network is no longer drawn: nothing in it could be tapped.
 *
 * Every tour is drawn as a route's legs are (SNOW-1310): the server cuts
 * it where the ground turns, a climb in SAC's deep blue and a descent in
 * sky blue, so a reader's own routes (grey and fuchsia) stand apart. Both
 * solid: on SAC's own maps a dash means a leg needing extra care and a dot
 * one climbed on foot.
 * A summit is a label (a dot and its name), never a target.
 *
 * A TAP (map.js's dispatcher calls `tap(point)` after the markers and the
 * weather symbols) on a line opens the tour nearest it
 * (ski_tours_core.nearestTour). A tour opens in the route panel
 * through window.pwaSkiTourRoute, at once from the line and SAC's figures;
 * when the server has never walked the terrain under it the panel says so
 * (`terrain_pending`), the tour is asked for again with `?sample=1`, and
 * the panel is refilled with the profile.
 *
 * Publishes (frozen `window.pwaSkiTours`):
 *   tap(point)          — handle a map tap at a screen point; true when taken
 *   find()              — "Find a route": the overlay on, and the hint
 *   openSummit(summit)  — a summit picked in search (SNOW-1294): the layer
 *                         on, the map at the summit, and a "Tours need
 *                         signal" toast when they cannot load (SNOW-1320)
 */

// @ts-check

(function skiToursInit() {
  'use strict';

  // Cast once here: every one is checked below before anything runs.
  const config = /** @type {HTMLElement} */ (document.getElementById('ski-tours-config'));
  const hint = /** @type {HTMLElement} */ (document.getElementById('ski-tours-hint'));
  const claimTemplate = /** @type {HTMLTemplateElement} */ (
    document.getElementById('ski-tour-claim-template')
  );
  const core = self.pwaSkiToursCore;
  if (!config || !hint || !claimTemplate || !core || !window.MapSheet) return;

  const NIL_UUID = '00000000-0000-0000-0000-000000000000';
  const GEOJSON_URL = config.dataset.geojsonUrl || '';
  const FEATURE_URL = (config.dataset.featureUrl || '').replace(NIL_UUID, '__UUID__');
  const SAVE_URL = (config.dataset.saveUrl || '').replace(NIL_UUID, '__UUID__');
  const SIGNIN_URL = config.dataset.signinUrl || '';
  const SAVE_ELIGIBLE = config.dataset.saveEligible === 'true';

  const SOURCE = 'ski-tours';
  // A way up and a way down, a layer each, so map_state.js can name them.
  const LINE = 'ski-tours-line';
  const LINE_DOWN = 'ski-tours-line-down';
  const SUMMIT = 'ski-tours-summit';
  const LABEL = 'ski-tours-summit-label';
  // MapLibre paint cannot read a CSS custom property. The lines are a
  // route's two leg colours (legColours); the marks are the ink and the card.
  const INK_COLOUR = '#1a1916';
  const HALO_COLOUR = '#ffffff';
  const LOAD_DEBOUNCE_MS = 250;
  // SNOW-1320: how long a summit pick waits for its tours fetch. Long
  // enough for the flight and a slow answer; past it a failure is no
  // longer about the pick.
  const SUMMIT_MARK_MS = 15000;

  const STRINGS = self.pwaStrings.read('ski-tours-strings-template', {
    hint: 'Tap a route',
    'hint-zoom': 'Zoom in, then tap a route',
    failed: "The routes couldn't be loaded. Check your connection.",
    saved: 'Route saved.',
    'save-failed': "That route couldn't be saved. Try again.",
    'save-limit': "You've reached your saved-route limit. Delete one to save another.",
    'save-offline': 'You need to be online to save a route.',
    'tours-offline': 'Tours need signal',
  });

  const state = {
    /** The last collection loaded, for a basemap swap's re-install. */
    /** @type {{type: string, features: Array<any>}} */
    data: { type: 'FeatureCollection', features: [] },
    /** The rectangle `data` covers, or null (and null for the overview). */
    /** @type {?[number, number, number, number]} */
    loaded: null,
    /** What `data` is: the overview, a view's detail, or nothing yet. */
    /** @type {'none'|'overview'|'detail'} */
    mode: 'none',
    /** The overview, once loaded: it does not change while the page lives. */
    /** @type {?{type: string, features: Array<any>}} */
    overview: null,
    /** @type {?AbortController} */
    inflight: null,
    /** @type {?number} */
    timer: null,
    /** The tour the panel was asked for last, so a late answer is dropped. */
    /** @type {?string} */
    opening: null,
    /**
     * SNOW-1320: when a summit was picked in search (ms), so the next tours
     * fetch to settle says so if it fails. One-shot: that fetch consumes
     * it. A pick no fetch followed (the view already loaded) leaves it
     * set, so a mark older than SUMMIT_MARK_MS is ignored.
     * @type {?number}
     */
    summitPending: null,
  };

  /** @returns {any} The MapLibre map, or null before it exists. */
  function getMap() {
    return (window.snowdeskMapState && window.snowdeskMapState.map) || null;
  }

  /** @returns {boolean} Whether either part is switched on. */
  function shown() {
    return !!(window.pwaSkiRoutes && window.pwaSkiRoutes.isShown());
  }

  /** @returns {boolean} Whether a placement mode (draw mode) has the map. */
  function focused() {
    return !!(window.PlacementFocus && window.PlacementFocus.isActive());
  }

  /**
   * A part's layers' visibility: as its switch says, and hidden while a
   * placement mode has the map. PlacementFocus hides only the layers that
   * exist when it starts, and the tours are often added later, on the
   * first zoom past z9.
   *
   * @param {'ski_tours'|'ski_summits'} key
   * @returns {'visible'|'none'}
   */
  function partVisibility(key) {
    const on = window.pwaSkiRoutes && window.pwaSkiRoutes.isPartShown(key);
    return on && !focused() ? 'visible' : 'none';
  }

  /** Show or hide each part's layers, once they exist. */
  function applyVisibility() {
    const map = getMap();
    if (!map || !map.getLayer(LINE)) return;
    for (const id of [LINE, LINE_DOWN]) {
      map.setLayoutProperty(id, 'visibility', partVisibility('ski_tours'));
    }
    for (const id of [SUMMIT, LABEL]) {
      map.setLayoutProperty(id, 'visibility', partVisibility('ski_summits'));
    }
  }

  /** @returns {string} The CSRF token for a POST. */
  function csrfToken() {
    const input = /** @type {HTMLInputElement|null} */ (
      config && config.querySelector('input[name="csrfmiddlewaretoken"]')
    );
    return input ? input.value : '';
  }

  // ---------------------------------------------------------------------------
  // The layers
  // ---------------------------------------------------------------------------

  /**
   * SAC's two colours, off the legs core, read when the layers are added
   * (SNOW-1307). The literals mirror `--color-sac-climb` and
   * `--color-sac-descent`.
   *
   * @returns {{climb: string, descent: string}}
   */
  function legColours() {
    const legs = self.pwaRouteLegsCore || {};
    return {
      climb: legs.SAC_CLIMB_COLOUR || '#1d4ed8',
      descent: legs.SAC_DESCENT_COLOUR || '#38bdf8',
    };
  }

  /**
   * Add the tours' source and layers, once per style.
   *
   * Under the boundaries and every pin, over the danger colours. Each
   * part's layers start as its switch says.
   *
   * @param {any} map
   */
  function installLayers(map) {
    if (map.getSource(SOURCE)) return;
    map.addSource(SOURCE, {
      type: 'geojson',
      data: state.data,
      attribution: 'SAC ski tours © swisstopo',
    });
    const visibility = partVisibility('ski_tours');
    const summitVisibility = partVisibility('ski_summits');
    const before = map.getLayer('regions-line') ? 'regions-line' : undefined;
    const minzoom = core.MINZOOM;
    const isTour = ['==', ['get', 'kind'], 'tour'];
    // Thin out to z12, where a valley's tours share a few pixels; bolder
    // past it, where the lines separate. Thinner than the open tour, which
    // is drawn over them in the same colours.
    const width = ['interpolate', ['linear'], ['zoom'], 9, 1.5, 12, 2.5, 15, 4];
    // SNOW-1310: each tour arrives cut into its legs, as a route is drawn:
    // a climb in SAC's deep blue, a descent in sky blue, so a reader's own
    // routes (grey and fuchsia) stand clear of them. A tour with no
    // terrain yet is one line with `climbing` null, drawn deep blue. Never
    // dashed: a dash on an SAC map means "take extra care".
    const legs = legColours();
    map.addLayer({
      id: LINE,
      type: 'line',
      source: SOURCE,
      minzoom: minzoom,
      filter: ['all', isTour, ['!=', ['get', 'climbing'], false]],
      layout: { visibility: visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': legs.climb, 'line-width': width },
    }, before);
    map.addLayer({
      id: LINE_DOWN,
      type: 'line',
      source: SOURCE,
      minzoom: minzoom,
      filter: ['all', isTour, ['==', ['get', 'climbing'], false]],
      layout: { visibility: visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': legs.descent, 'line-width': width },
    }, before);
    const isMark = ['in', ['get', 'kind'], ['literal', ['summit', 'hut']]];
    map.addLayer({
      id: SUMMIT,
      type: 'circle',
      source: SOURCE,
      minzoom: minzoom,
      filter: isMark,
      layout: { visibility: summitVisibility },
      paint: {
        // A summit is a dot of ink; a hut a ring, as a hut is drawn on a
        // topographic map.
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 3, 11, 4.5],
        'circle-color': ['case', ['==', ['get', 'kind'], 'hut'], HALO_COLOUR, INK_COLOUR],
        'circle-stroke-color': ['case', ['==', ['get', 'kind'], 'hut'], INK_COLOUR, HALO_COLOUR],
        'circle-stroke-width': 1.5,
      },
    }, before);
    // The map's own overlay font (SNOW-478), not the style's commonest,
    // which on some basemaps is the water labels' italic.
    const font = window.pwaSkiRoutes && window.pwaSkiRoutes.textFont
      ? window.pwaSkiRoutes.textFont()
      : undefined;
    map.addLayer({
      id: LABEL,
      type: 'symbol',
      source: SOURCE,
      minzoom: core.LABEL_MINZOOM,
      filter: isMark,
      layout: {
        visibility: summitVisibility,
        'text-field': [
          'case',
          ['has', 'altitude_m'],
          ['concat', ['get', 'name'], ' ', ['to-string', ['get', 'altitude_m']]],
          ['get', 'name'],
        ],
        ...(font ? { 'text-font': font } : {}),
        'text-size': 11,
        'text-anchor': 'top',
        'text-offset': [0, 0.6],
        'text-max-width': 9,
      },
      paint: {
        'text-color': INK_COLOUR,
        'text-halo-color': HALO_COLOUR,
        'text-halo-width': 1.5,
      },
    }, before);
  }

  // ---------------------------------------------------------------------------
  // Loading the view
  // ---------------------------------------------------------------------------

  /**
   * Put a loaded collection on the map.
   *
   * @param {any} map
   * @param {{type: string, features: Array<any>}} body
   */
  function paintData(map, body) {
    state.data = body;
    const source = map.getSource(SOURCE);
    if (source) source.setData(body);
    else installLayers(map);
  }

  /**
   * Consume the summit mark (SNOW-1320): the reader picked a summit and
   * the tours it was for could not be fetched, so say why the map shows
   * none. Any other way of turning the layer on never sets the mark.
   *
   * @param {boolean} ok Whether the fetch that settled brought tours.
   */
  function settleSummit(ok) {
    const at = state.summitPending;
    if (at === null) return;
    state.summitPending = null;
    if (!ok && Date.now() - at <= SUMMIT_MARK_MS) {
      window.MapSheet.toast(STRINGS['tours-offline']);
    }
  }

  /**
   * Fetch one collection, the one in flight before it dropped.
   *
   * @param {string} url
   * @param {function({type: string, features: Array<any>}): void} done
   */
  function fetchData(url, done) {
    if (state.inflight) state.inflight.abort();
    const inflight = new AbortController();
    state.inflight = inflight;
    fetch(url, { credentials: 'same-origin', signal: inflight.signal })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (body) {
        if (state.inflight !== inflight) return;
        state.inflight = null;
        const ok = !!(body && Array.isArray(body.features));
        settleSummit(ok);
        if (ok) done(body);
      })
      .catch(function () {
        // A fetch another replaced was aborted for it, not failed: the
        // mark waits for the one that replaced it.
        if (state.inflight !== inflight) return;
        state.inflight = null;
        settleSummit(false);
      });
  }

  /**
   * Load what the zoom asks for, when either part is on: every tour thinned
   * out to z12, the view's own detail from it, and nothing past z9.
   */
  function load() {
    const map = getMap();
    if (!map || !GEOJSON_URL || !shown() || focused()) return;
    const mode = core.loadMode(map.getZoom());
    if (mode === 'none') return;
    if (mode === 'overview') {
      if (state.mode === 'overview') return;
      const show = function (/** @type {{type: string, features: Array<any>}} */ body) {
        state.overview = body;
        state.mode = 'overview';
        state.loaded = null;
        paintData(map, body);
      };
      if (state.overview) show(state.overview);
      else fetchData(GEOJSON_URL + '?overview=1', show);
      return;
    }
    const b = map.getBounds();
    /** @type {[number, number, number, number]} */
    const view = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    if (state.mode === 'detail' && core.covers(state.loaded, view)) return;
    const box = core.loadBox(view);
    if (!box) return;
    fetchData(GEOJSON_URL + '?bbox=' + box.join(','), function (body) {
      state.mode = 'detail';
      state.loaded = box;
      paintData(map, body);
    });
  }

  /** Load after the map settles, at most once a quarter second. */
  function scheduleLoad() {
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = window.setTimeout(function () {
      state.timer = null;
      load();
      paintHint();
    }, LOAD_DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------------------
  // The hint
  // ---------------------------------------------------------------------------

  /** Say what to do next, while the hint is up. */
  function paintHint() {
    if (hint.hidden) return;
    const map = getMap();
    const text = hint.querySelector('[data-ski-tours-hint-text]');
    if (!text || !map) return;
    text.textContent = map.getZoom() < core.MINZOOM ? STRINGS['hint-zoom'] : STRINGS.hint;
  }

  /** @param {boolean} visible */
  function showHint(visible) {
    const map = getMap();
    if (visible && map && hint.parentElement !== map.getContainer()) {
      map.getContainer().appendChild(hint);
    }
    hint.hidden = !visible;
    paintHint();
  }

  // ---------------------------------------------------------------------------
  // A tour in the route panel
  // ---------------------------------------------------------------------------

  /**
   * Build the tour's block for the panel's claim slot.
   *
   * @param {Object<string, any>} props The tour Feature's properties.
   * @param {{saved?: boolean}} [options] `saved`: the block for a copy the
   *   reader already holds (SNOW-1303) — the guide and the credit, no Save.
   * @returns {HTMLElement}
   */
  function buildClaim(props, options) {
    const node = /** @type {HTMLElement} */ (
      /** @type {DocumentFragment} */ (claimTemplate.content.cloneNode(true)).firstElementChild
    );
    const guide = /** @type {HTMLAnchorElement|null} */ (node.querySelector('[data-ski-tour-guide]'));
    if (guide) {
      if (props.sac_url) {
        guide.href = String(props.sac_url);
      } else {
        guide.remove();
      }
    }
    if (options && options.saved) {
      node.querySelectorAll('[data-ski-tour-save-slot]').forEach((el) => el.remove());
      const actions = node.querySelector('[data-ski-tour-actions]');
      if (actions && !actions.children.length) actions.remove();
      return node;
    }
    const save = /** @type {HTMLButtonElement|null} */ (node.querySelector('[data-ski-tour-save]'));
    if (save) {
      save.addEventListener('click', function () {
        saveTour(String(props.tour_uuid), save);
      });
    }
    return node;
  }

  /**
   * Copy a tour onto the reader's routes, then show it as a saved route.
   *
   * @param {string} uuid
   * @param {HTMLButtonElement} button
   */
  function saveTour(uuid, button) {
    if (!SAVE_ELIGIBLE) {
      if (SIGNIN_URL) window.location.assign(SIGNIN_URL);
      return;
    }
    if (navigator.onLine === false) {
      window.MapSheet.toast(STRINGS['save-offline']);
      return;
    }
    // Disabled for the duration: a second tap would save a second copy.
    button.disabled = true;
    fetch(SAVE_URL.replace('__UUID__', encodeURIComponent(uuid)), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken() },
    })
      .then(function (resp) {
        if (resp.status === 403 && SIGNIN_URL) {
          window.location.assign(SIGNIN_URL);
          return null;
        }
        if (!resp.ok) {
          window.MapSheet.toast(resp.status === 409 ? STRINGS['save-limit'] : STRINGS['save-failed']);
          return null;
        }
        return resp.json();
      })
      .catch(function () {
        window.MapSheet.toast(STRINGS['save-failed']);
        return null;
      })
      .then(function (body) {
        if (!body) {
          button.disabled = false;
          return;
        }
        window.pwaTelemetry?.emit('map.route.created', { tour: true });
        window.pwaRouteRail?.close();
        // What an upload does once it lands: the map re-reads the routes
        // layer, then shows and frames the new route.
        document.dispatchEvent(
          new CustomEvent('snowdesk:routes-changed', { detail: { uploaded: true } }),
        );
        window.pwaRowFocus?.reveal({
          overlay: window.pwaRoutesOverlay,
          coordinates: Array.isArray(body.bounds) ? body.bounds : null,
        });
        window.MapSheet.toast(STRINGS.saved);
      });
  }

  /**
   * Fetch one tour's Feature.
   *
   * @param {string} uuid
   * @param {boolean} sample
   * @returns {Promise<any>}
   */
  function fetchFeature(uuid, sample) {
    return fetch(core.featureUrl(FEATURE_URL, uuid, sample), { credentials: 'same-origin' })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (body) {
        return body && body.feature ? body.feature : null;
      })
      .catch(function () {
        return null;
      });
  }

  /**
   * Open a tour in the route panel, brought into view, and follow with its terrain
   * when the server has not sampled it before.
   *
   * @param {string} uuid
   */
  function openTour(uuid) {
    if (!FEATURE_URL || !window.pwaSkiTourRoute) return;
    state.opening = uuid;
    showHint(false);
    fetchFeature(uuid, false).then(function (feature) {
      if (state.opening !== uuid) return;
      if (!feature) {
        window.MapSheet.toast(STRINGS.failed);
        return;
      }
      const claim = buildClaim(feature.properties || {});
      window.pwaSkiTourRoute.open(feature, { claim: claim, frame: true });
      if (!(feature.properties && feature.properties.terrain_pending)) return;
      fetchFeature(uuid, true).then(function (sampled) {
        if (!sampled || state.opening !== uuid) return;
        if (window.pwaSkiTourRoute.openUuid() !== uuid) return;
        window.pwaSkiTourRoute.open(sampled, { claim: claim });
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Taps and the way in
  // ---------------------------------------------------------------------------

  /**
   * Handle a map tap at a screen point: open the tour nearest it.
   *
   * @param {{x: number, y: number}} point
   * @returns {boolean} Whether the tap was on a tour.
   */
  function tap(point) {
    const map = getMap();
    if (!map || !shown() || core.loadMode(map.getZoom()) === 'none' || !map.getLayer(LINE)) {
      return false;
    }
    const px = core.TAP_PX;
    const hits = map.queryRenderedFeatures(
      [[point.x - px, point.y - px], [point.x + px, point.y + px]],
      { layers: [LINE, LINE_DOWN] },
    );
    const uuid = core.nearestTour(hits, point, function (/** @type {Array<number>} */ c) {
      return map.project(/** @type {[number, number]} */ ([c[0], c[1]]));
    });
    if (!uuid) return false;
    openTour(uuid);
    return true;
  }

  /**
   * A summit picked in search (SNOW-1294): both parts of the ski-touring
   * layer come on, as "Find a route" turns them on, and the map flies to
   * the summit, close enough for its tours' own detail. The reader then
   * picks a tour by its line (SNOW-1306).
   *
   * SNOW-1320: the summit list reads offline, the tours do not, so a
   * summit picked without signal would land on a map with no tours and no
   * reason. Known to be offline, say so now; otherwise mark the pick, and
   * the next tours fetch says so if it fails (settleSummit).
   *
   * @param {{key: string, lon: number, lat: number}} summit
   */
  function openSummit(summit) {
    const map = getMap();
    if (!summit || !summit.key || !map) return;
    if (navigator.onLine === false) {
      state.summitPending = null;
      window.MapSheet.toast(STRINGS['tours-offline']);
    } else {
      state.summitPending = Date.now();
    }
    window.pwaSkiRoutes?.show();
    if (Number.isFinite(summit.lon) && Number.isFinite(summit.lat)) {
      map.flyTo({
        center: [summit.lon, summit.lat],
        zoom: Math.max(map.getZoom(), core.DETAIL_MINZOOM + 1),
      });
    }
  }

  /** "Find a route": the routes panel closes, the tours come on, the hint shows. */
  function find() {
    window.pwaMapOverlays?.closeAll();
    window.pwaSkiRoutes?.show();
    showHint(true);
    load();
  }

  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('[data-ski-tour-find]')) {
      find();
      return;
    }
    if (target.closest('[data-ski-tours-hint-close]')) showHint(false);
  });

  // The tour the panel showed has gone: a late answer for it is dropped.
  document.addEventListener('snowdesk:ski-tour-closed', function () {
    state.opening = null;
  });

  /** Wire the map once it exists, and re-add the layers on a style swap. */
  function bindMap() {
    const map = getMap();
    if (!map) {
      window.setTimeout(bindMap, 200);
      return;
    }
    const ready = function () {
      if (!map.isStyleLoaded()) return;
      installLayers(map);
      load();
    };
    map.on('styledata', ready);
    map.on('moveend', scheduleLoad);
    // A part switched on loads as soon as asked; a placement mode's start
    // and end (PlacementFocus announces both) hide and restore the parts.
    document.addEventListener('snowdesk:overlay-visibility-changed', function () {
      applyVisibility();
      scheduleLoad();
    });
    ready();
  }
  bindMap();

  window.pwaSkiTours = Object.freeze({
    tap: tap,
    find: find,
    openSummit: openSummit,
    /**
     * SNOW-1303: the SAC block for a saved copy's panel — the route rail
     * calls this with a copy's `sac` property.
     *
     * @param {Object<string, any>} props The tour's properties.
     * @returns {?HTMLElement}
     */
    savedBlock: (props) => (claimTemplate ? buildClaim(props, { saved: true }) : null),
  });
})();
