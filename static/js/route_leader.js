/*
 * static/js/route_leader.js — the leader line's DOM half (SNOW-1019).
 *
 * One dashed line that ties the route cursor's two drawings together:
 * from the cursor's dot on the map up to a notch on the route panel's
 * bottom edge, directly below the profile's cursor line (SNOW-1065 — two
 * stops since rail two was retired; up, not down, since SNOW-1068 pinned
 * the panel top-left). The shape is route_leader_core.js's; this module
 * owns the one `<svg>` it is drawn in and decides when to redraw.
 *
 * WHERE IT LIVES. Inside `#map`, absolutely positioned over the whole of
 * it, because the rail is inside `#map` too — one layer covers the map
 * and the rail, and both stops are converted into its coordinates. It
 * takes no pointer events (`.route-leader` in static/css/map.css), so
 * nothing under it stops working.
 *
 * WHERE ITS STOPS COME FROM. Each surface reports its own point in
 * viewport px, on the rule route_cursor_core.js set — every surface owns
 * its geometry:
 *
 *   - the map: `window.pwaRouteCursorMap.point()` (map.js);
 *   - the rail: `window.pwaRouteRail.cursorPoint()`.
 *
 * It is hidden when the cursor has no index, the rail is closed, or the
 * map has no point to offer — the routes overlay off, a route with no
 * slope record, or a dot the rail covers. A line with one end is no line.
 *
 * WHEN IT REDRAWS. On a cursor change (its own subscription to the open
 * route's cursor), a camera move or map resize, a window resize, a change
 * in the rail's height, and the rail opening or closing — at most once per
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
    var railPoint = rail.cursorPoint ? rail.cursorPoint() : null;
    // map.js answers null for a dot under the route panel
    // (the moment before or during the pan that brings it back).
    if (!mapPoint || !railPoint) {
      clear();
      return;
    }
    var origin = mapEl.getBoundingClientRect();
    /** @param {{x: number, y: number}} p */
    var local = function (p) { return { x: p.x - origin.left, y: p.y - origin.top }; };
    var start = local(mapPoint);
    var notch = local(railPoint);

    path.setAttribute('d', core.leaderPath([start, notch]));
    // The notch: a small triangle pointing up onto the panel's edge.
    var mark = document.createElementNS(SVG_NS, 'path');
    mark.setAttribute(
      'd',
      'M' + (notch.x - 4) + ' ' + (notch.y + 5)
        + ' L' + (notch.x + 4) + ' ' + (notch.y + 5)
        + ' L' + notch.x + ' ' + notch.y + ' Z',
    );
    mark.setAttribute('data-route-leader-stop', '');
    nodes.replaceChildren(mark);
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
  document.addEventListener('snowdesk:route-rail-resized', schedule);
  window.addEventListener('resize', schedule);

  window.pwaRouteLeader = Object.freeze({
    redraw: draw,
    element: svg,
  });
}());
