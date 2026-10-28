/*
 * static/js/ski_tours_core.js — the pure half of "Find a route": choosing a
 * SAC ski tour from the map (SNOW-1278).
 *
 * The tours are /api/ski-tours.geojson: each tour's line (`kind: "tour"`,
 * carrying its uuid and its summit's key) and a point per summit or hut a
 * tour goes to (`kind: "summit"` or `"hut"`). ski_tours.js draws them and
 * hands this module what a tap landed on.
 *
 * A TAP NEVER PICKS A TOUR ON ITS OWN. A summit under the tap lists every
 * tour to it, up and down; otherwise one tour in reach opens its panel and
 * more than one lists them, which near a hut is nearly always (decided
 * 28 Oct 2026).
 *
 * Nothing here touches the DOM or the map.
 *
 * Exports (frozen `self.pwaSkiToursCore`):
 *
 *   MINZOOM                    — the zoom the tours are drawn from
 *   TAP_PX                     — how near a line a tap reaches, px
 *   LOAD_PAD_DEG               — how far past the view a load reaches
 *   resolveTap(features)       — what a tap means: a summit's list, one
 *                                tour, a list of tours, or nothing
 *   loadBox(bounds)            — the rectangle to load for a view
 *   covers(box, bounds)        — whether a loaded rectangle holds a view
 *   highlightFilter(uuids)     — the filter that draws only those tours
 *   farEnds(features, uuids)   — where each of these tours starts or ends
 *   fitsView(a, b, size, padding) — whether a tour already shows whole
 *   listUrl(base, choice)      — the list sheet's URL for a tap
 *   featureUrl(template, uuid, sample) — a tour's Feature URL
 */

// @ts-check

(function () {
  'use strict';

  /** From here the vector tours replace swisstopo's raster. */
  const MINZOOM = 12;

  /** A tap reaches a line this many pixels either side (about a fingertip). */
  const TAP_PX = 20;

  /** A load reaches this far past each edge of the view, in degrees. */
  const LOAD_PAD_DEG = 0.05;

  /** The widest rectangle the endpoint answers, as ski-network's cap. */
  const MAX_SPAN_DEG = 0.5;

  /**
   * @typedef {{properties?: ?Object<string, any>}} TapFeature
   * @typedef {{kind: 'summit', key: string}
   *   | {kind: 'tour', uuid: string}
   *   | {kind: 'list', uuids: Array<string>}} TapChoice
   * @typedef {[number, number, number, number]} Box west, south, east, north
   */

  /**
   * What a tap on the tours means.
   *
   * @param {Array<TapFeature>} features What the tap's box hit, summits
   *   and tour lines in any order (a tour clipped into several tiles may
   *   appear more than once).
   * @returns {?TapChoice} A summit's list when a summit or hut is under
   *   the tap; one tour when only one is in reach; the list of them when
   *   more are; null when nothing is.
   */
  function resolveTap(features) {
    /** @type {Array<string>} */
    const uuids = [];
    for (const feature of features || []) {
      const props = (feature && feature.properties) || {};
      if ((props.kind === 'summit' || props.kind === 'hut') && props.key) {
        return { kind: 'summit', key: String(props.key) };
      }
      if (props.kind === 'tour' && props.uuid && !uuids.includes(String(props.uuid))) {
        uuids.push(String(props.uuid));
      }
    }
    if (!uuids.length) return null;
    if (uuids.length === 1) return { kind: 'tour', uuid: uuids[0] };
    return { kind: 'list', uuids: uuids };
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
   *   endpoint answers (the tours are not drawn that far out anyway).
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
   * Whether a tour's box, as drawn on screen, sits wholly inside the view
   * clear of its padding: when it does, opening the tour leaves the camera
   * where the reader put it (SNOW-1284).
   *
   * @param {{x: number, y: number}} a One corner of the box, in px.
   * @param {{x: number, y: number}} b The opposite corner, in px.
   * @param {{width: number, height: number}} size The map's canvas.
   * @param {{top: number, right: number, bottom: number, left: number}} padding
   *   What the panels and the fit's margins cover, in px.
   * @returns {boolean}
   */
  function fitsView(a, b, size, padding) {
    return Math.min(a.x, b.x) >= padding.left
      && Math.max(a.x, b.x) <= size.width - padding.right
      && Math.min(a.y, b.y) >= padding.top
      && Math.max(a.y, b.y) <= size.height - padding.bottom;
  }

  /**
   * The MapLibre filter that keeps only these tours.
   *
   * @param {Array<string>} uuids
   * @returns {Array<any>}
   */
  function highlightFilter(uuids) {
    return ['in', ['get', 'uuid'], ['literal', (uuids || []).slice()]];
  }

  /**
   * The far end of each of these tours: where it starts, for a way up, or
   * where it finishes, for a way down. The end of any of its parts farthest
   * from the tour's own summit, so a line drawn the other way round, or in
   * parts stored out of order, is still read right; with its summit not
   * loaded, a way up's first point and a way down's last.
   *
   * @param {Array<any>} features A loaded collection's features, summits
   *   and tours together.
   * @param {Array<string>} uuids The tours to mark.
   * @returns {{type: string, features: Array<any>}} One Point per tour.
   */
  function farEnds(features, uuids) {
    const wanted = new Set(uuids || []);
    /** @type {Map<string, Array<number>>} */
    const summits = new Map();
    for (const f of features || []) {
      const p = f && f.properties;
      if (p && (p.kind === 'summit' || p.kind === 'hut') && f.geometry) {
        summits.set(p.key, f.geometry.coordinates);
      }
    }
    /** @type {Array<any>} */
    const ends = [];
    const seen = new Set();
    for (const f of features || []) {
      const p = f && f.properties;
      if (!p || p.kind !== 'tour' || !wanted.has(p.uuid) || seen.has(p.uuid)) continue;
      const g = f.geometry;
      const parts = !g ? [] : g.type === 'MultiLineString' ? g.coordinates : [g.coordinates];
      const line = parts.filter((/** @type {any} */ part) => part && part.length);
      if (!line.length) continue;
      seen.add(p.uuid);
      const first = line[0][0];
      const last = line[line.length - 1][line[line.length - 1].length - 1];
      const summit = summits.get(p.summit);
      let end;
      if (summit) {
        // The part ends farthest from the summit: swisstopo stores a tour's
        // parts in no particular order, so its first point need not be where
        // it starts. Degrees, the longitude scaled by the latitude: only
        // which end is farther matters.
        const k = Math.cos((summit[1] * Math.PI) / 180);
        const d = (/** @type {Array<number>} */ c) =>
          ((c[0] - summit[0]) * k) ** 2 + (c[1] - summit[1]) ** 2;
        end = first;
        for (const part of line) {
          for (const c of [part[0], part[part.length - 1]]) {
            if (d(c) > d(end)) end = c;
          }
        }
      } else {
        end = p.down ? last : first;
      }
      ends.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [end[0], end[1]] },
        properties: { uuid: p.uuid },
      });
    }
    return { type: 'FeatureCollection', features: ends };
  }

  /**
   * The list sheet's URL for a tap's choice.
   *
   * @param {string} base routes:ski_tour_list.
   * @param {TapChoice} choice A summit's or a list's choice.
   * @returns {?string} Null for a single tour, which has no list.
   */
  function listUrl(base, choice) {
    if (!base || !choice) return null;
    const join = base.includes('?') ? '&' : '?';
    if (choice.kind === 'summit') return base + join + 'summit=' + encodeURIComponent(choice.key);
    if (choice.kind === 'list') return base + join + 'tours=' + choice.uuids.map(encodeURIComponent).join(',');
    return null;
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
    TAP_PX: TAP_PX,
    LOAD_PAD_DEG: LOAD_PAD_DEG,
    resolveTap: resolveTap,
    loadBox: loadBox,
    covers: covers,
    highlightFilter: highlightFilter,
    farEnds: farEnds,
    fitsView: fitsView,
    listUrl: listUrl,
    featureUrl: featureUrl,
  });
})();
