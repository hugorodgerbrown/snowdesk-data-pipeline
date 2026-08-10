/*
 * static/js/map_placement_focus.js — distraction-free pin positioning.
 *
 * While the user is positioning a pin — a favourite, a field observation,
 * or a resort in the staff Edit-resorts tool — everything Snowdesk draws
 * over the basemap is temporarily hidden, so the only things on screen are
 * the terrain and the pin being placed. The moment the pin is submitted or
 * the flow is cancelled, every hidden layer comes back exactly as it was.
 *
 * Exposes ``window.PlacementFocus`` with three methods:
 *
 *   enter()     Snapshot each overlay layer's current ``visibility`` and
 *               hide them all. Idempotent — a second call while already
 *               focused is a no-op, so the original (pre-focus) snapshot is
 *               never clobbered by a re-arm.
 *   exit()      Restore every snapshotted layer to its pre-focus
 *               visibility. Idempotent, and safe to call having never
 *               entered.
 *   isActive()  True between enter() and the matching exit().
 *
 * Which layers count as "over the basemap"
 * ----------------------------------------
 * Every layer Snowdesk installs — the L4 choropleth (``regions-*``), the
 * L1/L2 outlines, bulletin groupings, resort pins, favourite pins,
 * community-report clusters, and the Edit-resorts points — is backed by a
 * ``geojson`` source that this app adds. Every layer belonging to the
 * basemap style is backed by a ``vector``/``raster`` source (or is a
 * sourceless ``background`` layer, as in the offline fallback style). So
 * "hide the layers whose source is a geojson source" is an exact,
 * self-maintaining definition of the app's own overlays: a new overlay
 * added later is covered without touching this module, and no basemap
 * layer is ever hidden. Contrast the hand-maintained ``OVERLAY_LAYER_IDS``
 * lists in static/js/map.js, which have to enumerate ids because they map
 * *menu rows* to layers — a mapping that can't be derived.
 *
 * Callers
 * -------
 *   static/js/place_picker.js    — covers the favourite-create and
 *                                  field-observation flows, which both
 *                                  position via the shared centre pin
 *                                  (activate() → enter, deactivate() → exit).
 *   static/js/map_edit_resorts.js — enters while a draft resort marker is
 *                                  on the map (drop/paste/select-with-coords)
 *                                  and exits when it is removed (Save,
 *                                  Cancel, Escape, or selecting another row).
 *
 * static/js/map.js additionally listens for the ``snowdesk:placement-focus``
 * event dispatched below and closes any open region / detail popup on
 * entry — a card anchored to a region that is no longer drawn would be a
 * leftover distraction. Popups are not restored on exit: the map has
 * usually been panned by then, so the old anchor is meaningless.
 *
 * Every transition also dispatches ``snowdesk:overlay-visibility-changed``,
 * so the three overlay roundels drop their "shown on the map" ring for the
 * duration of the placement and take it back afterwards — see
 * announceOverlayVisibility() below.
 */

(function () {
  'use strict';

  /** Pre-focus ``visibility`` of every layer hidden by enter(), keyed by
   * layer id. Null whenever focus is inactive — which doubles as the
   * active/inactive flag.
   * @type {Object<string, string>|null}
   */
  let snapshot = null;

  /** Return the global MAP object if available, else null. Guards against
   * the case where map.js has not yet initialised.
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null;
  }

  /** Ids of every layer this app draws over the basemap — see the header
   * comment for why a geojson-source test is the definition. Returns an
   * empty list when the style isn't readable yet (getStyle() throws before
   * the style loads), so a placement armed against a half-booted map
   * degrades to "hide nothing" rather than throwing.
   * @param {maplibregl.Map} map
   * @returns {string[]}
   */
  function overlayLayerIds(map) {
    let style;
    try {
      style = map.getStyle();
    } catch (_) {
      return [];
    }
    if (!style || !Array.isArray(style.layers)) return [];
    const sources = style.sources || {};
    const ids = [];
    for (const layer of style.layers) {
      const source = sources[layer.source];
      if (source && source.type === 'geojson') ids.push(layer.id);
    }
    return ids;
  }

  /** Set one layer's visibility, tolerating a layer that has since been
   * removed (a basemap swap mid-flight) — MapLibre throws on an unknown id.
   * @param {maplibregl.Map} map
   * @param {string} layerId
   * @param {string} value — 'visible' or 'none'.
   */
  function setVisibility(map, layerId, value) {
    try {
      if (map.getLayer(layerId)) map.setLayoutProperty(layerId, 'visibility', value);
    } catch (_) { /* layer vanished between enumeration and write */ }
  }

  /** Hide every overlay layer and return the snapshot of what they were.
   * ``visibility`` is unset on most layers (MapLibre's default is
   * 'visible'), so the snapshot normalises undefined to 'visible' — that
   * value is what exit() writes back, which is equivalent to the default.
   * @param {maplibregl.Map} map
   * @returns {Object<string, string>}
   */
  function hideOverlays(map) {
    const previous = {};
    for (const layerId of overlayLayerIds(map)) {
      let prior;
      try {
        prior = map.getLayoutProperty(layerId, 'visibility');
      } catch (_) {
        prior = undefined;
      }
      previous[layerId] = prior === 'none' ? 'none' : 'visible';
      setVisibility(map, layerId, 'none');
    }
    return previous;
  }

  /** Tell the overlay roundels that what is drawn on the map has changed.
   *
   * SNOW-658 review: this module hides and restores every app layer without
   * going through any of map.js's three overlay bridges, so it is a writer
   * of all three overlays' visibility — and since those bridges answer
   * ``isVisible()`` from the layers themselves, their answer changes the
   * moment enter()/exit() runs. Nothing else would say so: the preference
   * behind each overlay has not moved, and map.js announces from its own
   * writers only. Without this, the three rings would go on claiming
   * "shown on the map" over a map cleared down to the basemap.
   *
   * The same event map.js's ``announceOverlayVisibility`` dispatches, sent
   * directly rather than by asking map.js to send it: this module's writes
   * are its own, and the event deliberately carries no detail, so a second
   * dispatcher costs nothing and adds no state to keep in step.
   */
  function announceOverlayVisibility() {
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
  }

  /** Announce a focus transition so map.js (which owns the popups) can
   * react. Kept as an event rather than a direct call because map.js's
   * popup handles are closure-scoped inside its main IIFE — the same
   * bridge pattern as snowdesk:overlay-load / snowdesk:country-toggle.
   * @param {boolean} active
   */
  function announce(active) {
    document.dispatchEvent(
      new CustomEvent('snowdesk:placement-focus', { detail: { active } }),
    );
    announceOverlayVisibility();
  }

  window.PlacementFocus = {
    /** Hide every app overlay, remembering what was visible. No-op when
     * already focused or when the map isn't up yet.
     */
    enter: function enter() {
      if (snapshot !== null) return;
      const map = getMap();
      if (!map) return;
      snapshot = hideOverlays(map);
      announce(true);
    },

    /** Restore every layer hidden by enter(). Idempotent. */
    exit: function exit() {
      if (snapshot === null) return;
      const previous = snapshot;
      snapshot = null;
      const map = getMap();
      if (map) {
        for (const layerId of Object.keys(previous)) {
          setVisibility(map, layerId, previous[layerId]);
        }
      }
      announce(false);
    },

    /**
     * @returns {boolean} True while placement focus is applied.
     */
    isActive: function isActive() {
      return snapshot !== null;
    },
  };

  // A basemap swap tears the whole style down and map.js re-installs every
  // overlay from scratch — visible, and with layer ids our snapshot can no
  // longer restore. The layers menu is reachable mid-placement, so re-hide
  // and re-snapshot against the fresh style. map.js dispatches this only
  // after all its re-installs have run, so the values captured here are the
  // genuine pre-focus visibilities (restored by the install functions from
  // overlayState), not a half-built style.
  document.addEventListener('snowdesk:basemap-changed', function () {
    if (snapshot === null) return;
    const map = getMap();
    if (!map) return;
    snapshot = hideOverlays(map);
    // The re-install map.js just did announced the overlays as drawn (it
    // fires this event at the end of that work), and the line above has
    // hidden them again — so say so, or the rings come back mid-placement
    // and stay on. Not announce(), which would re-declare a placement
    // transition that never happened and close popups a second time.
    announceOverlayVisibility();
  });
}());
