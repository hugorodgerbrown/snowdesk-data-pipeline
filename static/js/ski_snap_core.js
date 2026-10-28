/*
 * static/js/ski_snap_core.js — the pure half of snapping a drawn route to
 * swisstopo's ski-touring network (SNOW-1277).
 *
 * The network is /api/ski-network.geojson: segments that meet at shared
 * ends, each a MultiLineString whose ends are exact (the service keeps them
 * when it simplifies), so two segments meeting at a junction share the very
 * same coordinate. That makes the network a graph with no tolerance needed:
 * a NODE is a line end, keyed by its coordinate; an EDGE is one line.
 *
 * Nothing here touches the DOM or the map; route_draw.js calls it with
 * plain arrays. A point is `[lon, lat]`, GeoJSON axis order.
 *
 * Exports (frozen `self.pwaSkiSnapCore`):
 *
 *   SNAP_PX                         — how near a tap must be to snap, px
 *   DETOUR_CAP                      — the longest a routed leg may be, as a
 *                                     multiple of the straight distance
 *   metresPerPixel(lat, zoom)       — the ground a screen pixel covers
 *   buildGraph(features)            — the graph of a FeatureCollection's lines
 *   nearestOnNetwork(graph, p, m)   — the nearest place on the network
 *                                     within m metres of p, or null
 *   shortestPath(graph, a, b)       — the shortest way along the network
 *                                     between two such places, or null
 *   legFor(graph, from, to, maxM)   — the points one tap adds: along the
 *                                     network when both ends are on it and
 *                                     the way round is not absurd, else a
 *                                     straight line
 */

// @ts-check

(function () {
  'use strict';

  /** A tap within this many pixels of a line snaps to it (decided 28 Oct 2026). */
  const SNAP_PX = 24;

  /**
   * A routed leg longer than this many times the straight distance is not
   * what the drawer meant — the two places are on the network but not
   * joined near by — so the leg is drawn straight instead.
   */
  const DETOUR_CAP = 3;

  /** A point this close to the network is taken to be on it, in metres. */
  const ON_NETWORK_M = 2;

  const EARTH_RADIUS_M = 6371008.8;
  const EQUATOR_M = 40075016.686;
  const RAD = Math.PI / 180;

  /**
   * @typedef {[number, number]} Point
   * @typedef {{coords: Array<Point>, cumulative: Array<number>, length: number, from: string, to: string}} Edge
   * @typedef {{edges: Array<Edge>, adjacency: Map<string, Array<{edge: number, node: string}>>}} Graph
   * @typedef {{edge: number, offset: number, point: Point, distance: number}} Snap
   */

  /**
   * The ground one screen pixel covers, in metres, for MapLibre's 512 px
   * tiles.
   *
   * @param {number} lat
   * @param {number} zoom
   * @returns {number}
   */
  function metresPerPixel(lat, zoom) {
    return (EQUATOR_M * Math.cos(lat * RAD)) / (512 * Math.pow(2, zoom));
  }

  /**
   * The great-circle distance between two points, in metres.
   *
   * @param {Point} a
   * @param {Point} b
   * @returns {number}
   */
  function haversineM(a, b) {
    const dLat = (b[1] - a[1]) * RAD;
    const dLon = (b[0] - a[0]) * RAD;
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /**
   * A node's key: its coordinate, as the service rounded it.
   *
   * @param {Point} point
   * @returns {string}
   */
  function nodeKey(point) {
    return point[0].toFixed(6) + ',' + point[1].toFixed(6);
  }

  /**
   * Round a point to the six places the drawn route keeps.
   *
   * @param {Point} point
   * @returns {Point}
   */
  function round6(point) {
    return [Math.round(point[0] * 1e6) / 1e6, Math.round(point[1] * 1e6) / 1e6];
  }

  /**
   * The graph of every line in a FeatureCollection.
   *
   * @param {Array<any>} features GeoJSON Features with LineString or
   *   MultiLineString geometry.
   * @returns {Graph}
   */
  function buildGraph(features) {
    /** @type {Array<Edge>} */
    const edges = [];
    /** @type {Map<string, Array<{edge: number, node: string}>>} */
    const adjacency = new Map();
    const link = (/** @type {string} */ from, /** @type {number} */ edge, /** @type {string} */ to) => {
      const list = adjacency.get(from);
      if (list) list.push({ edge: edge, node: to });
      else adjacency.set(from, [{ edge: edge, node: to }]);
    };
    (features || []).forEach(function (feature) {
      const geometry = feature && feature.geometry;
      if (!geometry) return;
      const lines =
        geometry.type === 'LineString' ? [geometry.coordinates] :
        geometry.type === 'MultiLineString' ? geometry.coordinates : [];
      lines.forEach(function (/** @type {Array<Point>} */ coords) {
        if (!Array.isArray(coords) || coords.length < 2) return;
        const cumulative = [0];
        for (let i = 1; i < coords.length; i += 1) {
          cumulative.push(cumulative[i - 1] + haversineM(coords[i - 1], coords[i]));
        }
        const from = nodeKey(coords[0]);
        const to = nodeKey(coords[coords.length - 1]);
        const index = edges.length;
        edges.push({ coords: coords, cumulative: cumulative, length: cumulative[cumulative.length - 1], from: from, to: to });
        link(from, index, to);
        link(to, index, from);
      });
    });
    return { edges: edges, adjacency: adjacency };
  }

  /**
   * The nearest place on the network to `point`, within `maxM` metres.
   *
   * Each piece of each line is measured in a flat frame centred on the
   * point — exact enough over the few tens of metres a snap spans.
   *
   * @param {Graph} graph
   * @param {Point} point
   * @param {number} maxM
   * @returns {?Snap}
   */
  function nearestOnNetwork(graph, point, maxM) {
    const kx = Math.cos(point[1] * RAD) * RAD * EARTH_RADIUS_M;
    const ky = RAD * EARTH_RADIUS_M;
    const limitDeg = maxM / ky / Math.max(Math.cos(point[1] * RAD), 0.01);
    /** @type {?Snap} */
    let best = null;
    graph.edges.forEach(function (edge, index) {
      const coords = edge.coords;
      for (let i = 1; i < coords.length; i += 1) {
        const a = coords[i - 1];
        const b = coords[i];
        // Cheap reject: the piece's box is nowhere near.
        if (
          Math.min(a[0], b[0]) > point[0] + limitDeg ||
          Math.max(a[0], b[0]) < point[0] - limitDeg ||
          Math.min(a[1], b[1]) > point[1] + limitDeg ||
          Math.max(a[1], b[1]) < point[1] - limitDeg
        ) continue;
        const ax = (a[0] - point[0]) * kx;
        const ay = (a[1] - point[1]) * ky;
        const dx = (b[0] - a[0]) * kx;
        const dy = (b[1] - a[1]) * ky;
        const lengthSq = dx * dx + dy * dy;
        const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSq));
        const px = ax + t * dx;
        const py = ay + t * dy;
        const distance = Math.sqrt(px * px + py * py);
        if (distance <= maxM && (!best || distance < best.distance)) {
          const pieceM = edge.cumulative[i] - edge.cumulative[i - 1];
          best = {
            edge: index,
            offset: edge.cumulative[i - 1] + t * pieceM,
            point: round6([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]),
            distance: distance,
          };
        }
      }
    });
    return best;
  }

  /**
   * The coordinates of one edge between two offsets along it, in the
   * direction asked for, the ends interpolated.
   *
   * @param {Edge} edge
   * @param {number} fromM
   * @param {number} toM
   * @returns {Array<Point>}
   */
  function subline(edge, fromM, toM) {
    const at = (/** @type {number} */ offset) => {
      const c = edge.cumulative;
      let i = 1;
      while (i < c.length - 1 && c[i] < offset) i += 1;
      const span = c[i] - c[i - 1];
      const t = span === 0 ? 0 : Math.max(0, Math.min(1, (offset - c[i - 1]) / span));
      const a = edge.coords[i - 1];
      const b = edge.coords[i];
      return round6([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
    };
    const lo = Math.min(fromM, toM);
    const hi = Math.max(fromM, toM);
    /** @type {Array<Point>} */
    const out = [at(lo)];
    edge.cumulative.forEach(function (offset, i) {
      if (offset > lo && offset < hi) out.push(edge.coords[i]);
    });
    out.push(at(hi));
    return fromM <= toM ? out : out.reverse();
  }

  /**
   * The coordinates of a whole edge walked from `node`.
   *
   * @param {Edge} edge
   * @param {string} node
   * @returns {Array<Point>}
   */
  function walk(edge, node) {
    return node === edge.from ? edge.coords.slice() : edge.coords.slice().reverse();
  }

  /**
   * Dijkstra over the nodes, from both ends of `a`'s edge to both ends of
   * `b`'s. Returns the node path and its cost, or null when the two are
   * not connected.
   *
   * Which END of `a`'s and `b`'s edges the path leaves and joins by is
   * returned as an offset along the edge (0 or its length), not read back
   * from the node: a closed line's two ends are one node, and reading the
   * direction from the key alone walked the whole loop the wrong way.
   *
   * @param {Graph} graph
   * @param {Snap} a
   * @param {Snap} b
   * @returns {?{cost: number, start: string, end: string, startAt: number, endAt: number, steps: Array<{edge: number, from: string}>}}
   */
  function search(graph, a, b) {
    const ea = graph.edges[a.edge];
    const eb = graph.edges[b.edge];
    /** @type {Map<string, number>} */
    const cost = new Map();
    /** @type {Map<string, {edge: number, from: string}|null>} */
    const via = new Map();
    /** @type {Array<[number, string]>} */
    const heap = [];
    const push = (/** @type {number} */ c, /** @type {string} */ node) => {
      heap.push([c, node]);
      let i = heap.length - 1;
      while (i > 0) {
        const up = (i - 1) >> 1;
        if (heap[up][0] <= heap[i][0]) break;
        [heap[up], heap[i]] = [heap[i], heap[up]];
        i = up;
      }
    };
    const pop = () => {
      const top = heap[0];
      const last = /** @type {[number, string]} */ (heap.pop());
      if (heap.length) {
        heap[0] = last;
        let i = 0;
        for (;;) {
          const l = 2 * i + 1;
          const r = l + 1;
          let m = i;
          if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
          if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
          if (m === i) break;
          [heap[m], heap[i]] = [heap[i], heap[m]];
          i = m;
        }
      }
      return top;
    };
    /** Where along `a`'s edge each seeded node was left from. */
    /** @type {Map<string, number>} */
    const startAt = new Map();
    const seed = (
      /** @type {string} */ node,
      /** @type {number} */ c,
      /** @type {number} */ at,
    ) => {
      if (c < (cost.get(node) ?? Infinity)) {
        cost.set(node, c);
        via.set(node, null);
        startAt.set(node, at);
        push(c, node);
      }
    };
    seed(ea.from, a.offset, 0);
    seed(ea.to, ea.length - a.offset, ea.length);
    /** Each exit node's cost onto `b`, and where along `b`'s edge it joins. */
    /** @type {Map<string, {cost: number, at: number}>} */
    const exits = new Map();
    const exit = (/** @type {string} */ node, /** @type {number} */ c, /** @type {number} */ at) => {
      const held = exits.get(node);
      if (!held || c < held.cost) exits.set(node, { cost: c, at: at });
    };
    exit(eb.from, b.offset, 0);
    exit(eb.to, eb.length - b.offset, eb.length);
    let best = Infinity;
    let end = '';
    const done = new Set();
    while (heap.length) {
      const [c, node] = pop();
      if (done.has(node)) continue;
      done.add(node);
      if (c >= best) break;
      const out = exits.get(node);
      if (out !== undefined && c + out.cost < best) {
        best = c + out.cost;
        end = node;
      }
      (graph.adjacency.get(node) || []).forEach(function (step) {
        const next = c + graph.edges[step.edge].length;
        if (next < (cost.get(step.node) ?? Infinity)) {
          cost.set(step.node, next);
          via.set(step.node, { edge: step.edge, from: node });
          push(next, step.node);
        }
      });
    }
    if (!end) return null;
    /** @type {Array<{edge: number, from: string}>} */
    const steps = [];
    let node = end;
    let hop = via.get(node);
    while (hop) {
      steps.unshift(hop);
      node = hop.from;
      hop = via.get(node);
    }
    return {
      cost: best,
      start: node,
      end: end,
      startAt: /** @type {number} */ (startAt.get(node)),
      endAt: /** @type {{cost: number, at: number}} */ (exits.get(end)).at,
      steps: steps,
    };
  }

  /**
   * The shortest way along the network from `a` to `b`, or null when no
   * way joins them.
   *
   * @param {Graph} graph
   * @param {Snap} a
   * @param {Snap} b
   * @returns {?{coordinates: Array<Point>, lengthM: number}}
   */
  function shortestPath(graph, a, b) {
    const ea = graph.edges[a.edge];
    const eb = graph.edges[b.edge];
    const direct = a.edge === b.edge ? Math.abs(b.offset - a.offset) : Infinity;
    const found = search(graph, a, b);
    if (direct === Infinity && !found) return null;
    if (!found || direct <= found.cost) {
      return { coordinates: subline(ea, a.offset, b.offset), lengthM: direct };
    }
    /** @type {Array<Point>} */
    let coordinates = subline(ea, a.offset, found.startAt);
    found.steps.forEach(function (step) {
      coordinates = coordinates.concat(walk(graph.edges[step.edge], step.from).slice(1));
    });
    const tail = subline(eb, found.endAt, b.offset);
    coordinates = coordinates.concat(tail.slice(1));
    return { coordinates: coordinates, lengthM: found.cost };
  }

  /**
   * The points one tap adds to a drawn route, the previous point excluded.
   *
   * The tap snaps when the network passes within `maxM` of it. The leg is
   * then routed along the network when the previous point is on it too and
   * the way round is no longer than DETOUR_CAP times the straight distance;
   * otherwise it is a straight line to the (snapped) tap.
   *
   * @param {?Graph} graph The network, or null when snapping is off.
   * @param {?Point} from The route's last point, or null for the first tap.
   * @param {Point} tap
   * @param {number} maxM
   * @returns {{points: Array<Point>, snapped: boolean, routed: boolean}}
   */
  function legFor(graph, from, tap, maxM) {
    const to = graph ? nearestOnNetwork(graph, tap, maxM) : null;
    if (!graph || !to) return { points: [tap], snapped: false, routed: false };
    const start = from ? nearestOnNetwork(graph, from, ON_NETWORK_M) : null;
    if (!from || !start) return { points: [to.point], snapped: true, routed: false };
    const path = shortestPath(graph, start, to);
    const straight = haversineM(from, to.point);
    if (!path || path.lengthM > DETOUR_CAP * Math.max(straight, 1)) {
      return { points: [to.point], snapped: true, routed: false };
    }
    return { points: path.coordinates.slice(1), snapped: true, routed: true };
  }

  self.pwaSkiSnapCore = Object.freeze({
    SNAP_PX: SNAP_PX,
    DETOUR_CAP: DETOUR_CAP,
    metresPerPixel: metresPerPixel,
    buildGraph: buildGraph,
    nearestOnNetwork: nearestOnNetwork,
    shortestPath: shortestPath,
    legFor: legFor,
  });
})();
