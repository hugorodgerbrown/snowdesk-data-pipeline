/*
 * static/js/map_zoom_control.js — the map's zoom control (SNOW-1077).
 *
 * The roundel at the top of the bottom-left stack and the flyout it opens
 * (public/partials/_map_zoom_control.html). The face shows the whole zoom
 * the map is drawing and the roundel's accessible name says it ("Zoom
 * level 13"). The flyout holds − and +, a scale across the map's zoom range
 * with a marker at the camera, and a tick — with a row naming it — for
 * each zoom-gated layer that is on.
 *
 * WHY IT EXISTS. Two layers appear only past a zoom: the terrain filter
 * from z12, a saved route's slope colours from z14. The map never said
 * which zoom it was at, so a reader who switched the filter on at z10 saw
 * nothing and had no way to learn why. And zoom was gesture-only, which
 * fails WCAG 2.5.1 for anyone who cannot pinch: − and + are the
 * single-pointer alternative.
 *
 * It replaces a DEBUG-only readout in the same slot (map_zoom_readout.js),
 * and keeps two things from it: the tile zoom read off MapLibre rather than
 * computed (below), and — under DEBUG only, where the template renders the
 * line — the camera zoom to two decimals.
 *
 * TILE ZOOM, NOT CAMERA ZOOM, on the face. The camera is continuous; the
 * map draws whole-number tiles. MapLibre's `coveringZoomLevel` for a 512px
 * vector source is `floor(camera)` clamped to the source's own range
 * (measured: camera 9.99 fetches z9, camera 10.00 fetches z10), while a
 * 256px raster source rounds instead. So this asks the transform about the
 * style's first vector source rather than assuming one rule, and falls
 * back to floor(camera) when there is none (zoom_control_core.faceZoom).
 *
 * THE GATES come from `window.pwaZoomGates` (map.js): one entry per gated
 * layer, `{ key, zoom, active }`. The label for a key is the strings
 * template's `gate-<key>`; a key with no label shows the key, which reads
 * wrong on purpose. The ticks repaint on `zoom`, on
 * `snowdesk:overlay-visibility-changed` (routes on or off),
 * `snowdesk:terrain-filter-changed`, and `snowdesk:zoom-gates-changed`
 * (map.js, when a refetched routes payload lands with the overlay already
 * on).
 *
 * ONE OVERLAY AT A TIME. The flyout registers with `window.pwaMapOverlays`
 * as `map-zoom-flyout`, the same way #map-fill-flyout does
 * (map_basemap_picker.js), and closes on an outside click and on Escape.
 * It opens into the crook between the stack and the date row, where the
 * legend card opens, so it needs no measuring.
 *
 * LOAD ORDER: runs at parse time and reads `MAP`, so it loads after map.js,
 * and after zoom_control_core.js. Outside the map bundle (nothing in the
 * bundle reads it), so its tag sits below the bundle's contiguous run — see
 * tests/public/test_map_script_order.py.
 */

(function mapZoomControlInit() {
  'use strict';

  const toggle = document.getElementById('map-zoom-toggle');
  const flyout = document.getElementById('map-zoom-flyout');
  const core = self.pwaZoomControlCore;
  if (!toggle || !flyout || !core) return;
  if (typeof MAP === 'undefined' || !MAP) return;
  // Held locally: the control's listeners outlive this IIFE, and every one
  // of them should talk to the map that existed when it was bound.
  const map = MAP;

  const OVERLAY_NAME = 'map-zoom-flyout';

  const STRINGS = self.pwaStrings.read('map-zoom-strings-template', {
    'toggle-label': 'Zoom level %(zoom)s',
    'gate-region_select': 'Region selector',
    'desc-region_select': 'Tap a region to select it',
    'gate-terrain_filter': 'Terrain overlay',
    'desc-terrain_filter': 'Slope and aspect on the ground',
    'gate-route_slopes': 'Route slope angle',
    'desc-route_slopes': 'A saved route coloured by steepness',
    'threshold-from': '≥%(zoom)s',
    'threshold-up-to': '≤%(zoom)s',
    camera: 'Camera zoom %(zoom)s',
  });

  const face = toggle.querySelector('[data-zoom-face]');
  const zoomOut = flyout.querySelector('[data-zoom-step="out"]');
  const zoomIn = flyout.querySelector('[data-zoom-step="in"]');
  const marker = flyout.querySelector('[data-zoom-marker]');
  const tickList = flyout.querySelector('[data-zoom-ticks]');
  const levelList = flyout.querySelector('[data-zoom-levels]');
  const cameraLine = flyout.querySelector('[data-zoom-camera]');
  const tickTemplate = document.getElementById('map-zoom-tick-template');

  /**
   * The zoom the style's vector sources are fetching tiles at.
   *
   * Asks the transform rather than computing `floor(zoom)` here: the answer
   * depends on the source's tile size, its `roundZoom` flag and its own
   * minzoom/maxzoom clamp, and MapLibre owns all three. Read from the
   * FIRST vector source cache — every vector source in these styles shares
   * a tile size, so one answer serves; a style with none (the offline
   * fallback) yields null.
   *
   * @returns {number|null}
   */
  function tileZoom() {
    try {
      const caches = map.style && map.style.sourceCaches;
      if (!caches) return null;
      for (const key of Object.keys(caches)) {
        const cache = caches[key];
        const source = cache && cache._source;
        if (!source || source.type !== 'vector') continue;
        const z = map.transform.coveringZoomLevel(source);
        return Math.max(source.minzoom || 0, Math.min(z, source.maxzoom ?? z));
      }
    } catch (_e) {
      // The style is mid-swap, or MapLibre's internals moved. The camera
      // is always available, and faceZoom falls back to it.
    }
    return null;
  }

  /**
   * The label for a gate's key.
   *
   * @param {string} key A key from window.pwaZoomGates.
   * @returns {string}
   */
  function gateLabel(key) {
    return STRINGS['gate-' + key] || key;
  }

  /**
   * A clone of a `<template>`'s first element.
   *
   * @param {HTMLTemplateElement|null} template
   * @returns {HTMLElement|null}
   */
  function cloneRow(template) {
    if (!template || !template.content) return null;
    const first = template.content.firstElementChild;
    return first ? /** @type {HTMLElement} */ (first.cloneNode(true)) : null;
  }

  // The ticks last drawn, as one string, so a frame of a pinch that moves
  // no tick across its gate rebuilds nothing.
  let drawnTicks = null;

  /**
   * Redraw the ticks, when they changed.
   *
   * Each tick names one zoom-gated feature, its threshold (≥12, ≤10) and
   * what it does, placed
   * at the height of the level it becomes available. It is bold while the
   * camera is at a zoom where the feature works, switched on or not: the
   * scale answers "what can I do at this zoom", which is what a reader
   * who switched something on and saw nothing needs.
   *
   * @param {Array<{key: string, zoom: number, position: number, reached: boolean, upTo: boolean}>} ticks
   * @returns {void}
   */
  function renderTicks(ticks) {
    // The rows themselves change only when the gates do. A zoom past a
    // threshold restyles the row in place rather than rebuilding it, so a
    // row that holds keyboard focus — the one just tapped, say — keeps it
    // while the map animates through its threshold.
    const signature = ticks.map((t) => [t.key, t.zoom, t.upTo].join(':')).join('|');
    if (signature === drawnTicks && tickList) {
      ticks.forEach((tick, i) => {
        tickList.children[i]?.toggleAttribute('data-reached', tick.reached);
      });
      return;
    }
    drawnTicks = signature;

    const tickNodes = [];
    for (const tick of ticks) {
      const tickNode = cloneRow(tickTemplate);
      if (!tickNode) continue;
      tickNode.dataset.zoomTick = tick.key;
      tickNode.toggleAttribute('data-reached', tick.reached);
      tickNode.style.top = ((1 - tick.position) * 100).toFixed(2) + '%';
      const button = tickNode.querySelector('[data-zoom-tick-button]');
      if (button) button.dataset.zoomTarget = String(tick.zoom);
      const label = tickNode.querySelector('[data-zoom-tick-label]');
      if (label) label.textContent = gateLabel(tick.key);
      const threshold = tickNode.querySelector('[data-zoom-tick-threshold]');
      if (threshold) {
        threshold.textContent = self.pwaStrings.interpolate(
          STRINGS[tick.upTo ? 'threshold-up-to' : 'threshold-from'], { zoom: tick.zoom },
        );
      }
      const description = tickNode.querySelector('[data-zoom-tick-desc]');
      if (description) description.textContent = STRINGS['desc-' + tick.key] || '';
      tickNodes.push(tickNode);
    }
    if (tickList) tickList.replaceChildren(...tickNodes);
  }

  // The level range last numbered, so the numbers are written once per
  // range rather than on every frame of a pinch.
  let drawnLevels = null;

  /**
   * Number every whole zoom level on the line, once per range.
   *
   * @param {number} min The map's minimum zoom.
   * @param {number} max The map's maximum zoom.
   * @returns {void}
   */
  function renderLevels(min, max) {
    const signature = min + ':' + max;
    if (!levelList || signature === drawnLevels) return;
    drawnLevels = signature;
    const nodes = core.levels(min, max).map((level) => {
      const li = document.createElement('li');
      li.className = 'map-zoom-level';
      li.style.top = ((1 - level.position) * 100).toFixed(2) + '%';
      li.textContent = String(level.zoom);
      return li;
    });
    levelList.replaceChildren(...nodes);
  }

  // The face last written, so a pinch — a `zoom` event every frame — writes
  // the DOM only when the whole zoom actually changes.
  let drawnFace = null;

  // Where − and + are taking the map, while a step is animating. MapLibre's
  // zoomIn/zoomOut step from the CURRENT zoom, so a second tap during the
  // first one's animation stepped from the intermediate zoom: two quick
  // taps of − from z10 ended near z9, not z8. Each tap steps
  // from this target instead, and it clears when the move it started
  // ends, or when a gesture takes over.
  /** @type {number|null} */
  let pendingTarget = null;

  /**
   * Repaint the face and, while it is open, the flyout.
   *
   * @returns {void}
   */
  function render() {
    const camera = map.getZoom();
    const whole = core.faceZoom(tileZoom(), camera);
    if (whole !== drawnFace) {
      drawnFace = whole;
      if (face) face.textContent = String(whole);
      toggle.setAttribute(
        'aria-label',
        self.pwaStrings.interpolate(STRINGS['toggle-label'], { zoom: whole }),
      );
    }
    if (flyout.hidden) return;

    const min = map.getMinZoom();
    const max = map.getMaxZoom();
    // Judged from where the map is going, so + goes dead as soon as a tap
    // has committed the map to its maximum, not when the animation lands.
    const buttons = core.buttonState(pendingTarget ?? camera, min, max);
    setStepEnabled(zoomOut, buttons.canZoomOut, zoomIn);
    setStepEnabled(zoomIn, buttons.canZoomIn, zoomOut);
    if (marker) {
      marker.style.top = ((1 - core.scalePosition(camera, min, max)) * 100).toFixed(2) + '%';
    }

    renderLevels(min, max);
    const gates = window.pwaZoomGates ? window.pwaZoomGates.list() : [];
    renderTicks(core.ticks(gates, camera, min, max));

    if (cameraLine) {
      cameraLine.textContent = self.pwaStrings.interpolate(
        STRINGS.camera, { zoom: camera.toFixed(2) },
      );
    }
  }

  /**
   * Enable or disable one step button, keeping keyboard focus in the
   * flyout: a button disabled while it holds focus drops focus to <body>,
   * so focus moves to the other step instead.
   *
   * @param {HTMLButtonElement|null} button The step to set.
   * @param {boolean} enabled Whether it can move the map.
   * @param {HTMLButtonElement|null} other The opposite step.
   * @returns {void}
   */
  function setStepEnabled(button, enabled, other) {
    if (!button || button.disabled === !enabled) return;
    const hadFocus = document.activeElement === button;
    button.disabled = !enabled;
    if (!enabled && hadFocus && other && !other.disabled) other.focus();
  }

  /**
   * Zoom one whole step out (-1) or in (+1).
   *
   * Steps from the target of a step still animating rather than from the
   * camera — the further of the two in the step's direction — and clamps
   * to the map's range.
   *
   * @param {number} direction -1 or +1.
   * @returns {void}
   */
  function step(direction) {
    const camera = map.getZoom();
    const from = pendingTarget === null
      ? camera
      : direction > 0 ? Math.max(camera, pendingTarget) : Math.min(camera, pendingTarget);
    const target = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), from + direction));
    pendingTarget = target;
    map.zoomTo(target);
    render();
  }

  /**
   * Zoom straight to a level — a feature's threshold, from its row.
   *
   * Animated (MapLibre's zoomTo eases), so the marker visibly travels to
   * the row that was tapped; the row turns bold as the camera arrives.
   *
   * @param {number} zoom The level to go to.
   * @returns {void}
   */
  function goTo(zoom) {
    const target = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), zoom));
    pendingTarget = target;
    map.zoomTo(target);
    render();
  }

  /**
   * Open or close the flyout.
   *
   * @param {boolean} open
   * @returns {void}
   */
  function setOpen(open) {
    // Only one overlay is open over the map at a time. Announced before
    // the flyout is unhidden, so whatever it replaces is gone by the time
    // this is on screen.
    if (open) window.pwaMapOverlays?.opening(OVERLAY_NAME);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    flyout.hidden = !open;
    if (open) render();
  }

  window.pwaMapOverlays?.register(OVERLAY_NAME, {
    isOpen: () => !flyout.hidden,
    close: () => setOpen(false),
  });

  toggle.addEventListener('click', (e) => {
    // Stopped, or the opening click would reach the outside-click dismiss
    // below and close the flyout it just opened.
    e.stopPropagation();
    setOpen(flyout.hidden);
  });

  if (zoomOut) zoomOut.addEventListener('click', () => step(-1));
  if (tickList) {
    tickList.addEventListener('click', (e) => {
      const target = /** @type {Element} */ (e.target);
      const button = target.closest && target.closest('[data-zoom-tick-button]');
      if (!(button instanceof HTMLElement)) return;
      const zoom = Number(button.dataset.zoomTarget);
      if (Number.isFinite(zoom)) goTo(zoom);
    });
  }
  if (zoomIn) zoomIn.addEventListener('click', () => step(1));

  // The pending target clears when its move ends at the target. Not on
  // every `moveend`: a second tap's zoomTo stops the first animation, and
  // MapLibre fires `moveend` for that stop, short of the first target —
  // clearing there would leave a third quick tap stepping from the
  // intermediate zoom again. A gesture (an event carrying originalEvent)
  // starting or ending a move clears it too, since the reader has taken the
  // map somewhere else.
  /**
   * @param {{originalEvent?: Event}} [event]
   * @returns {void}
   */
  function settle(event) {
    if (pendingTarget === null) return;
    const arrived = Math.abs(map.getZoom() - pendingTarget) < core.EPSILON;
    if (arrived || (event && event.originalEvent)) {
      pendingTarget = null;
      render();
    }
  }
  map.on('moveend', settle);
  map.on('movestart', (event) => {
    if (event && event.originalEvent) settle(event);
  });

  // Outside-click dismiss — a tap on the map itself, which is no overlay
  // and so announces nothing to the registry. `click`, not `pointerdown`,
  // so − and + fire their own handlers before this one is asked.
  document.addEventListener('click', (e) => {
    if (flyout.hidden) return;
    const target = /** @type {Node} */ (e.target);
    if (toggle.contains(target) || flyout.contains(target)) return;
    setOpen(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !flyout.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });

  // 'zoom' alone would miss a tile zoom that changes when the SOURCES
  // change rather than the camera — a basemap swap, or a slow style
  // settling — hence 'idle' and the basemap event as well.
  map.on('zoom', render);
  map.on('idle', render);
  document.addEventListener('snowdesk:basemap-changed', render);
  document.addEventListener('snowdesk:overlay-visibility-changed', render);
  document.addEventListener('snowdesk:terrain-filter-changed', render);
  document.addEventListener('snowdesk:zoom-gates-changed', render);
  render();
})();
