/*
 * static/js/route_leader.js — the leader line's DOM half (SNOW-1019).
 *
 * One dashed line that ties the route cursor's three drawings together:
 * from the cursor's dot on the map, down to where rail one's cursor line
 * meets its profile, and on to rail two's while a leg is open. The shape
 * is route_leader_core.js's; this module owns the one `<svg>` it is drawn
 * in and decides when to redraw.
 *
 * WHERE IT LIVES. Inside `#map`, absolutely positioned over the whole of
 * it, because the rail is inside `#map` too — one layer covers the map
 * and both rails, and every stop is converted into its coordinates. It
 * takes no pointer events (`.route-leader` in static/css/map.css), so
 * nothing under it stops working.
 *
 * WHERE ITS STOPS COME FROM. Each surface reports its own point in
 * viewport px, on the rule route_cursor_core.js set — every surface owns
 * its geometry:
 *
 *   - the map: `window.pwaRouteCursorMap.point()` (map.js);
 *   - rail one: `window.pwaRouteRail.cursorPoint()`;
 *   - rail two: `window.pwaRouteRailTwo.cursorPoint()`, null while it is
 *     hidden.
 *
 * It is hidden when the cursor has no index or the rail is closed. With
 * no map point — the routes overlay off, a route with no slope record, or
 * a dot the rails cover — it drops the map stop and joins rail one to
 * rail two, and is hidden only when rail two is closed as well.
 *
 * WHEN IT REDRAWS. On a cursor change (its own subscription to the open
 * route's cursor), a camera move or map resize, a rail-two redraw (a pan
 * or a zoom moves rail two's cursor point without the cursor changing), a
 * window resize, a change in the rail's height, and the rail opening or
 * closing — at most once per
 * animation frame. Every listener is bound once; a rail open only swaps
 * the cursor subscription.
 *
 * Publishes (frozen `window.pwaRouteLeader`):
 *
 *   redraw() — draw now, without waiting for a frame
 *   element  — the svg
 */

(function routeLeaderInit() {
  'use strict';

  var mapEl = document.getElementById('map');
  if (!mapEl) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  var svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'route-leader');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('data-route-leader', '');
  var path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'var(--color-route-line)');
  path.setAttribute('stroke-width', '1.5');
  path.setAttribute('stroke-dasharray', '4 4');
  path.setAttribute('stroke-opacity', '0.85');
  svg.appendChild(path);
  var nodes = document.createElementNS(SVG_NS, 'g');
  nodes.setAttribute('fill', 'var(--color-route-line)');
  svg.appendChild(nodes);
  svg.style.display = 'none';
  mapEl.appendChild(svg);

  /** The cursor the leader follows, and the removal of that subscription. */
  var cursor = null;
  var unsubscribe = null;
  /** Removes the map bridge's camera listener, once bound. */
  var unbindMap = null;
  /** The pending animation-frame redraw, or 0. */
  var frame = 0;

  /** Hide the leader and empty it. */
  function clear() {
    path.setAttribute('d', '');
    nodes.replaceChildren();
    svg.style.display = 'none';
  }

  /** Draw the leader through its current stops, or hide it. */
  function draw() {
    frame = 0;
    var rail = window.pwaRouteRail;
    var core = self.pwaRouteLeaderCore;
    if (!core || !cursor || !rail || !rail.isOpen() || cursor.state().index === null) {
      clear();
      return;
    }
    var mapPoint = window.pwaRouteCursorMap ? window.pwaRouteCursorMap.point() : null;
    var railOne = rail.cursorPoint ? rail.cursorPoint() : null;
    var railTwo = window.pwaRouteRailTwo && window.pwaRouteRailTwo.cursorPoint
      ? window.pwaRouteRailTwo.cursorPoint()
      : null;
    // The map stop is optional: map.js answers null for a dot behind the
    // rails or under the top chrome (the moment before or during the pan
    // that brings it back), and the line then joins the two rails alone.
    var railStops = [railOne, railTwo].filter(Boolean);
    if (!railOne || (!mapPoint && railStops.length < 2)) {
      clear();
      return;
    }
    var origin = mapEl.getBoundingClientRect();
    /** @param {{x: number, y: number}} p */
    var local = function (p) { return { x: p.x - origin.left, y: p.y - origin.top }; };
    var stops = (mapPoint ? [mapPoint] : []).concat(railStops).map(local);

    path.setAttribute('d', core.leaderPath(stops));
    nodes.replaceChildren();
    railStops.map(local).forEach(function (stop) {
      var node = document.createElementNS(SVG_NS, 'circle');
      node.setAttribute('cx', String(stop.x));
      node.setAttribute('cy', String(stop.y));
      node.setAttribute('r', '2.5');
      node.setAttribute('data-route-leader-stop', '');
      nodes.appendChild(node);
    });
    svg.style.display = '';
  }

  /** Redraw on the next animation frame, once however often asked. */
  function schedule() {
    if (typeof window.requestAnimationFrame !== 'function') {
      draw();
      return;
    }
    if (frame) return;
    frame = window.requestAnimationFrame(draw);
  }

  /** Follow the rail's current cursor, dropping the previous one. */
  function follow() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = window.pwaRouteRail && window.pwaRouteRail.cursor
      ? window.pwaRouteRail.cursor()
      : null;
    if (cursor) unsubscribe = cursor.subscribe(schedule);
    // The map exists by the time a rail opens (a tap on the map opens
    // it), so the camera listener is bound then, and only the once.
    if (!unbindMap && window.pwaRouteCursorMap) {
      unbindMap = window.pwaRouteCursorMap.onChange(schedule);
    }
    if (!cursor) {
      if (frame && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(frame);
      }
      frame = 0;
      clear();
      return;
    }
    schedule();
  }

  document.addEventListener('snowdesk:route-rail-changed', follow);
  document.addEventListener('snowdesk:route-rail-two-drawn', schedule);
  document.addEventListener('snowdesk:route-rail-resized', schedule);
  window.addEventListener('resize', schedule);

  window.pwaRouteLeader = Object.freeze({
    redraw: draw,
    element: svg,
  });
}());
