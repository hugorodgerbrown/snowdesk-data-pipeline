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
    'gate-zoom': 'z%(zoom)s',
    'gate-terrain_filter': 'Terrain filter',
    'gate-route_slopes': 'Route slope colours',
    camera: 'Camera zoom %(zoom)s',
  });

  const face = toggle.querySelector('[data-zoom-face]');
  const zoomOut = flyout.querySelector('[data-zoom-step="out"]');
  const zoomIn = flyout.querySelector('[data-zoom-step="in"]');
  const marker = flyout.querySelector('[data-zoom-marker]');
  const tickList = flyout.querySelector('[data-zoom-ticks]');
  const gateList = flyout.querySelector('[data-zoom-gates]');
  const cameraLine = flyout.querySelector('[data-zoom-camera]');
  const tickTemplate = document.getElementById('map-zoom-tick-template');
  const gateTemplate = document.getElementById('map-zoom-gate-template');

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
   * Redraw the ticks and their rows, when they changed.
   *
   * @param {Array<{key: string, zoom: number, position: number, reached: boolean}>} ticks
   * @returns {void}
   */
  function renderTicks(ticks) {
    const signature = ticks.map((t) => t.key + ':' + t.zoom + ':' + t.reached).join('|');
    if (signature === drawnTicks) return;
    drawnTicks = signature;

    const tickNodes = [];
    const gateNodes = [];
    for (const tick of ticks) {
      const tickNode = cloneRow(tickTemplate);
      if (tickNode) {
        tickNode.dataset.zoomTick = tick.key;
        tickNode.toggleAttribute('data-reached', tick.reached);
        tickNode.style.left = (tick.position * 100).toFixed(2) + '%';
        const number = tickNode.querySelector('[data-zoom-tick-zoom]');
        if (number) number.textContent = String(tick.zoom);
        tickNodes.push(tickNode);
      }
      const gateNode = cloneRow(gateTemplate);
      if (gateNode) {
        gateNode.dataset.zoomGate = tick.key;
        gateNode.toggleAttribute('data-reached', tick.reached);
        const label = gateNode.querySelector('[data-zoom-gate-label]');
        if (label) label.textContent = gateLabel(tick.key);
        const zoom = gateNode.querySelector('[data-zoom-gate-zoom]');
        if (zoom) {
          zoom.textContent = self.pwaStrings.interpolate(STRINGS['gate-zoom'], { zoom: tick.zoom });
        }
        gateNodes.push(gateNode);
      }
    }
    if (tickList) tickList.replaceChildren(...tickNodes);
    if (gateList) {
      gateList.replaceChildren(...gateNodes);
      gateList.hidden = gateNodes.length === 0;
    }
  }

  /**
   * Repaint the face and, while it is open, the flyout.
   *
   * @returns {void}
   */
  function render() {
    const camera = map.getZoom();
    const whole = core.faceZoom(tileZoom(), camera);
    if (face) face.textContent = String(whole);
    toggle.setAttribute(
      'aria-label',
      self.pwaStrings.interpolate(STRINGS['toggle-label'], { zoom: whole }),
    );
    if (flyout.hidden) return;

    const min = map.getMinZoom();
    const max = map.getMaxZoom();
    const buttons = core.buttonState(camera, min, max);
    if (zoomOut) zoomOut.disabled = !buttons.canZoomOut;
    if (zoomIn) zoomIn.disabled = !buttons.canZoomIn;
    if (marker) marker.style.left = (core.scalePosition(camera, min, max) * 100).toFixed(2) + '%';

    const gates = window.pwaZoomGates ? window.pwaZoomGates.list() : [];
    renderTicks(core.ticks(gates, camera, min, max));

    if (cameraLine) {
      cameraLine.textContent = self.pwaStrings.interpolate(
        STRINGS.camera, { zoom: camera.toFixed(2) },
      );
    }
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

  if (zoomOut) zoomOut.addEventListener('click', () => map.zoomOut());
  if (zoomIn) zoomIn.addEventListener('click', () => map.zoomIn());

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
