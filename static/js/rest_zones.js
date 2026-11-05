/*
 * static/js/rest_zones.js — the "Wildlife rest zones" map layer (SNOW-1384,
 * behind the `route_drawing` flag).
 *
 * The DOM and map half; the pure half (the rectangle a view loads, the URL,
 * the paint, which zone a tap opens) is rest_zones_core.js. The surface is
 * public/partials/_rest_zones_surface.html: the config, the sheet, its body
 * and its strings.
 *
 * WHAT IS DRAWN. FOEN's wildlife rest zones (RestZone, synced weekly from
 * the reference-data service), from z10, for the view's own rectangle and
 * the map's day: the server marks each zone `in_force` for that day, and a
 * zone out of its period is drawn muted rather than left off. Two layers,
 * `rest-zones-fill` and `rest-zones-line` (map_state.js OVERLAY_LAYERS),
 * under the region boundaries and every pin.
 *
 * WHEN IT LOADS. After the map settles (`moveend`, a quarter-second
 * debounce), only while the switch is on and the map is at z10 or closer,
 * and only when the loaded rectangle no longer holds the view or the map's
 * day changed (`snowdesk:date-changed`, never the scrubber's preview). A
 * newer request aborts the one in flight. A failed request leaves the last
 * zones drawn and says so on the console only: the layer is an aid, and a
 * toast for every dropped request in a valley with no signal would be noise.
 *
 * THE SWITCH. The layers menu's row (key `rest_zones`) toggles through this
 * module's bridge (map_basemap_picker.js OVERLAY_BRIDGES), which persists
 * the choice under OVERLAY_STORAGE_KEY.rest_zones. Off unless switched on.
 *
 * A TAP. map.js's dispatcher calls `tap(point)` after the markers, the
 * weather symbols, the tours and an open route, and only when no resort pin
 * is under the tap; a tap inside a zone opens its sheet and takes the tap,
 * so the region under it is not selected as well.
 *
 * Publishes (frozen `window.pwaRestZones`):
 *   tap(point)   — handle a map tap at a screen point; true when taken
 *   show()       — the switch on
 *   hide()       — the switch off
 *   isEnabled()  — whether the switch is on
 *   MINZOOM      — the zoom the zones are drawn from
 *   setSuppressed(reason, active)
 *                — SNOW-1421: Conditions mode holds the zones off the map
 *                  (map_mode.js); never writes the switch
 *
 * A PLACEMENT. Placing a pin or drawing a route clears the map of every
 * other overlay (map_placement_focus.js), but never of these: the zones
 * are legally binding, and still there while something is placed. While
 * a route is drawn they show even with the switch off; once it is saved
 * or drawing stops, the switch decides again.
 *
 * THE MODE (SNOW-1421). The zones are Terrain's own layer, so Conditions
 * holds them off the map — except while a route is drawn, which asks for
 * them in either mode. `visibility()` asks window.pwaMapModeState (map.js,
 * which knows the boot mode before this file runs), so the first install on
 * a Conditions boot is already hidden; the hook covers later changes.
 */

// @ts-check

(function restZonesInit() {
  'use strict';

  const config = /** @type {HTMLElement|null} */ (document.getElementById('rest-zones-config'));
  const sheetEl = /** @type {HTMLElement|null} */ (document.getElementById('rest-zone-sheet'));
  const bodyTemplate = /** @type {HTMLTemplateElement|null} */ (
    document.getElementById('rest-zone-body-template')
  );
  const core = self.pwaRestZonesCore;
  if (!config || !sheetEl || !bodyTemplate || !core || !window.MapSheet) return;
  // Narrowed once for the closures below, which TypeScript cannot follow.
  const SHEET = sheetEl;
  const BODY = bodyTemplate;

  const GEOJSON_URL = config.dataset.geojsonUrl || '';
  const SOURCE = 'rest-zones';
  const FILL = 'rest-zones-fill';
  const LINE = 'rest-zones-line';
  const LOAD_DEBOUNCE_MS = 250;
  // The map mode's suppression reason (layer_visibility_core.js).
  const MAP_MODE_REASON = self.pwaLayerVisibilityCore
    ? self.pwaLayerVisibilityCore.SUPPRESSION.MAP_MODE
    : 'map-mode';
  // The key the layers menu's row persists under; the literal is the
  // fallback for a page whose map_state.js predates the row.
  const STORAGE_KEY = (window.snowdeskMapState
    && window.snowdeskMapState.overlayStorageKey
    && window.snowdeskMapState.overlayStorageKey.rest_zones)
    || 'snowdesk.map.overlay.rest_zones';

  const STRINGS = self.pwaStrings.read('rest-zone-strings-template', {
    binding: 'Legally binding',
    recommended: 'Recommended',
    in_force_today: 'Protected on this day',
    not_in_force: 'Not protected on this day',
    period_unknown: 'Period not stated',
  });

  const state = {
    /** The last collection loaded, for a basemap swap's re-install. */
    /** @type {{type: string, features: Array<any>}} */
    data: { type: 'FeatureCollection', features: [] },
    /** The rectangle `data` covers, or null before the first load. */
    /** @type {?[number, number, number, number]} */
    loaded: null,
    /** The day `data` was loaded for. */
    /** @type {?string} */
    loadedDate: null,
    /** The map's day, or null for "today" as the server knows it. */
    /** @type {?string} */
    date: initialDate(),
    /** @type {?AbortController} */
    inflight: null,
    /** @type {?number} */
    timer: null,
    /** Whether draw mode has the map (`snowdesk:route-draw`). */
    drawing: false,
    /** The uuid of the zone the sheet shows, or null when it is closed. */
    /** @type {?string} */
    openUuid: null,
  };

  const sheet = window.MapSheet.attach(SHEET, {
    onTeardown: function () {
      state.openUuid = null;
    },
  });

  /**
   * The map's day at boot: `?d=`, else today as the page states it.
   * map_shared.js's `readDisplayDate` answers exactly that when it is
   * loaded; the fallback repeats it for a page without the map bundle.
   *
   * @returns {?string}
   */
  function initialDate() {
    if (typeof readDisplayDate === 'function') return readDisplayDate() || null;
    let day = null;
    try {
      day = new URLSearchParams(location.search).get('d');
    } catch {
      day = null;
    }
    const scrubber = document.getElementById('season-scrubber');
    return day || (scrubber && scrubber.dataset.today) || null;
  }

  /** @returns {any} The MapLibre map, or null before it exists. */
  function getMap() {
    return (window.snowdeskMapState && window.snowdeskMapState.map) || null;
  }

  /** @returns {boolean} Whether the switch is on. */
  function isEnabled() {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  }

  /** @returns {boolean} Whether the map's mode holds the zones off the map (SNOW-1421). */
  function held() {
    return !!(window.pwaMapModeState && window.pwaMapModeState.isSuppressed('rest_zones'));
  }

  /**
   * Whether the zones are drawn: the switch is on and the map's mode does
   * not hold them, or a route is being drawn. Draw mode shows them whatever
   * the switch and the mode say, and they decide again once the route is
   * saved or drawing stops (Hugo, 3 Nov 2026).
   *
   * @returns {boolean}
   */
  function shown() {
    return (isEnabled() && !held()) || state.drawing;
  }

  /** @returns {boolean} Whether a placement mode (a pin, a route) has the map. */
  function placing() {
    return !!(window.PlacementFocus && window.PlacementFocus.isActive());
  }

  /** @returns {'visible'|'none'} The layers' visibility. */
  function visibility() {
    return shown() ? 'visible' : 'none';
  }

  /** Show or hide the layers, once they exist. */
  function applyVisibility() {
    const map = getMap();
    if (!map || !map.getLayer(FILL)) return;
    for (const id of [FILL, LINE]) {
      map.setLayoutProperty(id, 'visibility', visibility());
    }
  }

  // ---------------------------------------------------------------------------
  // The layers
  // ---------------------------------------------------------------------------

  /**
   * Add the zones' source and layers, once per style: under the region
   * boundaries and every pin, over the danger colours.
   *
   * @param {any} map
   */
  function installLayers(map) {
    if (map.getSource(SOURCE)) return;
    map.addSource(SOURCE, {
      type: 'geojson',
      data: state.data,
      // MapLibre keeps a Feature's top-level id only when it is a number;
      // the uuid is a string, so a tap reads it back from the properties.
      promoteId: 'uuid',
      attribution: '© BAFU',
    });
    const before = map.getLayer('regions-line') ? 'regions-line' : undefined;
    const layout = { visibility: visibility() };
    map.addLayer({
      id: FILL,
      type: 'fill',
      source: SOURCE,
      minzoom: core.MINZOOM,
      layout: layout,
      paint: core.fillPaint(),
    }, before);
    map.addLayer({
      id: LINE,
      type: 'line',
      source: SOURCE,
      minzoom: core.MINZOOM,
      layout: { ...layout, 'line-join': 'round' },
      paint: core.linePaint(),
    }, before);
  }

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
    refreshOpenZone();
  }

  /**
   * The open sheet follows the zones just loaded: a new day can change
   * whether its zone is protected. A zone no longer loaded closes it.
   */
  function refreshOpenZone() {
    if (!state.openUuid || !sheet.isOpen()) return;
    const uuid = state.openUuid;
    const feature = state.data.features.find(
      (f) => f && f.properties && f.properties.uuid === uuid,
    );
    if (feature) renderZone(feature.properties);
    else sheet.close();
  }

  // ---------------------------------------------------------------------------
  // Loading the view
  // ---------------------------------------------------------------------------

  /** Load the view's zones when it needs them. */
  function load() {
    const map = getMap();
    if (!map || !GEOJSON_URL || !shown()) return;
    if (map.getZoom() < core.MINZOOM) return;
    const b = map.getBounds();
    /** @type {[number, number, number, number]} */
    const view = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    if (state.loadedDate === state.date && core.covers(state.loaded, view)) {
      // What is drawn is what is wanted: a request still in flight was for
      // something since given up (another day), and must not land on it.
      if (state.inflight) state.inflight.abort();
      state.inflight = null;
      return;
    }
    const box = core.loadBox(view);
    if (!box) return;
    const date = state.date;
    if (state.inflight) state.inflight.abort();
    const inflight = new AbortController();
    state.inflight = inflight;
    fetch(core.dataUrl(GEOJSON_URL, box, date), {
      credentials: 'same-origin',
      signal: inflight.signal,
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (body) {
        if (state.inflight !== inflight) return;
        state.inflight = null;
        // The map moved to another day while this was in flight.
        if (date !== state.date) return;
        if (!body || !Array.isArray(body.features)) throw new Error('not a FeatureCollection');
        state.loaded = box;
        state.loadedDate = date;
        paintData(map, body);
      })
      .catch(function (err) {
        // A request a newer one replaced was aborted for it, not failed.
        if (state.inflight !== inflight) return;
        state.inflight = null;
        console.warn('rest zones: the zones could not be loaded', err);
      });
  }

  /** Load after the map settles, at most once a quarter second. */
  function scheduleLoad() {
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = window.setTimeout(function () {
      state.timer = null;
      load();
    }, LOAD_DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------------------
  // The switch
  // ---------------------------------------------------------------------------

  /** @param {boolean} on */
  function setEnabled(on) {
    try {
      localStorage.setItem(STORAGE_KEY, String(on));
    } catch {
      // Private mode: the choice still applies for this page.
    }
    applyVisibility();
    if (on) scheduleLoad();
    else sheet.close();
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
  }

  // ---------------------------------------------------------------------------
  // The sheet
  // ---------------------------------------------------------------------------

  /**
   * Write one slot of the body, or remove it when there is nothing to say.
   *
   * @param {ParentNode} root
   * @param {string} name The slot's `data-rest-zone-*` suffix.
   * @param {?string} text
   */
  function fill(root, name, text) {
    const el = /** @type {HTMLElement|null} */ (root.querySelector('[data-rest-zone-' + name + ']'));
    if (!el) return;
    if (text) {
      el.textContent = text;
      return;
    }
    const row = el.closest('[data-rest-zone-row]');
    (row || el).remove();
  }

  /**
   * The rules page as a link target, only when it is an absolute http(s)
   * URL. The server already checks it; this is the second line, because a
   * `javascript:` href would run on a tap.
   *
   * @param {unknown} value
   * @returns {?string}
   */
  function safeHref(value) {
    if (!value) return null;
    try {
      const url = new URL(String(value));
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
    } catch {
      return null;
    }
  }

  /**
   * Fill the sheet from one zone's properties.
   *
   * @param {Object<string, any>} props
   */
  function renderZone(props) {
    const node = /** @type {DocumentFragment} */ (BODY.content.cloneNode(true));
    fill(node, 'name', String(props.name || ''));
    fill(node, 'kind', props.binding ? STRINGS.binding : STRINGS.recommended);
    fill(node, 'canton', props.canton ? String(props.canton) : null);
    fill(node, 'status', props.in_force ? STRINGS.in_force_today : STRINGS.not_in_force);
    fill(node, 'period', props.period ? String(props.period) : STRINGS.period_unknown);
    fill(node, 'restriction', props.restriction ? String(props.restriction) : null);
    const link = /** @type {HTMLAnchorElement|null} */ (node.querySelector('[data-rest-zone-rules]'));
    if (link) {
      const href = safeHref(props.url);
      if (href) link.href = href;
      else link.remove();
    }
    SHEET.replaceChildren(node);
  }

  /**
   * Fill the sheet from one zone's properties and open it.
   *
   * @param {Object<string, any>} props
   */
  function openZone(props) {
    renderZone(props);
    sheet.open();
    state.openUuid = props.uuid ? String(props.uuid) : null;
  }

  /**
   * Handle a map tap at a screen point: open the zone under it.
   *
   * @param {{x: number, y: number}} point
   * @returns {boolean} Whether the tap was inside a zone.
   */
  function tap(point) {
    const map = getMap();
    // While a pin is placed the zones are drawn but take no tap: the tap
    // belongs to the placement.
    if (!map || !isEnabled() || held() || placing() || !map.getLayer(FILL)) return false;
    if (map.getZoom() < core.MINZOOM) return false;
    const zone = core.pickZone(map.queryRenderedFeatures([point.x, point.y], {
      layers: [FILL, LINE],
    }));
    if (!zone || !zone.properties) return false;
    openZone(zone.properties);
    return true;
  }

  // ---------------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------------

  // The map's day moved: the zones are reloaded for it. A preview (a drag
  // still under way) is not a day anyone stopped on.
  // Draw mode's start and end (route_draw.js): the zones show for the
  // drawing, then fall back to the switch.
  document.addEventListener('snowdesk:route-draw', function (event) {
    const detail = /** @type {CustomEvent} */ (event).detail;
    state.drawing = !!(detail && detail.active);
    applyVisibility();
    if (state.drawing) scheduleLoad();
  });

  document.addEventListener('snowdesk:date-changed', function (event) {
    const detail = /** @type {CustomEvent} */ (event).detail;
    state.date = (detail && detail.date) || null;
    scheduleLoad();
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
    ready();
  }
  bindMap();

  window.pwaRestZones = Object.freeze({
    tap: tap,
    show: () => setEnabled(true),
    hide: () => setEnabled(false),
    isEnabled: isEnabled,
    MINZOOM: core.MINZOOM,
    /**
     * SNOW-1421: hold or release the zones for the map's mode, without
     * writing the switch. Only the mode's reason is taken.
     *
     * @param {string} reason A ``pwaLayerVisibilityCore.SUPPRESSION`` value.
     * @param {boolean} active
     */
    setSuppressed: (reason, active) => {
      if (reason !== MAP_MODE_REASON || !window.pwaMapModeState) return;
      window.pwaMapModeState.hold('rest_zones', active);
      applyVisibility();
      if (shown()) scheduleLoad();
      document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    },
  });
})();
