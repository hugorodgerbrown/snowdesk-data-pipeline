/*
 * static/js/ski_tours_core.js — the pure half of "Find a route": choosing a
 * SAC ski tour from the map (SNOW-1278).
 *
 * The tours are /api/ski-tours.geojson: each tour's line (`kind: "tour"`,
 * carrying its uuid and its summit's key) and a point per summit or hut a
 * tour goes to (`kind: "summit"` or `"hut"`). ski_tours.js draws them and
 * hands this module what a tap landed on.
 *
 * A TAP OPENS THE NEAREST TOUR (SNOW-1306). The tours are drawn with a way
 * up and a way down told apart, so the reader picks one by sight and the
 * tap takes the line nearest it. A summit is a label, never a target
 * (Hugo, 30 Oct 2026: "Just a label for now. Users can see the routes."),
 * so the list sheet a summit and a crowded tap once opened is gone.
 *
 * Nothing here touches the DOM or the map.
 *
 * WHAT IS LOADED follows the zoom (SNOW-1285): from MINZOOM the whole set
 * at once, every line thinned (`?overview=1`), and from DETAIL_MINZOOM the
 * view's own rectangle, at full detail. Both are tapped the same way.
 *
 * Exports (frozen `self.pwaSkiToursCore`):
 *
 *   MINZOOM                    — the zoom the tours and summits are drawn from
 *   LABEL_MINZOOM              — the zoom the summits are named from
 *   DETAIL_MINZOOM             — the zoom the view's own detail loads from
 *   loadMode(zoom)             — what a zoom loads: none, overview or detail
 *   TAP_PX                     — how near a line a tap reaches, px
 *   LOAD_PAD_DEG               — how far past the view a load reaches
 *   nearestTour(features, point, project) — the tour a tap opens, or null
 *   loadBox(bounds)            — the rectangle to load for a view
 *   covers(box, bounds)        — whether a loaded rectangle holds a view
 *   featureUrl(template, uuid, sample) — a tour's Feature URL
 */

// @ts-check

(function () {
  'use strict';

  /** From here the tours and summits are drawn, and can be tapped. */
  const MINZOOM = 9;

  /** From here a summit carries its name: at z9 the names would collide. */
  const LABEL_MINZOOM = 10;

  /** From here the view's rectangle loads at full detail. */
  const DETAIL_MINZOOM = 12;

  /** A tap reaches a line this many pixels either side (about a fingertip). */
  const TAP_PX = 20;

  /** A load reaches this far past each edge of the view, in degrees. */
  const LOAD_PAD_DEG = 0.05;

  /** The widest rectangle the endpoint answers, as ski-network's cap. */
  const MAX_SPAN_DEG = 0.5;

  /**
   * @typedef {{properties?: ?Object<string, any>, geometry?: ?{type: string, coordinates: any}}} TapFeature
   * @typedef {[number, number, number, number]} Box west, south, east, north
   */

  /**
   * The squared distance, px, from a point to a segment.
   *
   * @param {{x: number, y: number}} p
   * @param {{x: number, y: number}} a
   * @param {{x: number, y: number}} b
   * @returns {number}
   */
  function segmentDistance2(p, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const length2 = dx * dx + dy * dy;
    const t = length2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2)) : 0;
    const x = a.x + t * dx - p.x;
    const y = a.y + t * dy - p.y;
    return x * x + y * y;
  }

  /**
   * The tour a tap opens: the one whose line passes nearest the tap.
   *
   * @param {Array<TapFeature>} features The tour lines the tap's box hit,
   *   in any order (a tour clipped into several tiles may appear more
   *   than once, in parts).
   * @param {{x: number, y: number}} point The tap, screen px.
   * @param {function(Array<number>): {x: number, y: number}} project A
   *   coordinate to screen px (the map's own projection).
   * @returns {?string} The tour's uuid, or null when no tour was hit.
   */
  function nearestTour(features, point, project) {
    /** @type {?string} */
    let best = null;
    let bestD = Infinity;
    for (const feature of features || []) {
      const props = (feature && feature.properties) || {};
      if (props.kind !== 'tour' || !props.uuid) continue;
      const g = feature.geometry;
      const parts = !g ? [] : g.type === 'MultiLineString' ? g.coordinates : [g.coordinates];
      for (const part of parts) {
        if (!Array.isArray(part)) continue;
        const pts = part.map(project);
        for (let i = 0; i < pts.length; i++) {
          const d = segmentDistance2(point, pts[i], pts[Math.min(i + 1, pts.length - 1)]);
          if (d < bestD) {
            bestD = d;
            best = String(props.uuid);
          }
        }
      }
    }
    return best;
  }

  /**
   * What a zoom loads: nothing, every tour thinned, or the view's detail.
   *
   * @param {number} zoom
   * @returns {'none'|'overview'|'detail'}
   */
  function loadMode(zoom) {
    if (zoom < MINZOOM) return 'none';
    return zoom < DETAIL_MINZOOM ? 'overview' : 'detail';
  }

  /**
   * The rectangle to load for a view: the view padded on every side, so a
   * small pan needs no new request, and rounded outward to the pad so two
   * near views ask the same URL (and the browser's cache answers).
   *
   * A wide screen at z12 (2560 px spans about 0.44°) fits the endpoint's
   * limit when the padding does not, so the padding gives way first: the
   * view rounded outward to the pad, then the view itself to the metre.
   * Only a view that is itself wider than the limit loads nothing.
   *
   * @param {Box} bounds The view, west, south, east, north.
   * @returns {?Box} The rectangle, or null when the view is wider than the
   *   endpoint answers (the overview is loaded that far out instead).
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
   * A tour's Feature URL.
   *
   * @param {string} template routes:ski_tour_feature with `__UUID__`.
   * @param {string} uuid
   * @param {boolean} sample Ask the server to sample the terrain now.
   * @returns {string}
   */
  function featureUrl(template, uuid, sample) {
    const url = template.replace('__UUID__', encodeURIComponent(uuid));
    return sample ? url + (url.includes('?') ? '&' : '?') + 'sample=1' : url;
  }

  self.pwaSkiToursCore = Object.freeze({
    MINZOOM: MINZOOM,
    LABEL_MINZOOM: LABEL_MINZOOM,
    DETAIL_MINZOOM: DETAIL_MINZOOM,
    loadMode: loadMode,
    TAP_PX: TAP_PX,
    LOAD_PAD_DEG: LOAD_PAD_DEG,
    nearestTour: nearestTour,
    loadBox: loadBox,
    covers: covers,
    featureUrl: featureUrl,
  });
})();
