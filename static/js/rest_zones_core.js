/*
 * static/js/rest_zones_core.js — the pure half of the "Wildlife rest zones"
 * map layer (SNOW-1384, behind the `route_drawing` flag).
 *
 * The zones are /api/rest-zones.geojson: FOEN's polygons for one rectangle,
 * each carrying whether it is legally binding (`binding`) and whether its
 * protected period covers the map's day (`in_force`, decided on the server
 * from `?d=`). rest_zones.js draws them and opens a sheet on a tap; this
 * module holds what that needs and touches neither the DOM nor the map.
 *
 * HOW A ZONE IS DRAWN. A binding zone is filled and outlined in red, a
 * recommended one in amber, more faintly. A zone out of its period is
 * drawn too, muted, so a reader planning a later day still sees where it
 * is. Every zone keeps a fill, faint or not: a tap anywhere inside one is
 * what opens it, and MapLibre answers a tap only from what a layer paints.
 *
 * Exports (frozen `self.pwaRestZonesCore`):
 *
 *   MINZOOM              — the zoom the zones are drawn and loaded from
 *   MAX_SPAN_DEG         — the widest rectangle the endpoint answers
 *   LOAD_PAD_DEG         — how far past the view a load reaches
 *   loadBox(bounds)      — the rectangle to load for a view, or null
 *   covers(box, bounds)  — whether a loaded rectangle holds a view
 *   dataUrl(base, box, date) — the endpoint URL for a rectangle and a day
 *   fillPaint()          — the fill layer's paint
 *   linePaint()          — the outline layer's paint
 *   pickZone(features)   — the zone a tap opens, or null
 */

// @ts-check

(function () {
  'use strict';

  /** From here the zones are drawn, loaded and tapped. */
  const MINZOOM = 11;

  /** The widest rectangle the endpoint answers, in degrees each way. */
  const MAX_SPAN_DEG = 1.0;

  /** A load reaches this far past each edge of the view, in degrees. */
  const LOAD_PAD_DEG = 0.05;

  // MapLibre paint cannot read a CSS custom property, so the colours are
  // literals here. Red for a zone the law closes, amber for one FOEN only
  // asks walkers to keep out of.
  const BINDING_COLOUR = '#c62828';
  const RECOMMENDED_COLOUR = '#e08a00';

  /**
   * @typedef {[number, number, number, number]} Box
   * @typedef {{id?: (string|number), properties?: ?Object<string, any>}} ZoneFeature
   */

  /**
   * The rectangle to load for a view: padded on every side, so a small pan
   * needs no new request, and rounded outward to the pad, so two near views
   * ask the same URL. The padding gives way first when the view is near the
   * endpoint's limit; a view wider than the limit loads nothing.
   *
   * @param {Box} bounds The view, west, south, east, north.
   * @returns {?Box}
   */
  function loadBox(bounds) {
    const fits = (/** @type {Box} */ b) =>
      b[2] - b[0] <= MAX_SPAN_DEG && b[3] - b[1] <= MAX_SPAN_DEG;
    for (const [step, pad] of [[LOAD_PAD_DEG, 1], [LOAD_PAD_DEG, 0], [0.001, 0]]) {
      const down = (/** @type {number} */ v) => Math.floor(v / step - pad) * step;
      const up = (/** @type {number} */ v) => Math.ceil(v / step + pad) * step;
      const round = (/** @type {number} */ v) => Math.round(v * 1000) / 1000;
      /** @type {Box} */
      const box = [
        round(down(bounds[0])),
        round(down(bounds[1])),
        round(up(bounds[2])),
        round(up(bounds[3])),
      ];
      if (fits(box)) return box;
    }
    return null;
  }

  /**
   * Whether a loaded rectangle holds the whole of a view.
   *
   * @param {?Box} box What was loaded, or null.
   * @param {Box} bounds The view.
   * @returns {boolean}
   */
  function covers(box, bounds) {
    return !!box
      && box[0] <= bounds[0] && box[1] <= bounds[1]
      && box[2] >= bounds[2] && box[3] >= bounds[3];
  }

  /**
   * The endpoint URL for one rectangle on one day.
   *
   * @param {string} base api:rest_zones_geojson.
   * @param {Box} box The rectangle.
   * @param {?string} date The map's day, YYYY-MM-DD; omitted, the server
   *   answers for today.
   * @returns {string}
   */
  function dataUrl(base, box, date) {
    const sep = base.includes('?') ? '&' : '?';
    const day = date ? '&d=' + encodeURIComponent(date) : '';
    return base + sep + 'bbox=' + box.join(',') + day;
  }

  /** @returns {any} The colour expression: red when binding, else amber. */
  function colour() {
    return ['case', ['==', ['get', 'binding'], true], BINDING_COLOUR, RECOMMENDED_COLOUR];
  }

  /**
   * The fill: a binding zone in force the strongest, a recommended zone
   * fainter, and either one out of its period fainter still.
   *
   * @returns {Object<string, any>}
   */
  function fillPaint() {
    return {
      'fill-color': colour(),
      'fill-opacity': [
        'case',
        ['!=', ['get', 'in_force'], true], 0.06,
        ['==', ['get', 'binding'], true], 0.22,
        0.12,
      ],
    };
  }

  /**
   * The outline: solid and opaque in force, faded out of its period.
   *
   * @returns {Object<string, any>}
   */
  function linePaint() {
    return {
      'line-color': colour(),
      'line-width': ['interpolate', ['linear'], ['zoom'], MINZOOM, 1, 15, 2.5],
      'line-opacity': ['case', ['==', ['get', 'in_force'], true], 0.9, 0.35],
    };
  }

  /**
   * The zone a tap opens: the topmost one under it. queryRenderedFeatures
   * answers topmost first, and a zone's outline and fill both answer for
   * the same zone.
   *
   * @param {?Array<ZoneFeature>} features What the tap landed on.
   * @returns {?ZoneFeature}
   */
  function pickZone(features) {
    if (!Array.isArray(features)) return null;
    for (const feature of features) {
      if (feature && feature.properties && feature.id != null) return feature;
    }
    return null;
  }

  self.pwaRestZonesCore = Object.freeze({
    MINZOOM: MINZOOM,
    MAX_SPAN_DEG: MAX_SPAN_DEG,
    LOAD_PAD_DEG: LOAD_PAD_DEG,
    BINDING_COLOUR: BINDING_COLOUR,
    RECOMMENDED_COLOUR: RECOMMENDED_COLOUR,
    loadBox: loadBox,
    covers: covers,
    dataUrl: dataUrl,
    fillPaint: fillPaint,
    linePaint: linePaint,
    pickZone: pickZone,
  });
})();
