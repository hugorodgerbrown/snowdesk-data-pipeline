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
 *      layers are added after it, and stay). SAC's tours and summits are
 *      among them; the guide under the draft is swisstopo's network, below.
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
 * SAVING OFFLINE (SNOW-1262). Online, Save is the direct post it always
 * was — the same immediate answer, the panel closing on it — except that
 * it carries an Idempotency-Key the device minted and tagged the draft
 * with first. Offline, or when that post never gets an answer, the same
 * save goes to window.pwaMutationQueue.enqueue() under the same key, the
 * panel closes, the routes panel lists it as "Waiting to sync" and the map
 * draws its plain line (routes.js, map.js). The draft stays stored and
 * tagged until the queue says how it ended:
 *
 *   - replayed (by this page, another, or the service worker with no tab
 *     open): the queue left an acknowledgement for the key, and the next
 *     reconcile — on load, or on `pwa:mutation-queue-changed` — clears
 *     the draft;
 *   - refused for good (400, 409 at the cap, 403, 404): the tag moves to
 *     `failed` and the draft is editable again; the routes panel offers
 *     Edit (window.pwaRouteDraw.edit) and Discard (…discard);
 *   - neither row nor acknowledgement: the draft keeps its key and is
 *     editable; an unchanged re-save sends the same key, so within the
 *     server's 24 h replay window it answers with the route it already
 *     made, and an edited one mints a new key (the draft's `sent`
 *     fingerprint no longer matches its body).
 *
 * While the save is queued, draw mode shows the line and says it is
 * waiting, and every edit and Save is disabled — so a second copy cannot
 * be queued. The draft is stamped with the account it was drawn under and
 * discarded on load under any other (route_draw_core.js's draftState).
 *
 * THE NETWORK (SNOW-1277). At z12 or closer the panel loads swisstopo's
 * ski-touring network for the view (/api/ski-network.geojson) and draws it
 * as a faint shadow under the draft, whether or not it is followed: since
 * SNOW-1285 it is the only place the map draws that network. While "Follow
 * ski routes" is on, a tap within 24 px of a line snaps to it, and a ring
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
 *   isActive()   — whether the map is in draw mode
 *   start()      — enter draw mode, resuming any stored draft
 *   edit(key)    — SNOW-1262: take a save refused for good back into the
 *                  draft and enter draw mode on it
 *   discard(key) — SNOW-1262: throw a refused save away, row and draft
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
    'saved-offline': "Saved on this device. It will sync when you're back online.",
    'save-queued': "This route is waiting to sync. It will be saved when you're back online.",
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
    /** SNOW-1262: the Idempotency-Key the draft's save went out under. */
    /** @type {?string} */
    key: null,
    /** SNOW-1262: the fingerprint of the body sent under `key`. */
    /** @type {?string} */
    sent: null,
    /** SNOW-1262: the key of a save the server refused for good. */
    /** @type {?string} */
    failed: null,
    /** SNOW-1262: whether the draft's save is waiting in the queue. */
    queued: false,
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
   * Add the draft's sources and layers: the network's shadow and the drawn
   * preview at the bottom, the waypoints and straight guide on
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

  /** @returns {Object} The loaded network. */
  function networkCollection() {
    return { type: 'FeatureCollection', features: Array.from(state.network.values()) };
  }

  /** Redraw the network's shadow. */
  function drawNetwork() {
    const map = getMap();
    const source = map && map.getSource(NETWORK_SOURCE);
    if (source) source.setData(networkCollection());
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
    if (!map || !state.active || !NETWORK_URL || !snapCore) return;
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

    // SNOW-1262: a draft whose save is queued is shown, not edited — a
    // change now would be a second route, or a body the queued key no
    // longer matches.
    const locked = state.queued;
    const undo = find('[data-route-draw-undo]');
    if (undo) undo.disabled = locked || state.points.length === 0;
    const clear = find('[data-route-draw-clear]');
    if (clear) clear.disabled = locked || state.points.length === 0;
    const submit = find('[data-route-draw-submit]');
    if (submit) submit.disabled = locked || state.points.length < 2 || state.saving;
    const add = find('[data-route-draw-add]');
    if (add) add.disabled = locked;
    const name = find('#route-draw-name');
    if (name) name.disabled = locked;
    if (locked) showNotice(STRINGS['save-queued']);
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

  /** @returns {?string} The account signed in now, or null (SNOW-1262). */
  /**
   * The account signed in now (SNOW-1262): null for none, and UNDEFINED
   * while it cannot be known — no queue, or IndexedDB missing or awaiting a
   * reset. The difference matters: a stamped draft is destroyed only under
   * a different, KNOWN account.
   *
   * @returns {?string|undefined}
   */
  function principal() {
    const queue = window.pwaMutationQueue;
    const db = window.pwaDb;
    if (!queue || !queue.principal || typeof db !== 'object' || !db || db.isResetRequired()) {
      return undefined;
    }
    return queue.principal();
  }

  /**
   * Whether the stored draft is someone's while no account is known — and
   * so must be neither shown, overwritten nor removed (SNOW-1262).
   *
   * @returns {boolean}
   */
  function storedIsHidden() {
    const stored = readDraft();
    return Boolean(stored && core.draftState(stored, principal(), null, null) === 'hidden');
  }

  /**
   * Keep the draft on this device, or forget it once it is empty. Stamped
   * with its save's key, a refused save's key and the account (SNOW-1262).
   */
  function storeDraft() {
    // A hidden draft is another session's line: this visit's drawing lives
    // in memory rather than replacing it.
    if (storedIsHidden()) return;
    try {
      if (state.points.length === 0) localStorage.removeItem(core.DRAFT_KEY);
      else {
        localStorage.setItem(
          core.DRAFT_KEY,
          core.serialiseDraft(state.points, state.name, state.taps, {
            key: state.key,
            sent: state.key ? state.sent : null,
            failed: state.failed,
            principal: principal() || null,
          }),
        );
      }
    } catch (_err) {
      // Private mode or a full store: the draft lives for this visit only.
    }
  }

  /** @returns {?ReturnType<typeof core.parseDraft>} The stored draft. */
  function readDraft() {
    let raw = null;
    try {
      raw = localStorage.getItem(core.DRAFT_KEY);
    } catch (_err) {
      raw = null;
    }
    return core.parseDraft(raw);
  }

  /** Forget the stored draft outright. */
  function forgetDraft() {
    try {
      localStorage.removeItem(core.DRAFT_KEY);
    } catch (_err) {
      // Nothing was stored.
    }
  }

  /** Read back a stored draft into state, if there is one. */
  function restoreDraft() {
    const draft = readDraft();
    const verdict = draft ? core.draftState(draft, principal(), null, null) : 'none';
    const mine = verdict !== 'foreign' && verdict !== 'hidden';
    state.points = mine && draft ? draft.points : [];
    state.taps = mine && draft ? draft.taps : [];
    state.name = mine && draft ? draft.name : '';
    state.key = mine && draft ? draft.key : null;
    state.sent = mine && draft ? draft.sent : null;
    state.failed = mine && draft ? draft.failed : null;
  }

  /** Empty the draft in memory and in storage. */
  function resetDraft() {
    state.points = [];
    state.taps = [];
    state.name = '';
    state.key = null;
    state.sent = null;
    state.failed = null;
    state.queued = false;
    state.feature = null;
    state.previewFor = '';
    forgetDraft();
  }

  /**
   * Read the stored draft against the queue and settle it (SNOW-1262):
   * clear a draft whose save landed or that another account drew, untag
   * one whose save was refused, and note whether one is still queued.
   * Run on load and whenever the queue changes; redraws the panel if it
   * is open.
   *
   * @returns {Promise<void>}
   */
  async function reconcileDraft() {
    const queue = window.pwaMutationQueue;
    // Nothing is settled until the account is known: a draft destroyed or
    // untagged on a guess is the drawer's work lost.
    if (principal() === undefined) return;
    const draft = readDraft();
    if (!draft) {
      state.queued = false;
      return;
    }
    // Someone's draft, no one signed in: leave it exactly as it is.
    if (core.draftState(draft, principal(), null, null) === 'hidden') return;
    let row = null;
    let ack = null;
    if (draft.key && queue && queue.list) {
      const rows = await queue.list({ kind: core.ROUTE_CREATE_KIND });
      row = rows.find(function (r) {
        return r.idempotency_key === draft.key;
      }) || null;
      ack = await queue.acknowledged(draft.key);
    }
    // The queue was read asynchronously: a save or an edit since then
    // owns the draft now.
    const now = readDraft();
    if (!now || now.key !== draft.key) return;
    const verdict = core.draftState(draft, principal(), row, ack);
    if (verdict === 'foreign' || verdict === 'landed') {
      // Another account's line, or a route that exists now: either way
      // nothing of the draft is the drawer's to keep.
      resetDraft();
    } else if (verdict === 'failed') {
      // Written from the stored draft rather than from state, which is
      // only current while the panel is open.
      state.key = null;
      state.failed = draft.key;
      state.queued = false;
      try {
        localStorage.setItem(
          core.DRAFT_KEY,
          core.serialiseDraft(draft.points, draft.name, draft.taps, {
            key: null,
            failed: draft.key,
            principal: draft.principal || principal() || null,
          }),
        );
      } catch (_err) {
        // Private mode: state carries it for this visit.
      }
    } else {
      state.queued = verdict === 'queued';
    }
    if (state.active) refreshPanel();
  }

  /** Redraw the open panel and the guide from state. */
  function refreshPanel() {
    if (!state.active) return;
    renderPanel();
    drawGuide();
    schedulePreview();
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

  /**
   * Save the draft as a route on this account.
   *
   * SNOW-1262: online, the direct post it always was, under a key the
   * draft is tagged with first; offline, or with no answer, the same save
   * queued under the same key (`queueSave`). See the module header.
   */
  function save() {
    if (!SAVE_ELIGIBLE) {
      if (SIGNIN_URL) window.location.assign(SIGNIN_URL);
      return;
    }
    if (state.saving || state.queued || state.points.length < 2 || !CREATE_URL) return;
    // The key goes on the draft BEFORE the request, so whatever happens to
    // the request the draft names it. An "unsent" draft keeps its key while
    // its body is the one sent under it — the server then answers with the
    // route already made — and gets a new one once it has been edited,
    // because the server refuses an old key with a different body.
    const fingerprint = core.bodyFingerprint(state.points, nameOrDefault());
    if (state.key && state.sent !== fingerprint) state.key = null;
    if (!state.key) state.key = core.mintKey();
    state.sent = fingerprint;
    state.failed = null;
    storeDraft();
    const operation = core.createOperation(
      CREATE_URL,
      csrfToken(),
      state.points,
      nameOrDefault(),
      state.key,
    );
    state.saving = true;
    renderFigures();
    if (!online()) {
      queueSave(operation);
      return;
    }
    /** Whether the post was answered; a thrown fetch is queued instead. */
    let answered = false;
    fetch(operation.url, {
      method: operation.method,
      headers: Object.assign({}, operation.headers, { 'Idempotency-Key': operation.idempotencyKey }),
      body: operation.body,
    })
      .then(function (resp) {
        answered = true;
        if (resp.ok) return resp.json();
        // Refused: the server keeps that answer against the key for 24 h,
        // so the next save must not send it again.
        state.key = null;
        storeDraft();
        if (resp.status === 403 && SIGNIN_URL) {
          window.location.assign(SIGNIN_URL);
          return null;
        }
        return resp
          .json()
          .catch(function () {
            return null;
          })
          .then(function (/** @type {any} */ body) {
            const limit = resp.status === 409 && body && body.error === 'route_limit_reached';
            window.MapSheet.toast(limit ? STRINGS['save-limit'] : STRINGS['save-failed']);
            return null;
          });
      })
      .catch(function () {
        if (!answered) {
          // No answer at all: the network went. The same save, the same
          // key, into the queue — if the post did land, the replay gets
          // the route it made back.
          queueSave(operation);
          return 'queued';
        }
        window.MapSheet.toast(STRINGS['save-failed']);
        return null;
      })
      .then(function (body) {
        if (body === 'queued') return;
        state.saving = false;
        if (!body) {
          renderFigures();
          return;
        }
        state.points = [];
        state.taps = [];
        state.name = '';
        state.feature = null;
        state.key = null;
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

  /**
   * Put a save in the mutation queue under the draft's key (SNOW-1262),
   * then close the panel as a save does: the routes panel lists it as
   * waiting and the map draws its line, framed. The draft stays stored.
   *
   * @param {ReturnType<typeof core.createOperation>} operation
   * @returns {Promise<void>}
   */
  async function queueSave(operation) {
    const queue = window.pwaMutationQueue;
    const key = queue ? await queue.enqueue(operation) : null;
    state.saving = false;
    if (!key) {
      // No queue on this device (IndexedDB unavailable or awaiting a
      // reset): say so, as Save did before it could queue.
      state.key = null;
      storeDraft();
      window.MapSheet.toast(STRINGS['save-offline']);
      renderFigures();
      return;
    }
    state.queued = true;
    const bounds = core.pointsBounds(state.points);
    controller.close();
    window.pwaTelemetry?.emit('map.route.created', { drawn: true, queued: true });
    window.pwaRowFocus?.reveal({ overlay: window.pwaRoutesOverlay, coordinates: bounds });
    window.MapSheet.toast(STRINGS['saved-offline']);
  }

  /**
   * Take a save the server refused for good back into the draft and open
   * draw mode on it (SNOW-1262, the routes panel's Edit). The stored draft
   * is used when it is that save's — it keeps the taps — and the queued
   * body otherwise. The row goes: the draft holds everything it held.
   *
   * @param {string} key The refused save's Idempotency-Key.
   * @returns {Promise<void>}
   */
  async function edit(key) {
    const queue = window.pwaMutationQueue;
    if (!queue || !key) return;
    // Before anything mints a new key: did this save land after all? A
    // refused row can sit beside an acknowledgement for its key (another
    // drain replayed it), and Edit's new key would then save it twice.
    if (await queue.acknowledged(key)) {
      await queue.discard(key);
      const landed = readDraft();
      if (landed && (landed.failed === key || landed.key === key)) resetDraft();
      window.MapSheet.toast(STRINGS.saved);
      return;
    }
    const rows = await queue.list({ kind: core.ROUTE_CREATE_KIND });
    const row = rows.find(function (r) {
      return r.idempotency_key === key;
    });
    if (!row) return;
    const stored = readDraft();
    let draft = stored && (stored.failed === key || stored.key === key) ? stored : null;
    if (!draft) {
      let body = null;
      try {
        body = JSON.parse(row.body);
      } catch (_err) {
        body = null;
      }
      draft = core.parseDraft(
        body ? core.serialiseDraft(body.points, body.name === STRINGS['default-name'] ? '' : body.name) : null,
      );
    }
    if (!draft) return;
    await queue.discard(key);
    if (state.active) controller.close();
    state.points = draft.points;
    state.taps = draft.taps;
    state.name = draft.name;
    state.key = null;
    state.failed = null;
    state.queued = false;
    storeDraft();
    start();
  }

  /**
   * Throw a refused save away — the queued row, and the draft when it is
   * that save's (SNOW-1262, the routes panel's Discard).
   *
   * @param {string} key The refused save's Idempotency-Key.
   * @returns {Promise<void>}
   */
  async function discard(key) {
    const queue = window.pwaMutationQueue;
    if (!key) return;
    if (queue) await queue.discard(key);
    const stored = readDraft();
    if (stored && (stored.failed === key || stored.key === key)) {
      if (state.active) controller.close();
      resetDraft();
    }
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
    // SNOW-1262: a queued save's line is shown, not edited.
    if (state.queued) return;
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
    // SNOW-1262: is this draft's save still queued? Read now, so a tagged
    // draft opens locked rather than editable.
    reconcileDraft().catch(function () {});
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

  // SNOW-1262: settle a stored draft on load — the save may have landed
  // (from this device's worker with no tab open) or been refused while the
  // map was shut — and whenever the queue changes under an open page.
  reconcileDraft().catch(function () {});
  document.addEventListener('pwa:mutation-queue-changed', function () {
    reconcileDraft().catch(function () {});
  });

  window.pwaRouteDraw = Object.freeze({
    isActive: function () {
      return state.active;
    },
    start: start,
    edit: edit,
    discard: discard,
  });
})();
