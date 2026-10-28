/*
 * static/js/route_draw.js — draw a route on the map (SNOW-1256, behind the
 * `route_drawing` flag).
 *
 * The DOM and map half of route drawing; the pure half (the waypoint list,
 * its figures, the guide line, the stored draft) is route_draw_core.js.
 * The surface is public/partials/_route_draw_surface.html, which explains
 * the interaction: a still cross and "Add point" on a phone, a click with a
 * mouse, waypoints joined by straight lines.
 *
 * THE LIFE OF A DRAFT
 *
 *   1. "Draw on the map" in the routes panel ([data-route-draw-start])
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
  if (!sheet || !template || !config || !crosshair || !core || !window.MapSheet) return;

  const PREVIEW_URL = config.dataset.previewUrl || '';
  const CREATE_URL = config.dataset.createUrl || '';
  const SIGNIN_URL = config.dataset.signinUrl || '';
  const SAVE_ELIGIBLE = config.dataset.saveEligible === 'true';

  const PREFIX = 'route-draft';
  const GUIDE_SOURCE = PREFIX + '-guide';
  // The route colour (--color-route-line) and the white halo the map's
  // markers use; MapLibre paint cannot read a CSS custom property.
  const LINE_COLOUR =
    (self.pwaRouteDrawingCore && self.pwaRouteDrawingCore.ROUTE_LINE_COLOUR) || '#c026d3';
  const HALO_COLOUR = '#ffffff';
  const PREVIEW_DEBOUNCE_MS = 350;

  const STRINGS = self.pwaStrings.read('route-draw-strings-template', {
    'hint-coarse': 'Move the map to put the cross where you want the next point, then tap Add point.',
    'hint-fine': 'Click the map to add a point. Points are joined by straight lines.',
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
    name: '',
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

  /** Redraw the waypoints and the straight guide through them. */
  function drawGuide() {
    const map = getMap();
    const source = map && map.getSource(GUIDE_SOURCE);
    if (!source) return;
    const cursor = usesCrosshair() ? crosshairLngLat() : state.hover;
    source.setData(core.guideCollection(state.points, cursor));
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
      else localStorage.setItem(core.DRAFT_KEY, core.serialiseDraft(state.points, state.name));
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
    state.name = draft ? draft.name : '';
  }

  /**
   * Replace the waypoints and redraw everything that reads them.
   *
   * @param {Array<[number, number]>} next
   */
  function setPoints(next) {
    if (next === state.points) return;
    state.points = next;
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

  /** @param {any} event A MapLibre click. */
  function onMapClick(event) {
    if (!state.active || usesCrosshair()) return;
    setPoints(core.addPoint(state.points, event.lngLat.lng, event.lngLat.lat));
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

    const container = map.getContainer();
    if (crosshair.parentElement !== container) container.appendChild(crosshair);
    crosshair.hidden = !usesCrosshair();
    placeCrosshair();

    map.on('click', onMapClick);
    map.on('mousemove', onMapHover);
    map.on('move', onMapMove);
    window.addEventListener('resize', onResize);

    drawGuide();
    schedulePreview();
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
    state.profile = null;
    crosshair.hidden = true;
    window.removeEventListener('resize', onResize);
    const map = getMap();
    if (map) {
      map.off('click', onMapClick);
      map.off('mousemove', onMapHover);
      map.off('move', onMapMove);
      removeLayers(map);
    }
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
      if (point) setPoints(core.addPoint(state.points, point[0], point[1]));
      return;
    }
    if (target.closest('[data-route-draw-undo]')) {
      setPoints(core.undo(state.points));
      return;
    }
    if (target.closest('[data-route-draw-clear]')) {
      state.name = '';
      const name = find('#route-draw-name');
      if (name) name.value = '';
      setPoints([]);
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
