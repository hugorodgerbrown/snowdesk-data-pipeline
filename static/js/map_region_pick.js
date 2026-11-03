/*
 * static/js/map_region_pick.js — "Download a region": pick a region on the
 * map and download it (SNOW-1414).
 *
 * WHY IT EXISTS. A region download used to start in one place only: the
 * download button on the region sheet, which a tap on a region opens.
 * Terrain mode holds the region tiers, the region tap and the region sheet
 * (SNOW-1394 — a region exists only to hold a bulletin), so in Terrain a
 * region could not be downloaded at all, and Terrain is the planning mode
 * a download for tomorrow is planned in. This task is the second way in,
 * reached from the Downloads sheet beside "Download a custom area", and it
 * works the same in both modes.
 *
 * WHAT IT DOES, in order:
 *
 *   1. open() — `PlacementFocus.enter()` clears the app's overlays down to
 *      the basemap (its rest-zone exception stands), `body.map-framing`
 *      takes the day's furniture away exactly as the custom-area framing
 *      overlay does, and three layers of this module's own go on the
 *      existing `regions` source: a fill that is the tap target and tints
 *      the chosen region, a neutral outline, and the names. No danger
 *      colour anywhere.
 *   2. A tap on a region chooses it: its outline thickens, and the bar at
 *      the foot names it and says what it costs on this device
 *      (`pwaRegionDownload.estimate`, the same priced figure the region
 *      roundel shows).
 *   3. Download closes the task FIRST and then calls
 *      `pwaRegionDownload.start(regionId)` without waiting on it: `start()`
 *      settles only once the whole run has finished, and the picker has no
 *      business staying open for that. Progress shows where every region
 *      download shows it. A refusal settles early, and is told as a toast
 *      (`pwaRegionPickCore.refusalKind`).
 *
 * SUPPRESSION, NEVER PREFERENCE, STILL HOLDS. The task never releases a
 * layer Terrain holds and never writes a stored choice: its layers are new
 * ones, added after `PlacementFocus.enter()` took its snapshot. That is
 * also why close() removes them BEFORE `PlacementFocus.exit()` — they are
 * not in the snapshot, so exit() would leave them drawn.
 *
 * WHAT ENDS IT: Download, Cancel, Escape, a mode change and a basemap
 * change (a style swap drops the task's layers with the rest). map.js's
 * one click handler steps aside while `isActive()` is true, as it does for
 * route drawing, so a tap here never also opens a region sheet or a pin.
 *
 * LOAD ORDER: after map.js (it reads `snowdeskMapState.map` at open, not at
 * parse) and after map_region_download.js, whose bridge it calls. Outside
 * the map bundle's contiguous run — see tests/public/test_map_script_order.py.
 *
 * Exports (frozen `window.pwaRegionPick`): open(), close(), isActive().
 */

// @ts-check

(function mapRegionPickInit() {
  'use strict';

  const core = self.pwaRegionPickCore;
  const found = {
    overlay: document.getElementById('map-region-pick-overlay'),
    name: document.getElementById('map-region-pick-name'),
    readout: document.getElementById('map-region-pick-readout'),
    confirm: document.getElementById('map-region-pick-confirm'),
    cancel: document.getElementById('map-region-pick-cancel'),
  };
  if (!core || !found.overlay || !found.name || !found.readout || !found.confirm || !found.cancel) {
    return;
  }
  // Narrowed once here: the functions below are hoisted, so the checker
  // cannot carry the guard above into them.
  const overlayEl = /** @type {HTMLElement} */ (found.overlay);
  const nameEl = /** @type {HTMLElement} */ (found.name);
  const readoutEl = /** @type {HTMLElement} */ (found.readout);
  const confirmBtn = /** @type {HTMLButtonElement} */ (found.confirm);
  const cancelBtn = /** @type {HTMLElement} */ (found.cancel);

  // bin/i18n-lint: every user-facing string is rendered by the server into
  // this module's own template and read back here, with the English literal
  // as the fallback.
  const STRINGS = self.pwaStrings.read('map-region-pick-strings-template', {
    choosing: 'Tap a region to download it',
    hint: 'Region outlines only, no danger ratings',
    readout: 'Up to %(mb)s MB',
    unavailable: "This region can't be downloaded",
    'too-big': 'Up to %(mb)s MB, too large for this device',
    busy: 'Another region is still downloading. Try again when it finishes.',
    already: 'That region is already downloaded on this device.',
    failed: "That download couldn't be started. Try again.",
  });

  const SOURCE = 'regions';
  const FILL_LAYER = 'region-pick-fill';
  const LINE_LAYER = 'region-pick-line';
  const LABEL_LAYER = 'region-pick-label';
  const LAYERS = Object.freeze([FILL_LAYER, LINE_LAYER, LABEL_LAYER]);
  // Its own feature-state key, so the region selection map.js keeps under
  // `selected` is neither read nor written.
  const STATE_KEY = 'picked';
  const FALLBACK_FONT = ['Noto Sans Regular'];

  /** @type {any} */
  let activeMap = null;
  /** @type {?(string|number)} The chosen feature's MapLibre id. */
  let pickedFeatureId = null;
  /** @type {?string} The chosen region's id. */
  let pickedRegionId = null;

  /**
   * The map, through the shared state channel.
   *
   * @returns {any}
   */
  function getMap() {
    const state = window.snowdeskMapState;
    return state ? state.map : null;
  }

  /**
   * The text font the regions' own labels use, so the task's names read in
   * the same face on every basemap.
   *
   * @param {any} map
   * @returns {any}
   */
  function labelFont(map) {
    try {
      return map.getLayoutProperty('regions-label', 'text-font') || FALLBACK_FONT;
    } catch (_err) {
      return FALLBACK_FONT;
    }
  }

  /**
   * Add the task's three layers on the `regions` source.
   *
   * @param {any} map
   * @returns {void}
   */
  function addLayers(map) {
    removeLayers(map);
    const picked = ['boolean', ['feature-state', STATE_KEY], false];
    map.addLayer({
      id: FILL_LAYER,
      type: 'fill',
      source: SOURCE,
      paint: {
        'fill-color': '#1a1916',
        // Transparent but still the tap target; the chosen region tints.
        'fill-opacity': ['case', picked, 0.14, 0],
      },
    });
    map.addLayer({
      id: LINE_LAYER,
      type: 'line',
      source: SOURCE,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': '#1a1916',
        'line-width': ['case', picked, 3, 1],
        'line-opacity': ['case', picked, 0.95, 0.55],
      },
    });
    map.addLayer({
      id: LABEL_LAYER,
      type: 'symbol',
      source: SOURCE,
      minzoom: 8,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': labelFont(map),
        'text-size': 11,
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#2a2a2a',
        'text-halo-color': 'rgba(255,255,255,0.85)',
        'text-halo-width': 1.2,
      },
    });
  }

  /**
   * Remove the task's layers, and the chosen region's feature-state with
   * them. Tolerates layers a style swap has already dropped.
   *
   * @param {any} map
   * @returns {void}
   */
  function removeLayers(map) {
    if (!map) return;
    clearPicked(map);
    for (const id of LAYERS) {
      try {
        if (map.getLayer(id)) map.removeLayer(id);
      } catch (_err) {
        /* the style went with it */
      }
    }
  }

  /**
   * Take the chosen region's highlight off.
   *
   * @param {any} map
   * @returns {void}
   */
  function clearPicked(map) {
    if (pickedFeatureId === null) return;
    try {
      map.removeFeatureState({ source: SOURCE, id: pickedFeatureId }, STATE_KEY);
    } catch (_err) {
      /* source gone */
    }
    pickedFeatureId = null;
    pickedRegionId = null;
  }

  /**
   * Paint the bar for the current choice.
   *
   * @param {any} bar A `pwaRegionPickCore.barState` result.
   * @returns {void}
   */
  function paintBar(bar) {
    const S = core.STATES;
    overlayEl.dataset.pickState = bar.state;
    if (bar.state === S.CHOOSING) {
      nameEl.textContent = STRINGS.choosing;
      readoutEl.textContent = STRINGS.hint;
    } else {
      nameEl.textContent = bar.name;
      readoutEl.textContent =
        bar.state === S.UNAVAILABLE
          ? STRINGS.unavailable
          : self.pwaStrings.interpolate(
              bar.state === S.TOO_BIG ? STRINGS['too-big'] : STRINGS.readout,
              { mb: bar.mb },
            );
    }
    confirmBtn.disabled = !bar.canDownload;
    confirmBtn.setAttribute('aria-disabled', bar.canDownload ? 'false' : 'true');
  }

  /**
   * A tap on the map while the task is open: choose the region under it.
   *
   * @param {any} event A MapLibre click event.
   * @returns {void}
   */
  function onMapClick(event) {
    const map = activeMap;
    if (!map || !map.getLayer(FILL_LAYER)) return;
    const hit = map.queryRenderedFeatures(event.point, { layers: [FILL_LAYER] })[0];
    if (!hit) return;
    const props = hit.properties || {};
    const regionId = props.regionID || props.id || null;
    if (!regionId) return;
    clearPicked(map);
    if (hit.id !== undefined && hit.id !== null) {
      pickedFeatureId = hit.id;
      map.setFeatureState({ source: SOURCE, id: hit.id }, { [STATE_KEY]: true });
    }
    pickedRegionId = String(regionId);
    const download = window.pwaRegionDownload;
    const estimate = download && download.estimate ? download.estimate(pickedRegionId) : null;
    paintBar(core.barState(pickedRegionId, props.name || '', estimate));
  }

  /**
   * Escape closes the task, as Cancel does.
   *
   * @param {KeyboardEvent} event
   * @returns {void}
   */
  function onKeydown(event) {
    if (event.key === 'Escape') close();
  }

  /**
   * Start the task. A second call while it is open does nothing.
   *
   * @returns {boolean} Whether the task is open.
   */
  function open() {
    if (activeMap) return true;
    const map = getMap();
    if (!map || !map.getSource(SOURCE)) return false;
    activeMap = map;
    window.PlacementFocus?.enter();
    addLayers(map);
    document.body.classList.add('map-framing');
    overlayEl.hidden = false;
    paintBar(core.barState(null, null, null));
    map.on('click', onMapClick);
    document.addEventListener('keydown', onKeydown);
    return true;
  }

  /**
   * End the task and put the map back as it was: the task's layers come off
   * first, then `PlacementFocus.exit()` restores what it hid.
   *
   * @returns {void}
   */
  function close() {
    const map = activeMap;
    if (!map) return;
    activeMap = null;
    map.off('click', onMapClick);
    document.removeEventListener('keydown', onKeydown);
    removeLayers(map);
    window.PlacementFocus?.exit();
    document.body.classList.remove('map-framing');
    overlayEl.hidden = true;
  }

  /**
   * Download: close, then start the run without waiting for it to finish.
   *
   * @returns {void}
   */
  function confirm() {
    const regionId = pickedRegionId;
    const download = window.pwaRegionDownload;
    if (!regionId || !download || confirmBtn.disabled) return;
    close();
    Promise.resolve(download.start(regionId)).then(
      (outcome) => {
        const kind = core.refusalKind(outcome);
        if (kind) window.MapSheet?.toast(STRINGS[kind] || STRINGS.failed);
      },
      () => window.MapSheet?.toast(STRINGS.failed),
    );
  }

  confirmBtn.addEventListener('click', confirm);
  cancelBtn.addEventListener('click', close);
  document.addEventListener('snowdesk:map-mode-changed', close);
  document.addEventListener('snowdesk:basemap-changed', close);

  window.pwaRegionPick = Object.freeze({
    open: open,
    close: close,
    isActive: () => activeMap !== null,
  });
})();
