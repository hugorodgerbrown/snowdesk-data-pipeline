/*
 * static/js/route_draw.js — draw a route on the map (SNOW-1256, behind the
 * `route_drawing` flag).
 *
 * The DOM and map half of route drawing; the pure half (the waypoint list,
 * its figures, the guide line, the stored draft) is route_draw_core.js.
 * The surface is public/partials/_route_draw_surface.html, which explains
 * the interaction: a still cross and "Add point" on a phone, a click with a
 * mouse, legs snapped to the ski-touring network or straight.
 *
 * THE LIFE OF A DRAFT
 *
 *   1. "Create a route" in the routes panel ([data-route-draw-start])
 *      closes that panel and opens #route-draw-sheet, a form panel.
 *   2. Every app overlay steps aside (window.PlacementFocus, which hides
 *      every GeoJSON layer that exists AT THAT MOMENT — so the draft's own
 *      layers are added after it, and stay). swisstopo's ski-touring routes
 *      — the map's own layer, a raster PlacementFocus leaves alone — are
 *      held on as a guide under the draft (window.pwaSkiRoutes.hold),
 *      whatever the layers menu says, and handed back on exit.
 *   3. Each added or removed point redraws the guide at once, stores the
 *      draft on the device, and — debounced, the previous request aborted
 *      — posts the waypoints to routes:draw_preview. Its answer is the
 *      Feature the routes layer draws, so the draft is drawn by
 *      route_drawing_core.js and profiled by route_profile.js, exactly as
 *      the saved route will be.
 *   4. Save posts to routes:create_drawn, then hands over to what an upload
 *      does: `snowdesk:routes-changed` with `uploaded: true` (the map
 *      re-reads the layer once the terrain task has run), and the routes
 *      layer revealed and framed on the new route (window.pwaRowFocus).
 *
 * FOLLOWING THE SKI ROUTES (SNOW-1277). While "Follow ski routes" is on
 * and the map is at z12 or closer, the panel loads swisstopo's ski-touring
 * network for the view (/api/ski-network.geojson) and draws it as a faint
 * shadow under the draft; swisstopo's raster steps aside, since the shadow
 * is the same lines. A tap within 24 px of a line snaps to it, and a ring
 * shows where it would land before it is placed. When the last point is on
 * the network too, the new leg is routed along it (ski_snap_core.js, the
 * shortest way, straight instead when that is over three times as far).
 * One tap is one leg, however many points it adds: Undo takes back the
 * whole leg, and the draft stores which points were taps.
 *
 * Closing the panel by any route — the ×, Escape, another panel opening —
 * ends draw mode and keeps the draft, so an anonymous drawer who signs in
 * to save comes back to the line they drew. "Start over" is the only thing
 * that throws a draft away.
 *
 * map.js's click dispatcher returns early while `isActive()` is true, so a
 * tap that adds a waypoint never also opens the region under it.
 *
 * Publishes (frozen `window.pwaRouteDraw`):
 *   isActive() — whether the map is in draw mode
 *   start()    — enter draw mode, resuming any stored draft
 */

(function routeDrawInit() {
  'use strict';

  const sheet = document.getElementById('route-draw-sheet');
  const template = /** @type {HTMLTemplateElement|null} */ (
    document.getElementById('route-draw-template')
  );
  const config = document.getElementById('route-draw-config');
  const crosshair = document.getElementById('route-draw-crosshair');
  const core = self.pwaRouteDrawCore;
  const snapCore = self.pwaSkiSnapCore || null;
  if (!sheet || !template || !config || !crosshair || !core || !window.MapSheet) return;

  const PREVIEW_URL = config.dataset.previewUrl || '';
  const CREATE_URL = config.dataset.createUrl || '';
  const SIGNIN_URL = config.dataset.signinUrl || '';
  const SAVE_ELIGIBLE = config.dataset.saveEligible === 'true';
  const NETWORK_URL = (snapCore && config.dataset.networkUrl) || '';

  const PREFIX = 'route-draft';
  const GUIDE_SOURCE = PREFIX + '-guide';
  // The route colour (--color-route-line) and the white halo the map's
  // markers use; MapLibre paint cannot read a CSS custom property.
  const LINE_COLOUR =
    (self.pwaRouteDrawingCore && self.pwaRouteDrawingCore.ROUTE_LINE_COLOUR) || '#c026d3';
  const HALO_COLOUR = '#ffffff';
  const PREVIEW_DEBOUNCE_MS = 350;
  const NETWORK_SOURCE = PREFIX + '-network';
  // The network is a shadow to snap to, fainter than anything drawn on it
  // (Hugo, 28 Oct 2026): a dark neutral, thin and mostly transparent.
  const NETWORK_COLOUR = '#1e293b';
  // The network is loaded and snapped to from this zoom: a view wider than
  // /api/ski-network.geojson's half-degree cap is refused, and a snap
  // reach of 24 px is over a kilometre further out.
  const NETWORK_MINZOOM = 12;
  // Just inside the endpoint's 0.5° cap, after rounding to 3 places.
  const NETWORK_MAX_HALF_SPAN = 0.249;
  const NETWORK_DEBOUNCE_MS = 250;
  // Whether the drawer turned following off; on unless they did.
  const FOLLOW_KEY = 'snowdesk.route-draw-follow';

  const STRINGS = self.pwaStrings.read('route-draw-strings-template', {
    'hint-coarse': 'Move the map to put the cross where you want the next point, then tap Add point.',
    'hint-fine': 'Click the map to add a point. Near a ski route it snaps to the route.',
    'stats-empty': 'No points yet.',
    'stats-one': 'One point — add another to start the line.',
    'unit-km': '%(value)s km',
    climb: '↑ %(value)s m',
    descent: '↓ %(value)s m',
    'estimate-hm': '≈ %(hours)s h %(minutes)s',
    'estimate-h': '≈ %(hours)s h',
    'estimate-m': '≈ %(minutes)s min',
    'no-terrain': 'No terrain data here, so no profile, climb or time. Snowdesk models the ground in Switzerland only.',
    'preview-failed': "The profile couldn't be worked out. Check your connection.",
    'preview-rejected': "That line can't be drawn — it may be too long.",
    saved: 'Route saved.',
    'save-failed': "That route couldn't be saved. Try again.",
    'save-limit': "You've reached your saved-route limit. Delete one to save another.",
    'save-offline': 'You need to be online to save a route.',
    'default-name': 'Drawn route',
  });
  const PROFILE_STRINGS = self.pwaStrings.read('route-profile-strings-template', {
    'unit-m': '%(value)s m',
    'unit-km': '%(value)s km',
    'lane-label': 'Elevation profile of %(name)s',
  });
  const interpolate = self.pwaStrings.interpolate;

  /** Whether waypoints are placed under the still cross (a phone). */
  const coarse = window.matchMedia ? window.matchMedia('(pointer: coarse)') : null;

  const state = {
    active: false,
    /** @type {Array<[number, number]>} */
    points: [],
    /** The index of each tap's last point (route_draw_core.js). */
    /** @type {Array<number>} */
    taps: [],
    name: '',
    /** Whether a tap near a ski route snaps to it. */
    follow: readFollow(),
    /** The network segments loaded so far, by id. */
    /** @type {Map<string, any>} */
    network: new Map(),
    /** @type {?ReturnType<NonNullable<typeof snapCore>['buildGraph']>} */
    graph: null,
    /** The rectangles already loaded, [w, s, e, n]. */
    /** @type {Array<Array<number>>} */
    loaded: [],
    /** @type {?AbortController} */
    networkInflight: null,
    /** @type {?number} */
    networkTimer: null,
    /** The last preview Feature, matching `previewFor`, or null. */
    feature: null,
    /** The waypoints `feature` describes, serialised. */
    previewFor: '',
    /** The mouse's position, for the dashed next leg, or null. */
    hover: null,
    /** @type {?AbortController} */
    inflight: null,
    /** @type {?number} */
    timer: null,
    /** @type {?Object} */
    profile: null,
    saving: false,
  };

  const controller = window.MapSheet.attach(sheet, {
    triggerSelector: '[data-route-draw-start]',
    onTeardown: exitMode,
  });

  // ---------------------------------------------------------------------------
  // The map
  // ---------------------------------------------------------------------------

  /** @returns {boolean} The stored follow preference; on unless turned off. */
  function readFollow() {
    try {
      return localStorage.getItem(FOLLOW_KEY) !== 'off';
    } catch (_err) {
      return true;
    }
  }

  /** @returns {any} The MapLibre map, or null before it exists. */
  function getMap() {
    return (window.snowdeskMapState && window.snowdeskMapState.map) || null;
  }

  /** Whether points are placed under the cross rather than by a click. */
  function usesCrosshair() {
    return Boolean(coarse && coarse.matches);
  }

  /**
   * Add the draft's sources and layers: the drawn preview at the bottom
   * (over the map's ski-routes layer), the waypoints and straight guide on
   * top.
   *
   * @param {any} map
   */
  function installLayers(map) {
    if (NETWORK_URL && !map.getSource(NETWORK_SOURCE)) {
      map.addSource(NETWORK_SOURCE, { type: 'geojson', data: networkCollection() });
      map.addLayer({
        id: NETWORK_SOURCE + '-line',
        type: 'line',
        source: NETWORK_SOURCE,
        minzoom: NETWORK_MINZOOM,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': NETWORK_COLOUR, 'line-width': 1.5, 'line-opacity': 0.3 },
      });
    }
    if (self.pwaRouteDrawingCore) {
      self.pwaRouteDrawingCore.install(map, null, { prefix: PREFIX });
    }
    if (!map.getSource(GUIDE_SOURCE)) {
      map.addSource(GUIDE_SOURCE, { type: 'geojson', data: core.guideCollection([], null) });
      map.addLayer({
        id: GUIDE_SOURCE + '-line',
        type: 'line',
        source: GUIDE_SOURCE,
        filter: ['==', ['get', 'role'], 'line'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': LINE_COLOUR, 'line-width': 2, 'line-dasharray': [2, 1.5] },
      });
      map.addLayer({
        id: GUIDE_SOURCE + '-pending',
        type: 'line',
        source: GUIDE_SOURCE,
        filter: ['==', ['get', 'role'], 'pending'],
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': LINE_COLOUR,
          'line-width': 2,
          'line-opacity': 0.6,
          'line-dasharray': [1, 2],
        },
      });
      map.addLayer({
        id: GUIDE_SOURCE + '-snap',
        type: 'circle',
        source: GUIDE_SOURCE,
        filter: ['==', ['get', 'role'], 'snap'],
        // Wider than the phone's cross (a 9 px ring of its own), so it
        // still reads when the cross sits right on the line.
        paint: {
          'circle-radius': 14,
          'circle-color': 'rgba(0, 0, 0, 0)',
          'circle-stroke-color': LINE_COLOUR,
          'circle-stroke-width': 2,
        },
      });
      map.addLayer({
        id: GUIDE_SOURCE + '-points',
        type: 'circle',
        source: GUIDE_SOURCE,
        filter: ['==', ['get', 'role'], 'waypoint'],
        paint: {
          'circle-radius': ['case', ['get', 'first'], 7, 5],
          'circle-color': HALO_COLOUR,
          'circle-stroke-color': LINE_COLOUR,
          'circle-stroke-width': 2.5,
        },
      });
    }
  }

  /**
   * Remove every layer and source this module added.
   *
   * @param {any} map
   */
  function removeLayers(map) {
    let style;
    try {
      style = map.getStyle();
    } catch (_err) {
      return;
    }
    if (!style) return;
    (style.layers || [])
      .filter(function (layer) {
        return layer.id.indexOf(PREFIX) === 0;
      })
      .forEach(function (layer) {
        map.removeLayer(layer.id);
      });
    Object.keys(style.sources || {})
      .filter(function (id) {
        return id.indexOf(PREFIX) === 0;
      })
      .forEach(function (id) {
        map.removeSource(id);
      });
  }

  /** Redraw the taps, the line through the points and the snap ring. */
  function drawGuide() {
    const map = getMap();
    const source = map && map.getSource(GUIDE_SOURCE);
    if (!source) return;
    const cursor = usesCrosshair() ? crosshairLngLat() : state.hover;
    const snap = cursor ? snapFor(cursor) : null;
    source.setData(
      core.guideCollection(state.points, cursor, {
        taps: state.taps,
        snap: snap ? snap.point : null,
      }),
    );
  }

  // ---------------------------------------------------------------------------
  // The ski-touring network
  // ---------------------------------------------------------------------------

  /** @returns {boolean} Whether a tap now snaps: on, loaded and zoomed in. */
  function following() {
    const map = getMap();
    return Boolean(
      state.follow && state.graph && snapCore && map && map.getZoom() >= NETWORK_MINZOOM,
    );
  }

  /** @returns {number} The snap reach in metres at the map's middle and zoom. */
  function snapReach() {
    const map = getMap();
    if (!map || !snapCore) return 0;
    return snapCore.SNAP_PX * snapCore.metresPerPixel(map.getCenter().lat, map.getZoom());
  }

  /**
   * Where a tap at `point` would land on the network, or null.
   *
   * @param {[number, number]} point
   * @returns {?{point: [number, number]}}
   */
  function snapFor(point) {
    if (!following() || !snapCore || !state.graph) return null;
    return snapCore.nearestOnNetwork(state.graph, point, snapReach());
  }

  /** @returns {Object} The loaded network, or nothing while following is off. */
  function networkCollection() {
    return {
      type: 'FeatureCollection',
      features: state.follow ? Array.from(state.network.values()) : [],
    };
  }

  /**
   * Whether the network loaded so far covers the whole view.
   *
   * @param {any} map
   * @returns {boolean}
   */
  function networkCoversView(map) {
    const b = map.getBounds();
    return state.loaded.some(function (done) {
      return done[0] <= b.getWest() && done[1] <= b.getSouth()
        && done[2] >= b.getEast() && done[3] >= b.getNorth();
    });
  }

  /**
   * Redraw the network, and let swisstopo's raster step aside under it —
   * only once the shadow covers the view, so a failed or refused load
   * (offline, with the raster's tiles cached) leaves the raster as the guide.
   */
  function drawNetwork() {
    const map = getMap();
    const source = map && map.getSource(NETWORK_SOURCE);
    if (source) source.setData(networkCollection());
    const shadow = Boolean(
      NETWORK_URL && state.follow && map && map.getZoom() >= NETWORK_MINZOOM
        && networkCoversView(map),
    );
    window.pwaSkiRoutes?.hide(shadow);
  }

  /** Load the network for the view once the map has stopped moving. */
  function scheduleNetwork() {
    if (state.networkTimer !== null) clearTimeout(state.networkTimer);
    state.networkTimer = window.setTimeout(loadNetwork, NETWORK_DEBOUNCE_MS);
  }

  /**
   * Fetch the segments crossing the view, padded by a quarter and held to
   * the endpoint's cap, unless a rectangle already loaded covers it.
   */
  function loadNetwork() {
    state.networkTimer = null;
    const map = getMap();
    if (!map || !state.active || !state.follow || !NETWORK_URL || !snapCore) return;
    if (map.getZoom() < NETWORK_MINZOOM || !online()) return;
    const bounds = map.getBounds();
    const centre = map.getCenter();
    const padX = (bounds.getEast() - bounds.getWest()) / 4;
    const padY = (bounds.getNorth() - bounds.getSouth()) / 4;
    const box = [
      Math.max(bounds.getWest() - padX, centre.lng - NETWORK_MAX_HALF_SPAN),
      Math.max(bounds.getSouth() - padY, centre.lat - NETWORK_MAX_HALF_SPAN),
      Math.min(bounds.getEast() + padX, centre.lng + NETWORK_MAX_HALF_SPAN),
      Math.min(bounds.getNorth() + padY, centre.lat + NETWORK_MAX_HALF_SPAN),
    ].map(function (value) {
      return Math.round(value * 1000) / 1000;
    });
    const covered = state.loaded.some(function (done) {
      return done[0] <= box[0] && done[1] <= box[1] && done[2] >= box[2] && done[3] >= box[3];
    });
    if (covered) return;
    if (state.networkInflight) state.networkInflight.abort();
    const abort = new AbortController();
    state.networkInflight = abort;
    fetch(NETWORK_URL + '?bbox=' + box.join(','), { signal: abort.signal })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (body) {
        if (!body || !Array.isArray(body.features) || !state.active) return;
        body.features.forEach(function (/** @type {any} */ feature) {
          state.network.set(String(feature.id), feature);
        });
        state.graph = snapCore.buildGraph(Array.from(state.network.values()));
        if (!body.truncated) state.loaded.push(box);
        drawNetwork();
        drawGuide();
      })
      .catch(function () {
        // Offline or refused: the drawer draws straight lines meanwhile.
      })
      .finally(function () {
        if (state.networkInflight === abort) state.networkInflight = null;
      });
  }

  /**
   * Turn following on or off, remember it, and redraw what reads it.
   *
   * @param {boolean} on
   */
  function setFollow(on) {
    state.follow = on;
    try {
      localStorage.setItem(FOLLOW_KEY, on ? 'on' : 'off');
    } catch (_err) {
      // Private mode: the choice lasts for this visit.
    }
    const toggle = find('[data-route-draw-follow]');
    if (toggle) toggle.setAttribute('aria-checked', on ? 'true' : 'false');
    drawNetwork();
    drawGuide();
    if (on) scheduleNetwork();
  }

  /** Draw the last preview Feature, or nothing, through the shared drawing. */
  function drawPreview() {
    const map = getMap();
    const drawing = self.pwaRouteDrawingCore;
    if (!map || !drawing) return;
    const collection = {
      type: 'FeatureCollection',
      features: state.feature ? [state.feature] : [],
    };
    const ids = drawing.sourceIds(PREFIX);
    const data = drawing.sourceData(collection);
    Object.keys(ids).forEach(function (key) {
      const source = map.getSource(ids[key]);
      if (source) source.setData(data[key]);
    });
  }

  // ---------------------------------------------------------------------------
  // The cross
  // ---------------------------------------------------------------------------

  /**
   * Put the cross in the middle of the map the panel leaves showing: above
   * a bottom-docked panel on a phone, the map's own middle otherwise.
   */
  function placeCrosshair() {
    const map = getMap();
    if (!map) return;
    const container = map.getContainer();
    const box = container.getBoundingClientRect();
    const panel = sheet.getBoundingClientRect();
    let y = box.height / 2;
    const docked = !sheet.hasAttribute('hidden') && panel.width > box.width * 0.8;
    if (docked && panel.top > box.top) y = (panel.top - box.top) / 2;
    crosshair.style.setProperty('--route-draw-y', Math.round(y) + 'px');
  }

  /** @returns {?[number, number]} The ground under the cross. */
  function crosshairLngLat() {
    const map = getMap();
    if (!map || crosshair.hidden) return null;
    const container = map.getContainer().getBoundingClientRect();
    const mark = crosshair.getBoundingClientRect();
    const lngLat = map.unproject([
      mark.left + mark.width / 2 - container.left,
      mark.top + mark.height / 2 - container.top,
    ]);
    return [lngLat.lng, lngLat.lat];
  }

  // ---------------------------------------------------------------------------
  // The panel
  // ---------------------------------------------------------------------------

  /**
   * @param {string} selector
   * @returns {any}
   */
  function find(selector) {
    return sheet.querySelector(selector);
  }

  /** Render the panel's body from its template and bind it. */
  function renderPanel() {
    sheet.replaceChildren(template.content.cloneNode(true));
    const hint = find('[data-route-draw-hint]');
    if (hint) hint.textContent = STRINGS[usesCrosshair() ? 'hint-coarse' : 'hint-fine'];
    const add = find('[data-route-draw-add]');
    if (add) add.hidden = !usesCrosshair();
    const toggle = find('[data-route-draw-follow]');
    if (toggle) {
      toggle.hidden = !NETWORK_URL;
      toggle.setAttribute('aria-checked', state.follow ? 'true' : 'false');
    }
    const name = find('#route-draw-name');
    if (name) {
      name.value = state.name;
      name.addEventListener('input', function () {
        state.name = name.value;
        storeDraft();
      });
    }
    const profileRoot = find('[data-route-draw-profile]');
    state.profile = profileRoot && self.pwaRouteProfile
      ? self.pwaRouteProfile.mount(profileRoot, PROFILE_STRINGS)
      : null;
    renderFigures();
  }

  /** Write the figures, the profile and the buttons' states. */
  function renderFigures() {
    const stats = find('[data-route-draw-stats]');
    const notice = find('[data-route-draw-notice]');
    const profileRoot = find('[data-route-draw-profile]');
    const fresh = state.feature && state.previewFor === JSON.stringify(state.points);
    const parts = core.statsParts(fresh ? state.feature.properties : null, state.points);

    if (stats) stats.textContent = statsLine(parts);
    if (notice && fresh) {
      notice.hidden = parts.terrain;
      if (!parts.terrain) notice.textContent = STRINGS['no-terrain'];
    }
    if (profileRoot) {
      const drawable = fresh && parts.terrain && state.profile;
      profileRoot.hidden = !drawable;
      if (drawable) {
        const read = self.pwaRouteProfile.read(state.feature);
        state.profile.draw(read.profile, read.legs, read.sampleCount, read.spanM, nameOrDefault());
      }
    }

    const hint = find('[data-route-draw-hint]');
    if (hint) hint.hidden = state.points.length > 0;

    const undo = find('[data-route-draw-undo]');
    if (undo) undo.disabled = state.points.length === 0;
    const clear = find('[data-route-draw-clear]');
    if (clear) clear.disabled = state.points.length === 0;
    const submit = find('[data-route-draw-submit]');
    if (submit) submit.disabled = state.points.length < 2 || state.saving;
  }

  /**
   * The one figures line: distance, climb, descent and the estimate.
   *
   * @param {ReturnType<typeof core.statsParts>} parts
   * @returns {string}
   */
  function statsLine(parts) {
    if (parts.count === 0) return STRINGS['stats-empty'];
    if (parts.count === 1) return STRINGS['stats-one'];
    const out = [interpolate(STRINGS['unit-km'], { value: core.formatKm(parts.distanceM) })];
    if (parts.ascentM !== null) out.push(interpolate(STRINGS.climb, { value: Math.round(parts.ascentM) }));
    if (parts.descentM !== null) out.push(interpolate(STRINGS.descent, { value: Math.round(parts.descentM) }));
    if (parts.estimateS !== null) {
      const d = core.durationParts(parts.estimateS);
      let key = 'estimate-hm';
      if (d.hours === 0) key = 'estimate-m';
      else if (d.minutes === '00') key = 'estimate-h';
      out.push(interpolate(STRINGS[key], d));
    }
    return out.join(' · ');
  }

  /** @returns {string} The draft's name, or the label a nameless one gets. */
  function nameOrDefault() {
    return state.name.trim() || STRINGS['default-name'];
  }

  // ---------------------------------------------------------------------------
  // The draft
  // ---------------------------------------------------------------------------

  /** Keep the draft on this device, or forget it once it is empty. */
  function storeDraft() {
    try {
      if (state.points.length === 0) localStorage.removeItem(core.DRAFT_KEY);
      else {
        localStorage.setItem(
          core.DRAFT_KEY,
          core.serialiseDraft(state.points, state.name, state.taps),
        );
      }
    } catch (_err) {
      // Private mode or a full store: the draft lives for this visit only.
    }
  }

  /** Read back a stored draft into state, if there is one. */
  function restoreDraft() {
    let raw = null;
    try {
      raw = localStorage.getItem(core.DRAFT_KEY);
    } catch (_err) {
      raw = null;
    }
    const draft = core.parseDraft(raw);
    state.points = draft ? draft.points : [];
    state.taps = draft ? draft.taps : [];
    state.name = draft ? draft.name : '';
  }

  /**
   * Replace the line and its taps and redraw everything that reads them.
   *
   * @param {{points: Array<[number, number]>, taps: Array<number>}} next
   */
  function setLine(next) {
    if (next.points === state.points) return;
    state.points = next.points;
    state.taps = next.taps;
    storeDraft();
    drawGuide();
    renderFigures();
    schedulePreview();
  }

  // ---------------------------------------------------------------------------
  // The server
  // ---------------------------------------------------------------------------

  /** @returns {string} The page's CSRF token. */
  function csrfToken() {
    const input = /** @type {HTMLInputElement|null} */ (
      config.querySelector('input[name="csrfmiddlewaretoken"]')
    );
    return input ? input.value : '';
  }

  /** @returns {boolean} Whether the device thinks it is online. */
  function online() {
    const connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
  }

  /** Ask for a preview once the points have stopped changing. */
  function schedulePreview() {
    if (state.timer !== null) clearTimeout(state.timer);
    if (state.inflight) state.inflight.abort();
    state.inflight = null;
    if (state.points.length < 2) {
      state.feature = null;
      state.previewFor = '';
      drawPreview();
      renderFigures();
      return;
    }
    state.timer = window.setTimeout(requestPreview, PREVIEW_DEBOUNCE_MS);
  }

  /** Post the waypoints to routes:draw_preview and draw its answer. */
  function requestPreview() {
    state.timer = null;
    if (!PREVIEW_URL || !online()) return;
    const key = JSON.stringify(state.points);
    const abort = new AbortController();
    state.inflight = abort;
    fetch(PREVIEW_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ points: state.points }),
      signal: abort.signal,
    })
      .then(function (resp) {
        if (!resp.ok) {
          showNotice(resp.status === 400 ? STRINGS['preview-rejected'] : STRINGS['preview-failed']);
          return null;
        }
        return resp.json();
      })
      .then(function (body) {
        if (!body || !body.feature || !state.active) return;
        if (JSON.stringify(state.points) !== key) return;
        state.feature = body.feature;
        state.previewFor = key;
        drawPreview();
        renderFigures();
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        showNotice(STRINGS['preview-failed']);
      })
      .finally(function () {
        if (state.inflight === abort) state.inflight = null;
      });
  }

  /** @param {string} message */
  function showNotice(message) {
    const notice = find('[data-route-draw-notice]');
    if (!notice) return;
    notice.textContent = message;
    notice.hidden = false;
  }

  /** Save the draft as a route on this account. */
  function save() {
    if (!SAVE_ELIGIBLE) {
      if (SIGNIN_URL) window.location.assign(SIGNIN_URL);
      return;
    }
    if (state.saving || state.points.length < 2 || !CREATE_URL) return;
    if (!online()) {
      window.MapSheet.toast(STRINGS['save-offline']);
      return;
    }
    state.saving = true;
    renderFigures();
    fetch(CREATE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ points: state.points, name: nameOrDefault() }),
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
        state.saving = false;
        if (!body) {
          renderFigures();
          return;
        }
        state.points = [];
        state.taps = [];
        state.name = '';
        state.feature = null;
        storeDraft();
        controller.close();
        window.pwaTelemetry?.emit('map.route.created', { drawn: true });
        // What an upload does once it lands (routes.js's upload): the map
        // re-reads the layer, then shows and frames the new route.
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

  // ---------------------------------------------------------------------------
  // Entering and leaving
  // ---------------------------------------------------------------------------

  /**
   * Add one tap at `point`: snapped and routed along the network when
   * following, a straight leg otherwise.
   *
   * @param {[number, number]} point
   */
  function addTap(point) {
    const last = state.points.length ? state.points[state.points.length - 1] : null;
    const leg = following() && snapCore && state.graph
      ? snapCore.legFor(state.graph, last, point, snapReach())
      : { points: [point] };
    setLine(core.addLeg(state.points, state.taps, leg.points));
  }

  /** @param {any} event A MapLibre click. */
  function onMapClick(event) {
    if (!state.active || usesCrosshair()) return;
    addTap([event.lngLat.lng, event.lngLat.lat]);
  }

  /** @param {any} event A MapLibre mousemove. */
  function onMapHover(event) {
    if (!state.active || usesCrosshair()) return;
    state.hover = [event.lngLat.lng, event.lngLat.lat];
    drawGuide();
  }

  /** While the map moves under the cross, the next leg follows it. */
  function onMapMove() {
    if (state.active && usesCrosshair()) drawGuide();
  }

  /** Once the map stops, load the network for where it stopped. */
  function onMapMoveEnd() {
    if (!state.active) return;
    drawNetwork();
    scheduleNetwork();
  }

  function onResize() {
    if (state.active) placeCrosshair();
  }

  /** Enter draw mode, resuming any stored draft. */
  function start() {
    const map = getMap();
    if (!map || state.active) return;
    window.pwaRoutesSheet?.close();
    state.active = true;
    document.documentElement.setAttribute('data-route-drawing', '');
    restoreDraft();
    state.feature = null;
    state.previewFor = '';
    state.hover = null;

    controller.open();
    renderPanel();

    // Overlays step aside FIRST, so the draft's own layers, added next,
    // are not among the layers PlacementFocus hides.
    window.PlacementFocus?.enter();
    installLayers(map);
    window.pwaSkiRoutes?.hold(true);
    drawNetwork();

    const container = map.getContainer();
    if (crosshair.parentElement !== container) container.appendChild(crosshair);
    crosshair.hidden = !usesCrosshair();
    placeCrosshair();

    map.on('click', onMapClick);
    map.on('mousemove', onMapHover);
    map.on('move', onMapMove);
    map.on('moveend', onMapMoveEnd);
    window.addEventListener('resize', onResize);

    drawGuide();
    schedulePreview();
    scheduleNetwork();
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
  }

  /** Leave draw mode. The draft stays stored. */
  function exitMode() {
    if (!state.active) return;
    state.active = false;
    document.documentElement.removeAttribute('data-route-drawing');
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = null;
    if (state.inflight) state.inflight.abort();
    state.inflight = null;
    if (state.networkTimer !== null) clearTimeout(state.networkTimer);
    state.networkTimer = null;
    if (state.networkInflight) state.networkInflight.abort();
    state.networkInflight = null;
    state.profile = null;
    crosshair.hidden = true;
    window.removeEventListener('resize', onResize);
    const map = getMap();
    if (map) {
      map.off('click', onMapClick);
      map.off('mousemove', onMapHover);
      map.off('move', onMapMove);
      map.off('moveend', onMapMoveEnd);
      removeLayers(map);
    }
    window.pwaSkiRoutes?.hide(false);
    window.pwaSkiRoutes?.hold(false);
    window.PlacementFocus?.exit();
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: false } }));
  }

  // Bound AFTER MapSheet.attach registered its own click-outside handler,
  // so that handler runs first on the click that opens the panel and sees
  // it still shut.
  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('[data-route-draw-start]')) {
      start();
      return;
    }
    if (!sheet.contains(target)) return;
    if (target.closest('[data-route-draw-add]')) {
      const point = crosshairLngLat();
      if (point) addTap(point);
      return;
    }
    if (target.closest('[data-route-draw-follow]')) {
      setFollow(!state.follow);
      return;
    }
    if (target.closest('[data-route-draw-undo]')) {
      setLine(core.undoLeg(state.points, state.taps));
      return;
    }
    if (target.closest('[data-route-draw-clear]')) {
      state.name = '';
      const name = find('#route-draw-name');
      if (name) name.value = '';
      setLine({ points: [], taps: [] });
    }
  });

  sheet.addEventListener('submit', function (event) {
    if (!/** @type {HTMLElement} */ (event.target).closest('[data-route-draw-save]')) return;
    event.preventDefault();
    save();
  });

  window.pwaRouteDraw = Object.freeze({
    isActive: function () {
      return state.active;
    },
    start: start,
  });
})();
