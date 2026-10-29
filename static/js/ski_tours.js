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
 * While a list is open the tours it names are drawn over the rest as a
 * saved route's legs are (SNOW-1286): a way up in the climb's dashed grey,
 * a way down in the descent's solid fuchsia, each with a green dot at its far end
 * (where a way up starts and a way down finishes), and every other tour is
 * dimmed.
 *
 * A TAP (map.js's dispatcher calls `tap(point)` after the markers and the
 * weather symbols) on a summit lists every tour to it; on a line it opens
 * the one tour in reach, or lists them. A tour opens in the route panel
 * through window.pwaSkiTourRoute, at once from the line and SAC's figures;
 * when the server has never walked the terrain under it the panel says so
 * (`terrain_pending`), the tour is asked for again with `?sample=1`, and
 * the panel is refilled with the profile.
 *
 * Publishes (frozen `window.pwaSkiTours`):
 *   tap(point)          — handle a map tap at a screen point; true when taken
 *   find()              — "Find a route": the overlay on, and the hint
 *   openSummit(summit)  — a summit picked in search (SNOW-1294): the layer
 *                         on, the map at the summit, its tours listed
 */

// @ts-check

(function skiToursInit() {
  'use strict';

  // Cast once here: every one is checked below before anything runs.
  const config = /** @type {HTMLElement} */ (document.getElementById('ski-tours-config'));
  const sheet = /** @type {HTMLElement} */ (document.getElementById('ski-tours-sheet'));
  const hint = /** @type {HTMLElement} */ (document.getElementById('ski-tours-hint'));
  const claimTemplate = /** @type {HTMLTemplateElement} */ (
    document.getElementById('ski-tour-claim-template')
  );
  const core = self.pwaSkiToursCore;
  if (!config || !sheet || !hint || !claimTemplate || !core || !window.MapSheet) return;

  const NIL_UUID = '00000000-0000-0000-0000-000000000000';
  const GEOJSON_URL = config.dataset.geojsonUrl || '';
  const LIST_URL = config.dataset.listUrl || '';
  const FEATURE_URL = (config.dataset.featureUrl || '').replace(NIL_UUID, '__UUID__');
  const SAVE_URL = (config.dataset.saveUrl || '').replace(NIL_UUID, '__UUID__');
  const SIGNIN_URL = config.dataset.signinUrl || '';
  const SAVE_ELIGIBLE = config.dataset.saveEligible === 'true';

  const SOURCE = 'ski-tours';
  const LINE = 'ski-tours-line';
  const HIGHLIGHT = 'ski-tours-highlight';
  const HIGHLIGHT_DOWN = 'ski-tours-highlight-down';
  const SUMMIT = 'ski-tours-summit';
  const LABEL = 'ski-tours-summit-label';
  // The listed tours' far ends, from a source of their own: points worked
  // out here (ski_tours_core.farEnds), not features the server sends.
  const ENDS_SOURCE = 'ski-tours-ends';
  const FAR_END = 'ski-tours-far-end';
  const LAYERS = [LINE, HIGHLIGHT, HIGHLIGHT_DOWN, SUMMIT, FAR_END, LABEL];
  // MapLibre paint cannot read a CSS custom property. The tours' blue is
  // swisstopo's own for its ski routes (blue-600); the highlight is a
  // route's two leg colours (legColours); the marks are the ink and the card.
  const TOUR_COLOUR = '#2563eb';
  const INK_COLOUR = '#1a1916';
  // The mockup's green dot at a tour's far end: --color-sync-ok (green-600).
  const FAR_END_COLOUR = '#16a34a';
  // While a list is open the tours it does not name step back.
  const DIMMED_OPACITY = 0.3;
  const HALO_COLOUR = '#ffffff';
  // A summit is a smaller target than a line: reached only close to it.
  const SUMMIT_TAP_PX = 12;
  const LOAD_DEBOUNCE_MS = 250;

  const STRINGS = self.pwaStrings.read('ski-tours-strings-template', {
    hint: 'Tap a summit or a route',
    'hint-zoom': 'Zoom in, then tap a summit or a route',
    loading: 'Loading routes…',
    failed: "The routes couldn't be loaded. Check your connection.",
    'sac-route': 'SAC route',
    'shows-ascent': 'Route shows the ascent only.',
    'shows-descent': 'Route shows the descent only.',
    'guide-time': '%(time)s (SAC guide time)',
    saved: 'Route saved.',
    'save-failed': "That route couldn't be saved. Try again.",
    'save-limit': "You've reached your saved-route limit. Delete one to save another.",
    'save-offline': 'You need to be online to save a route.',
  });
  const interpolate = self.pwaStrings.interpolate;

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
    /** The tours drawn in the route colour. */
    /** @type {Array<string>} */
    highlight: [],
    /** The tour the panel was asked for last, so a late answer is dropped. */
    /** @type {?string} */
    opening: null,
    /** @type {?AbortController} */
    listInflight: null,
    /**
     * The summit whose list is open when it was opened from search, so the
     * collection the flight there loads lights its tours up (SNOW-1294).
     */
    /** @type {?string} */
    summitKey: null,
  };

  const controller = window.MapSheet.attach(sheet, {
    onTeardown: function () {
      if (state.listInflight) state.listInflight.abort();
      state.listInflight = null;
      state.summitKey = null;
      setHighlight([]);
    },
  });

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
    for (const id of [LINE, HIGHLIGHT, HIGHLIGHT_DOWN, FAR_END]) {
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
   * A saved route's two leg colours, off the legs core, read when the
   * layers are added. The literals mirror `--color-route-rail-climb` and
   * `--color-route-rail-descent`.
   *
   * @returns {{climb: string, descent: string}}
   */
  function legColours() {
    const legs = self.pwaRouteLegsCore || {};
    return {
      climb: legs.LEG_CLIMB_COLOUR || '#64748b',
      descent: legs.LEG_DESCENT_COLOUR || '#c026d3',
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
    const highlight = core.highlightFilter(state.highlight);
    // Bold from z12, where the lines separate; thinner further out, where
    // the whole of a valley's tours share a few pixels.
    const width = ['interpolate', ['linear'], ['zoom'], 9, 1.5, 12, 3, 15, 5];
    map.addLayer({
      id: LINE,
      type: 'line',
      source: SOURCE,
      minzoom: minzoom,
      filter: isTour,
      layout: { visibility: visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': TOUR_COLOUR,
        'line-width': width,
        'line-opacity': state.highlight.length ? DIMMED_OPACITY : 1,
      },
    }, before);
    const highlightWidth = ['interpolate', ['linear'], ['zoom'], 9, 3, 12, 5, 15, 7];
    // SNOW-1286: a lit tour is drawn as a saved route's legs are — a way up
    // in the climb's grey, dashed, a way down in the descent's solid
    // fuchsia — so a tour reads the same before and after it is saved.
    const legs = legColours();
    map.addLayer({
      id: HIGHLIGHT,
      type: 'line',
      source: SOURCE,
      minzoom: minzoom,
      filter: ['all', isTour, ['!', ['get', 'down']], highlight],
      layout: { visibility: visibility, 'line-cap': 'butt', 'line-join': 'round' },
      paint: {
        'line-color': legs.climb,
        'line-width': highlightWidth,
        'line-dasharray': [2, 1.5],
      },
    }, before);
    map.addLayer({
      id: HIGHLIGHT_DOWN,
      type: 'line',
      source: SOURCE,
      minzoom: minzoom,
      filter: ['all', isTour, ['get', 'down'], highlight],
      layout: { visibility: visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': legs.descent, 'line-width': highlightWidth },
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
    // Over the summit and hut marks: a tour that starts at a hut is named
    // by its green dot, not hidden under the hut's ring.
    map.addSource(ENDS_SOURCE, {
      type: 'geojson',
      data: core.farEnds(state.data.features, state.highlight),
    });
    map.addLayer({
      id: FAR_END,
      type: 'circle',
      source: ENDS_SOURCE,
      minzoom: minzoom,
      layout: { visibility: visibility },
      paint: {
        'circle-radius': 5,
        'circle-color': FAR_END_COLOUR,
        'circle-stroke-color': HALO_COLOUR,
        'circle-stroke-width': 2,
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

  /**
   * Draw these tours in the route colour with their far ends, and dim the
   * rest; an empty list puts every tour back at full strength.
   *
   * @param {Array<string>} uuids
   */
  function setHighlight(uuids) {
    state.highlight = uuids.slice();
    const map = getMap();
    if (!map || !map.getLayer(HIGHLIGHT)) return;
    const isTour = ['==', ['get', 'kind'], 'tour'];
    const highlight = core.highlightFilter(state.highlight);
    map.setFilter(HIGHLIGHT, ['all', isTour, ['!', ['get', 'down']], highlight]);
    map.setFilter(HIGHLIGHT_DOWN, ['all', isTour, ['get', 'down'], highlight]);
    map.setPaintProperty(LINE, 'line-opacity', state.highlight.length ? DIMMED_OPACITY : 1);
    paintFarEnds(map);
  }

  /**
   * Re-place the listed tours' far ends, from the collection now loaded.
   *
   * @param {any} map
   */
  function paintFarEnds(map) {
    const source = map && map.getSource(ENDS_SOURCE);
    if (source) source.setData(core.farEnds(state.data.features, state.highlight));
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
    // A list opened from search before its summit's tours were loaded.
    if (state.summitKey) setHighlight(toursTo(state.summitKey));
    // A pan that reloads while a list is open keeps its dots in place.
    paintFarEnds(map);
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
        if (body && Array.isArray(body.features)) done(body);
      })
      .catch(function () {
        if (state.inflight === inflight) state.inflight = null;
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
   * The SAC line under the panel's title: "SAC route · PD+ · 3 h 30 (SAC
   * guide time)". SAC's height figures are left out (SNOW-1302): the panel's
   * figures line is the line's own, as a saved route's is.
   *
   * @param {Object<string, any>} props The tour Feature's properties.
   * @returns {string}
   */
  function sacLine(props) {
    const parts = [STRINGS['sac-route']];
    if (props.grade) parts.push(String(props.grade));
    if (props.sac_time) parts.push(interpolate(STRINGS['guide-time'], { time: props.sac_time }));
    return parts.join(' · ');
  }

  /**
   * Build the tour's block for the panel's claim slot.
   *
   * @param {Object<string, any>} props The tour Feature's properties.
   * @returns {HTMLElement}
   */
  function buildClaim(props) {
    const node = /** @type {HTMLElement} */ (
      /** @type {DocumentFragment} */ (claimTemplate.content.cloneNode(true)).firstElementChild
    );
    const sac = node.querySelector('[data-ski-tour-sac]');
    if (sac) sac.textContent = sacLine(props);
    // Which half of the tour SAC's line is, and nothing inferred beyond it.
    const shows = node.querySelector('[data-ski-tour-shows]');
    if (shows) shows.textContent = STRINGS[props.down ? 'shows-descent' : 'shows-ascent'];
    const guide = /** @type {HTMLAnchorElement|null} */ (node.querySelector('[data-ski-tour-guide]'));
    if (guide) {
      if (props.sac_url) {
        guide.href = String(props.sac_url);
      } else {
        guide.remove();
        node.querySelector('[data-ski-tour-guide-note]')?.remove();
        node.querySelector('[data-ski-tour-guide-see]')?.remove();
      }
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
    controller.close();
    showHint(false);
    fetchFeature(uuid, false).then(function (feature) {
      if (state.opening !== uuid) return;
      if (!feature) {
        window.MapSheet.toast(STRINGS.failed);
        return;
      }
      const claim = buildClaim(feature.properties || {});
      window.pwaSkiTourRoute.open(feature, { claim: claim, frame: true, shows: core.fitsView });
      if (!(feature.properties && feature.properties.terrain_pending)) return;
      fetchFeature(uuid, true).then(function (sampled) {
        if (!sampled || state.opening !== uuid) return;
        if (window.pwaSkiTourRoute.openUuid() !== uuid) return;
        window.pwaSkiTourRoute.open(sampled, { claim: claim });
      });
    });
  }

  // ---------------------------------------------------------------------------
  // The list sheet
  // ---------------------------------------------------------------------------

  /**
   * The tours a summit's list names, from what is loaded, for the
   * highlight (the list itself is the server's).
   *
   * @param {string} key
   * @returns {Array<string>}
   */
  function toursTo(key) {
    return state.data.features
      .filter(function (f) {
        return f && f.properties && f.properties.kind === 'tour' && f.properties.summit === key;
      })
      .map(function (f) {
        return String(f.properties.uuid);
      });
  }

  /** @param {string} message */
  function paintSheetLine(message) {
    const line = document.createElement('p');
    line.className = 'px-2 py-4 text-sm text-text-2';
    line.textContent = message;
    sheet.replaceChildren(line);
  }

  /**
   * Open the list sheet for a summit's or a tap's tours.
   *
   * @param {string} url
   * @param {Array<string>} uuids The tours to light up on the map.
   */
  function openList(url, uuids) {
    state.opening = null;
    state.summitKey = null;
    showHint(false);
    if (window.pwaRouteRail?.isOpen?.()) window.pwaRouteRail.close();
    if (state.listInflight) state.listInflight.abort();
    const inflight = new AbortController();
    state.listInflight = inflight;
    if (!controller.isOpen()) paintSheetLine(STRINGS.loading);
    controller.open({ awaiting: true });
    setHighlight(uuids);
    fetch(url, {
      credentials: 'same-origin',
      headers: { 'HX-Request': 'true' },
      signal: inflight.signal,
    })
      .then(function (resp) {
        return resp.ok ? resp.text() : null;
      })
      .then(function (html) {
        if (state.listInflight !== inflight) return;
        state.listInflight = null;
        // Our own server-rendered partial (routes:ski_tour_list), whose
        // every value went through Django's escaping.
        if (html === null) paintSheetLine(STRINGS.failed);
        else sheet.innerHTML = html;
        controller.open();
      })
      .catch(function () {
        if (state.listInflight !== inflight) return;
        state.listInflight = null;
        paintSheetLine(STRINGS.failed);
      });
  }

  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const row = target && target.closest ? target.closest('[data-ski-tour]') : null;
    if (!row) return;
    openTour(String(row.getAttribute('data-ski-tour')));
  });

  // ---------------------------------------------------------------------------
  // Taps and the way in
  // ---------------------------------------------------------------------------

  /**
   * Handle a map tap at a screen point.
   *
   * @param {{x: number, y: number}} point
   * @returns {boolean} Whether the tap was on a tour or a summit.
   */
  function tap(point) {
    const map = getMap();
    if (!map || !shown() || core.loadMode(map.getZoom()) === 'none' || !map.getLayer(LINE)) {
      return false;
    }
    /** @param {number} px */
    const box = (px) => [[point.x - px, point.y - px], [point.x + px, point.y + px]];
    const hits = map.queryRenderedFeatures(box(SUMMIT_TAP_PX), { layers: [SUMMIT, LABEL] })
      .concat(map.queryRenderedFeatures(box(core.TAP_PX), { layers: [LINE] }));
    const choice = core.resolveTap(hits);
    if (!choice) return false;
    if (choice.kind === 'tour') {
      openTour(choice.uuid);
    } else {
      const url = core.listUrl(LIST_URL, choice);
      if (url) openList(url, choice.kind === 'summit' ? toursTo(choice.key) : choice.uuids);
    }
    return true;
  }

  /**
   * A summit picked in search (SNOW-1294): what a tap on its dot does, from
   * anywhere on the map. Both parts of the ski-touring layer come on, as
   * "Find a route" turns them on; the map flies to the summit, close enough for its tours'
   * own detail; and its list opens. The tours it names are lit up once the
   * flight's load has them.
   *
   * @param {{key: string, lon: number, lat: number}} summit
   */
  function openSummit(summit) {
    const map = getMap();
    if (!summit || !summit.key || !map) return;
    // Both parts, as find() turns on: a summit with its tours hidden, or
    // tours with their summit hidden, is half an answer.
    window.pwaSkiRoutes?.show();
    const url = core.listUrl(LIST_URL, { kind: 'summit', key: summit.key });
    if (url) openList(url, toursTo(summit.key));
    state.summitKey = summit.key;
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

  // The tour the panel showed has gone: nothing is highlighted for it.
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
  });
})();
