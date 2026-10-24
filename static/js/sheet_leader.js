/*
 * static/js/sheet_leader.js — the leader line from a map sheet to the
 * place it describes (Hugo, 24 Oct 2026).
 *
 * One dashed line from the tapped thing on the map — a weather symbol, a
 * resort pin — to the sheet describing it, so the reader can see which
 * point the sheet is for; on a map dense with symbols it was easy to lose.
 * Two sheets carry it, listed in SHEETS below: the weather sheet and the
 * place sheet. Only one object sheet is open at a time (map_sheet.js), so
 * one line serves both. The route panel's leader
 * (route_leader.js, SNOW-1019) is the pattern: the same dash, the same
 * bend (route_leader_core.js) and the same notch on the panel's edge.
 *
 * WHERE ITS STOPS ARE.
 *
 *   - desktop: the sheet is docked left (`side="left"`), so the line runs
 *     SIDEWAYS — from the symbol to a notch on the sheet's right edge,
 *     level with the sheet's heading (the place's name), held inside the
 *     sheet's height when the heading has scrolled away;
 *   - phone: the sheet is at the foot, so the line runs UP — from the
 *     sheet's top edge, under the symbol, to the symbol.
 *
 * Hidden while the sheet is closed, before a point is known, and whenever
 * the symbol is under the sheet or beside it on the wrong side: a line
 * that has to cross the sheet to arrive is no pointer.
 *
 * WHERE THE POINTS COME FROM. The place's coordinate is the sheet's
 * (`window.pwaWeatherDetail.point()`, `window.pwaPlaceSheet.point()`,
 * handed over by map.js on the tap); its screen point is the map's
 * (`window.pwaMapProjection.project()`). This module knows neither
 * MapLibre nor the sheets' content beyond the heading it levels with.
 *
 * WHEN IT REDRAWS. The sheet opening or closing (its `hidden` attribute)
 * or changing size (a detent, new content), a camera move or map resize,
 * and a window resize — at most once per animation frame.
 *
 * Publishes (frozen `window.pwaSheetLeader`):
 *
 *   stops(point, sheet, heading, phone) — the pure geometry, for tests:
 *       the line's two stops and its axis, or null for no line
 *   redraw()  — draw now, without waiting for a frame
 *   element   — the svg
 */

(function sheetLeaderInit() {
  'use strict';

  var mapEl = document.getElementById('map');
  if (!mapEl) return;

  /**
   * The sheets that point at their place: each one's element, the
   * coordinate it is showing, and the heading the desktop notch levels
   * with. A sheet absent from the page is skipped.
   */
  var SHEETS = [
    {
      el: document.getElementById('weather-sheet'),
      point: function () {
        var api = window.pwaWeatherDetail;
        return api && api.point ? api.point() : null;
      },
      heading: '[data-testid="weather-detail-heading"]',
    },
    {
      el: document.getElementById('place-sheet'),
      point: function () {
        var api = window.pwaPlaceSheet;
        return api && api.point ? api.point() : null;
      },
      heading: '[data-sheet-peek] h2',
    },
  ].filter(function (entry) { return !!entry.el; });
  if (!SHEETS.length) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';
  /** How far inside the sheet's corners a notch may sit, px. */
  var INSET = 16;
  /** How clear of the sheet a symbol must be for a line to reach it, px. */
  var CLEARANCE = 12;
  /** Matches map_sheet.js's DESKTOP_QUERY: the sheet docks left from sm. */
  var DESKTOP_QUERY = '(min-width: 640px)';

  var svg = document.createElementNS(SVG_NS, 'svg');
  // Shares the route leader's layer rule (static/css/map.css): over the
  // whole map, no pointer events.
  svg.setAttribute('class', 'route-leader');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('data-sheet-leader', '');
  var path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'var(--color-text-2)');
  path.setAttribute('stroke-width', '1.5');
  path.setAttribute('stroke-dasharray', '4 4');
  path.setAttribute('stroke-opacity', '0.85');
  svg.appendChild(path);
  var mark = document.createElementNS(SVG_NS, 'path');
  mark.setAttribute('fill', 'var(--color-text-2)');
  mark.setAttribute('data-sheet-leader-stop', '');
  svg.appendChild(mark);
  var dot = document.createElementNS(SVG_NS, 'circle');
  dot.setAttribute('r', '3');
  dot.setAttribute('fill', 'var(--color-text-2)');
  svg.appendChild(dot);
  svg.style.display = 'none';
  mapEl.appendChild(svg);

  /** The pending animation-frame redraw, or 0. */
  var frame = 0;
  /** Removes the map's camera listener, once bound. */
  var unbindMap = null;

  /**
   * Hold a number inside a range; the low end wins a range too short.
   *
   * @param {number} n
   * @param {number} lo
   * @param {number} hi
   * @returns {number}
   */
  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(n, hi));
  }

  /**
   * The line's stops, in viewport px, or null for no line.
   *
   * @param {?{x: number, y: number}} point The symbol's screen point.
   * @param {{left: number, right: number, top: number, bottom: number}} sheet
   *   The sheet's box.
   * @param {?{top: number, bottom: number}} heading The heading's box, or
   *   null with none rendered (a loading line).
   * @param {boolean} phone Whether the sheet is the phone's bottom sheet.
   * @returns {?{from: {x: number, y: number}, to: {x: number, y: number},
   *   axis: string, side: string}} `from` is the symbol, `to` the notch;
   *   `side` names the sheet edge the notch is on.
   */
  function stops(point, sheet, heading, phone) {
    if (!point || !sheet || !(sheet.bottom > sheet.top)) return null;
    if (phone) {
      if (point.y > sheet.top - CLEARANCE) return null;
      return {
        from: point,
        to: { x: clamp(point.x, sheet.left + INSET, sheet.right - INSET), y: sheet.top },
        axis: 'y',
        side: 'top',
      };
    }
    if (point.x < sheet.right + CLEARANCE) return null;
    var level = heading ? (heading.top + heading.bottom) / 2 : sheet.top + INSET;
    return {
      from: point,
      to: { x: sheet.right, y: clamp(level, sheet.top + INSET, sheet.bottom - INSET) },
      axis: 'x',
      side: 'right',
    };
  }

  /** Hide the leader and empty it. */
  function clear() {
    path.setAttribute('d', '');
    mark.setAttribute('d', '');
    svg.style.display = 'none';
  }

  /**
   * Whether the sheet is the phone's bottom sheet.
   *
   * @returns {boolean}
   */
  function isPhone() {
    if (typeof window.matchMedia !== 'function') return true;
    return !window.matchMedia(DESKTOP_QUERY).matches;
  }

  /** Draw the leader through its current stops, or hide it. */
  function draw() {
    frame = 0;
    var core = self.pwaRouteLeaderCore;
    var projection = window.pwaMapProjection;
    var open = SHEETS.filter(function (entry) {
      return !entry.el.hasAttribute('hidden') && entry.point();
    })[0];
    if (!core || !projection || !open) {
      clear();
      return;
    }
    var sheetEl = open.el;
    var point = projection.project(open.point());
    var headingEl = sheetEl.querySelector(open.heading);
    var line = stops(
      point,
      sheetEl.getBoundingClientRect(),
      headingEl ? headingEl.getBoundingClientRect() : null,
      isPhone(),
    );
    if (!line) {
      clear();
      return;
    }
    var origin = mapEl.getBoundingClientRect();
    /** @param {{x: number, y: number}} p */
    var local = function (p) { return { x: p.x - origin.left, y: p.y - origin.top }; };
    var from = local(line.from);
    var to = local(line.to);
    path.setAttribute('d', core.leaderPath([from, to], line.axis));
    dot.setAttribute('cx', String(from.x));
    dot.setAttribute('cy', String(from.y));
    // The notch: a small triangle pointing onto the sheet's edge, as the
    // route panel's does.
    if (line.side === 'top') {
      mark.setAttribute('d', 'M' + (to.x - 4) + ' ' + (to.y - 5)
        + ' L' + (to.x + 4) + ' ' + (to.y - 5) + ' L' + to.x + ' ' + to.y + ' Z');
    } else {
      mark.setAttribute('d', 'M' + (to.x + 5) + ' ' + (to.y - 4)
        + ' L' + (to.x + 5) + ' ' + (to.y + 4) + ' L' + to.x + ' ' + to.y + ' Z');
    }
    svg.style.display = '';
  }

  /** Redraw on the next animation frame, once however often asked. */
  function schedule() {
    if (!unbindMap && window.pwaMapProjection) {
      unbindMap = window.pwaMapProjection.onChange(schedule);
    }
    if (typeof window.requestAnimationFrame !== 'function') {
      draw();
      return;
    }
    if (frame) return;
    frame = window.requestAnimationFrame(draw);
  }

  SHEETS.forEach(function (entry) {
    if (typeof MutationObserver === 'function') {
      new MutationObserver(schedule).observe(entry.el, {
        attributes: true,
        attributeFilter: ['hidden', 'class', 'style', 'data-detent'],
      });
    }
    if (typeof ResizeObserver === 'function') {
      new ResizeObserver(schedule).observe(entry.el);
    }
    // The sheet's own scroll moves the heading the desktop notch follows.
    entry.el.addEventListener('scroll', schedule, true);
  });
  window.addEventListener('resize', schedule);
  document.addEventListener('snowdesk:sheet-leader-point', schedule);

  window.pwaSheetLeader = Object.freeze({
    stops: stops,
    redraw: draw,
    element: svg,
  });
}());
