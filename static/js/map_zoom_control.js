/*
 * static/js/map_zoom_control.js — the map's zoom control (SNOW-1077).
 *
 * The roundel at the top of the bottom-left stack and the flyout it opens
 * (public/partials/_map_zoom_control.html). The face shows the whole
 * camera zoom and the roundel's accessible name says it ("Zoom level
 * 13"). The flyout holds − and +, a scale across the map's zoom range
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
 * and keeps from it — under DEBUG only, where the template renders the
 * line — the camera zoom to two decimals.
 *
 * CAMERA ZOOM, AND WHEN IT IS ONLY ENLARGING. The face is floor(camera)
 * (zoom_control_core.faceZoom). It was the tile zoom until 2026-11-06,
 * which a basemap clamps to its own maxzoom, so at camera 15.6 the face
 * said 14 beside a flyout saying 15.6. Past that maxzoom MapLibre draws no
 * new detail, only enlarges the last tiles, and the control says so: from
 * maxzoom + 1 the roundel's ring goes dashed (`data-enlarged`) and, in the
 * flyout, the track above that level goes dotted and the marker's reading
 * turns hollow (Hugo, 2026-11-06). The maxzoom is read off the style's
 * first vector source (`tileMaxZoom` below), since each basemap has its
 * own; a style with none never reads as enlarged.
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
 * as `map-zoom-flyout`, the same way #basemap-menu does
 * (map_basemap_picker.js), and closes on an outside click and on Escape.
 * It opens into the crook between the stack and the date row, where the
 * legend card opens, so it needs no measuring.
 *
 * LOAD ORDER: runs at parse time and reads `MAP`, so it loads after map.js,
 * and after zoom_control_core.js. Outside the map bundle (nothing in the
 * bundle reads it), so its tag sits below the bundle's contiguous run — see
 * tests/map/test_map_script_order.py.
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
    'toggle-label-enlarged': 'Zoom level %(zoom)s, enlarged',
    'gate-region_select': 'Region selector',
    'gate-ski_routes': 'SAC ski touring',
    'gate-rest_zones': 'Wildlife rest zones',
    'gate-terrain_filter': 'Steep terrain',
    'gate-problem_terrain': 'Sync problem terrain',
    'gate-route_slopes': 'Route slope angle',
    'threshold-from': '≥%(zoom)s',
    'threshold-up-to': '≤%(zoom)s',
    camera: 'Camera zoom %(zoom)s',
  });

  const face = toggle.querySelector('[data-zoom-face]');
  const zoomOut = flyout.querySelector('[data-zoom-step="out"]');
  const zoomIn = flyout.querySelector('[data-zoom-step="in"]');
  const marker = flyout.querySelector('[data-zoom-marker]');
  const enlargedTrack = flyout.querySelector('[data-zoom-enlarged-track]');
  const markerValue = flyout.querySelector('[data-zoom-marker-value]');
  // The readout's one decimal, in the page's language (12,4 in German).
  const tenths = new Intl.NumberFormat(document.documentElement.lang || undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  const tickList = flyout.querySelector('[data-zoom-ticks]');
  const levelList = flyout.querySelector('[data-zoom-levels]');
  const scale = flyout.querySelector('[data-zoom-scale]');
  const connectorLayer = flyout.querySelector('[data-zoom-connectors]');
  const cameraLine = flyout.querySelector('[data-zoom-camera]');
  const tickTemplate = document.getElementById('map-zoom-tick-template');

  /**
   * The highest zoom the basemap publishes tiles for.
   *
   * Read from the FIRST vector source: every vector source in these styles
   * shares one tile pyramid, so one answer serves. A style with none (the
   * offline fallback) yields null, and the map never reads as enlarged.
   *
   * @returns {number|null}
   */
  function tileMaxZoom() {
    try {
      const caches = map.style && map.style.sourceCaches;
      if (!caches) return null;
      for (const key of Object.keys(caches)) {
        const source = caches[key] && caches[key]._source;
        if (!source || source.type !== 'vector') continue;
        return Number.isFinite(source.maxzoom) ? source.maxzoom : null;
      }
    } catch (_e) {
      // The style is mid-swap, or MapLibre's internals moved. Saying
      // nothing about enlargement is the safe answer.
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

  const SVG_NS = 'http://www.w3.org/2000/svg';

  /**
   * The scale's geometry in px, read from the flyout's CSS custom
   * properties (map.css .map-zoom-flyout) so the stylesheet stays the one
   * place they are set; the fallbacks are the same values, for a page
   * (or jsdom) with no stylesheet.
   *
   * @returns {{height: number, trackX: number, labelX: number, pitch: number}}
   */
  function geometry() {
    const style = getComputedStyle(flyout);
    const px = (/** @type {string} */ name, /** @type {number} */ fallback) => {
      const value = parseFloat(style.getPropertyValue(name));
      return Number.isFinite(value) && value > 0 ? value : fallback;
    };
    const measured = scale ? scale.clientHeight : 0;
    return {
      height: measured > 0 ? measured : px('--zoom-scale-h', 280),
      trackX: px('--zoom-track-x', 44),
      labelX: px('--zoom-label-x', 104),
      pitch: px('--zoom-label-pitch', 30),
    };
  }

  // The ticks last drawn, as one string, so a frame of a pinch that moves
  // no tick across its gate rebuilds nothing.
  let drawnTicks = null;

  /**
   * Redraw the ticks and their connectors, when they changed.
   *
   * Each tick names one zoom-gated feature and its threshold (≥12, ≤10),
   * in the label column, joined by a curve to a node on the axis at the
   * level it becomes available (zoom_control_core.labelRows places the
   * labels, connectorPath draws the curve). It is bold, and its curve
   * dark, while the camera is at a zoom where the feature works, switched
   * on or not: the scale answers "what can I do at this zoom", which is
   * what a reader who switched something on and saw nothing needs.
   *
   * @param {Array<{key: string, zoom: number, position: number, reached: boolean, upTo: boolean}>} ticks
   * @returns {void}
   */
  function renderTicks(ticks) {
    const geo = geometry();
    // The rows themselves change only when the gates (or the scale's
    // size) do. A zoom past a threshold restyles the row in place rather
    // than rebuilding it, so a row that holds keyboard focus — the one
    // just tapped, say — keeps it while the map animates through its
    // threshold.
    const signature = geo.height + '#' + ticks.map((t) => [t.key, t.zoom, t.upTo].join(':')).join('|');
    if (signature === drawnTicks && tickList) {
      ticks.forEach((tick, i) => {
        tickList.children[i]?.toggleAttribute('data-reached', tick.reached);
        connectorLayer?.children[i]?.toggleAttribute('data-reached', tick.reached);
      });
      return;
    }
    drawnTicks = signature;

    const rows = core.labelRows(ticks, geo.height, geo.pitch);
    const tickNodes = [];
    const connectorNodes = [];
    ticks.forEach((tick, i) => {
      const tickNode = cloneRow(tickTemplate);
      if (!tickNode) return;
      const levelY = (1 - tick.position) * geo.height;
      tickNode.dataset.zoomTick = tick.key;
      tickNode.toggleAttribute('data-reached', tick.reached);
      tickNode.style.top = rows[i].toFixed(2) + 'px';
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
      tickNodes.push(tickNode);

      // One <g> per tick, in the ticks' order, so the restyle above can
      // find a tick's connector by its index.
      const group = document.createElementNS(SVG_NS, 'g');
      group.setAttribute('class', 'map-zoom-connector');
      group.setAttribute('data-zoom-connector', tick.key);
      group.toggleAttribute('data-reached', tick.reached);
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('class', 'map-zoom-connector-line');
      path.setAttribute('d', core.connectorPath(geo.trackX, levelY, geo.labelX, rows[i]));
      const node = document.createElementNS(SVG_NS, 'circle');
      node.setAttribute('class', 'map-zoom-connector-node');
      node.setAttribute('cx', String(geo.trackX));
      node.setAttribute('cy', levelY.toFixed(2));
      node.setAttribute('r', '3');
      group.append(path, node);
      connectorNodes.push(group);
    });
    if (tickList) tickList.replaceChildren(...tickNodes);
    if (connectorLayer) connectorLayer.replaceChildren(...connectorNodes);
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
    const whole = core.faceZoom(camera);
    const tileMax = tileMaxZoom();
    const enlarged = core.isEnlarged(camera, tileMax);
    const faceSignature = whole + (enlarged ? '+' : '');
    if (faceSignature !== drawnFace) {
      drawnFace = faceSignature;
      if (face) face.textContent = String(whole);
      toggle.toggleAttribute('data-enlarged', enlarged);
      toggle.setAttribute(
        'aria-label',
        self.pwaStrings.interpolate(
          STRINGS[enlarged ? 'toggle-label-enlarged' : 'toggle-label'], { zoom: whole },
        ),
      );
    }
    if (flyout.hidden) return;

    const min = map.getMinZoom();
    const max = map.getMaxZoom();
    if (enlargedTrack instanceof HTMLElement) {
      const from = core.enlargedFrom(tileMax, max);
      enlargedTrack.hidden = from === null;
      if (from !== null) {
        enlargedTrack.style.height = ((1 - core.scalePosition(from, min, max)) * 100).toFixed(2) + '%';
      }
    }
    // Judged from where the map is going, so + goes dead as soon as a tap
    // has committed the map to its maximum, not when the animation lands.
    const buttons = core.buttonState(pendingTarget ?? camera, min, max);
    setStepEnabled(zoomOut, buttons.canZoomOut, zoomIn);
    setStepEnabled(zoomIn, buttons.canZoomIn, zoomOut);
    if (marker) {
      marker.toggleAttribute('data-enlarged', enlarged);
      marker.style.top = ((1 - core.scalePosition(camera, min, max)) * 100).toFixed(2) + '%';
    }
    if (markerValue) markerValue.textContent = tenths.format(core.markerZoom(camera));

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

  // 'zoom' alone would miss a tile maximum that changes when the SOURCES
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
