/*
 * static/js/route_profile.js — the route profile: one drawing, three pages
 * (SNOW-1215).
 *
 * The lane the map's route panel draws — one fill per leg under one
 * outline, distance ticks along its foot, and, while a point is placed, a
 * vertical cursor line with a dot where it crosses the outline and the
 * point's elevation and distance beside it. Until SNOW-1215 the drawing
 * lived inside route_rail.js, and the trip pages drew a different profile
 * of their own (trip_map.js's single-colour area). The Plan page and the
 * trip page now draw THIS, from the same markup
 * (templates/includes/_route_profile.html), so a route reads the same on
 * the map, while planning, and on the page the group is sent.
 *
 * DRAWING ONLY. What places the point — a tap, a scrub, the map's line —
 * is the caller's; route_rail.js owns all of that for the map. A caller
 * hands `cursor(index)` the index it wants drawn, or null to clear it.
 * All the arithmetic is route_rail_core.js's.
 *
 * WHY THE FIGURES ARE HTML. The lane is stretched with
 * `preserveAspectRatio="none"`, which would squash a circle or a glyph, so
 * the tick labels, the dot and the readout are HTML placed by percentage
 * over and under it.
 *
 * Publishes (frozen `window.pwaRouteProfile`):
 *
 *   mount(root, strings) → view, or null when `root` holds no lane
 *     view.draw(profile, legs, sampleCount, spanM, name)
 *                  — draw the lane for one route: a `readProfile` result,
 *                    its wire legs, N (the segments the legs index; 0 for
 *                    the outline alone), the route's length for the
 *                    ticks, and its name for the lane's label
 *     view.cursor(index)
 *                  — draw the cursor line and readout at a sample index,
 *                    or clear them with null
 *     view.indexAt(clientX)
 *                  — the sample under a viewport x on the lane, clamped
 *                    to the route; null before a draw with legs, or while
 *                    the lane has no width
 *     view.lane    — the lane's <svg>, for the caller's pointer handlers
 *
 *   read(feature) → {profile, legs, slope, sampleCount, spanM}
 *                  — what `draw` takes, read off a route Feature whose
 *                    nested properties may arrive JSON-encoded
 *   sampleCountOf(slope, legs) → N
 *                  — `slope.angles`' length when the route has one, else
 *                    the last leg's `to` plus one: legs ride on an
 *                    unsampled route too, in the segment indices its
 *                    record will have (apps/routes/services/leg_wire.py)
 *
 *   `strings` holds `unit-m`, `unit-km` and `lane-label`, the partial's
 *   strings template's keys.
 */

(function routeProfileInit() {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  /** The space between the cursor line and its figures, px: clear of the dot. */
  var READOUT_GAP_PX = 9;

  /**
   * Create one SVG element with attributes.
   *
   * @param {string} tag
   * @param {Object<string, string>} attrs
   * @returns {SVGElement}
   */
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs).forEach(function (key) {
      el.setAttribute(key, attrs[key]);
    });
    return el;
  }

  /**
   * Read a feature property that may arrive JSON-encoded.
   *
   * The routes cache holds real objects, but a feature MapLibre handed
   * back stringifies nested properties, and a caller may pass either.
   *
   * @param {*} value
   * @returns {*}
   */
  function readJson(value) {
    if (typeof value !== 'string') return value;
    try {
      return JSON.parse(value);
    } catch (_err) {
      return null;
    }
  }

  /**
   * N, the segments a route's legs index.
   *
   * @param {?{angles?: Array}} slope The route's slope record, or null.
   * @param {Array} legs The route's wire legs.
   * @returns {number} 0 with neither.
   */
  function sampleCountOf(slope, legs) {
    if (slope && Array.isArray(slope.angles) && slope.angles.length) {
      return slope.angles.length;
    }
    var last = legs.length ? legs[legs.length - 1] : null;
    return last && Number.isInteger(last.to) ? last.to + 1 : 0;
  }

  /**
   * Read what `draw` takes off one route Feature.
   *
   * A route with no legs at all — no elevation, or too short — reads as
   * N = 0 and no legs: the outline alone, with no point to place.
   *
   * @param {{geometry?: {coordinates?: Array}, properties?: object}} feature
   * @returns {{profile: object, legs: Array, slope: *, sampleCount: number, spanM: number}}
   */
  function read(feature) {
    var props = (feature && feature.properties) || {};
    var slope = readJson(props.slope);
    var wireLegs = readJson(props.legs);
    var legs = Array.isArray(wireLegs) ? wireLegs : [];
    var sampleCount = sampleCountOf(slope, legs);
    if (!(sampleCount > 0)) legs = [];
    var coordinates = feature && feature.geometry && feature.geometry.coordinates;
    var profile = self.pwaElevationProfileCore.readProfile(
      Array.isArray(coordinates) ? coordinates : [],
    );
    var spanM = typeof props.distance_m === 'number' ? props.distance_m : profile.distanceM;
    return { profile: profile, legs: legs, slope: slope, sampleCount: sampleCount, spanM: spanM };
  }

  /**
   * Bind a view to one profile's markup.
   *
   * @param {ParentNode} root An element holding _route_profile.html.
   * @param {Object<string, string>} strings The partial's strings.
   * @returns {?{draw: Function, cursor: Function, indexAt: function(number): ?number, lane: Element}}
   */
  function mount(root, strings) {
    var lane = root && root.querySelector('[data-route-rail-lane]');
    var ticksEl = root && root.querySelector('[data-route-rail-ticks]');
    if (!lane || !ticksEl) return null;
    var readoutEl = /** @type {HTMLElement|null} */ (root.querySelector('[data-route-rail-readout]'));
    var dotEl = /** @type {HTMLElement|null} */ (root.querySelector('[data-route-rail-dot]'));
    var elevationEl = /** @type {HTMLElement|null} */ (root.querySelector('[data-route-rail-elevation]'));
    var distanceEl = /** @type {HTMLElement|null} */ (root.querySelector('[data-route-rail-distance]'));
    var interpolate = self.pwaStrings.interpolate;
    var units = { m: strings['unit-m'], km: strings['unit-km'] };

    /** The drawn route's readProfile result, or null. */
    var profile = null;
    /** N, the segments the drawn route's legs index; 0 with no legs. */
    var sampleCount = 0;
    /** The route's length a point's distance is a share of. */
    var spanM = 0;

    /**
     * Draw the lane: the leg fills, the outline, the tick marks, and the
     * tick labels under it.
     *
     * @param {object} nextProfile A readProfile result.
     * @param {Array} legs The route's wire legs.
     * @param {number} nextSampleCount N; 0 when the route has no legs.
     * @param {number} nextSpanM The route's length for the tick labels.
     * @param {string} name The route's name, for the lane's label.
     */
    function draw(nextProfile, legs, nextSampleCount, nextSpanM, name) {
      var core = self.pwaRouteRailCore;
      var box = core.BOX;
      profile = nextProfile;
      sampleCount = nextSampleCount;
      spanM = nextSpanM;
      lane.replaceChildren();
      ticksEl.replaceChildren();
      lane.setAttribute('viewBox', '0 0 ' + box.width + ' ' + box.height);
      lane.setAttribute('aria-label', interpolate(strings['lane-label'], { name: name }));

      var paths = core.legPaths(profile, legs, sampleCount, box);

      paths.legs.forEach(function (fill) {
        lane.appendChild(svgEl('path', {
          d: fill.d,
          class: 'route-rail-leg',
          'data-climbing': fill.climbing ? 'true' : 'false',
        }));
      });

      if (paths.outline) {
        lane.appendChild(svgEl('path', {
          d: paths.outline,
          fill: 'none',
          stroke: 'currentColor',
          'stroke-width': '1.5',
          'stroke-linejoin': 'round',
          'vector-effect': 'non-scaling-stroke',
          'pointer-events': 'none',
        }));
      }

      if (!(spanM > 0)) return;
      core.ticks(spanM, units).forEach(function (tick) {
        var fraction = tick.d / spanM;
        var x = (fraction * box.width).toFixed(2);
        lane.appendChild(svgEl('line', {
          x1: x,
          x2: x,
          y1: String(box.height - (tick.major ? 8 : 4)),
          y2: String(box.height),
          stroke: 'currentColor',
          'stroke-opacity': tick.major ? '0.5' : '0.25',
          'vector-effect': 'non-scaling-stroke',
          'pointer-events': 'none',
          class: 'text-text-3',
        }));
        if (!tick.label) return;
        var label = document.createElement('span');
        // The first label hangs right of its tick and the rest are centred
        // on theirs, so "0 km" is not cut off at the lane's left edge. None
        // wraps: an absolute box near the right edge shrinks to the space
        // left, which broke "15 km" over two lines on a phone.
        label.className = fraction === 0
          ? 'absolute top-0 whitespace-nowrap'
          : 'absolute top-0 -translate-x-1/2 whitespace-nowrap';
        label.style.left = (fraction * 100).toFixed(3) + '%';
        label.textContent = tick.label;
        ticksEl.appendChild(label);
      });
    }

    /**
     * Place the dot and the two figures for the cursor index, or hide them.
     *
     * The dot sits where the line crosses the outline (`profileY`); a point
     * on a stretch with no elevation gets no dot and no elevation, and
     * still its distance. The figures go right of the line, left-aligned,
     * unless they would not fit before the lane's right edge
     * (`readoutSide`).
     *
     * @param {?number} index The cursor index; null to hide.
     */
    function paintReadout(index) {
      if (!readoutEl || !dotEl || !elevationEl || !distanceEl) return;
      var core = self.pwaRouteRailCore;
      if (index === null || !(sampleCount > 0) || !profile) {
        readoutEl.hidden = true;
        return;
      }
      var fraction = (index + 0.5) / sampleCount;
      var percent = fraction * 100;
      var elevation = core.pointElevation(profile, index, sampleCount, units);
      var distance = core.pointDistance(index, sampleCount, spanM, units);
      var y = core.profileY(profile, fraction * profile.distanceM);

      elevationEl.textContent = elevation || '';
      elevationEl.hidden = !elevation;
      distanceEl.textContent = distance || '';
      distanceEl.hidden = !distance;
      dotEl.hidden = y === null;
      if (y !== null) {
        dotEl.style.left = percent.toFixed(3) + '%';
        dotEl.style.top = ((y / core.BOX.height) * 100).toFixed(3) + '%';
      }
      readoutEl.hidden = false;

      // Measured once shown, so the widths are the rendered ones.
      var laneWidth = lane.getBoundingClientRect().width;
      var widest = Math.max(
        elevationEl.getBoundingClientRect().width,
        distanceEl.getBoundingClientRect().width,
      );
      var side = core.readoutSide(fraction * laneWidth, laneWidth, widest, READOUT_GAP_PX);
      readoutEl.setAttribute('data-side', side);
      [elevationEl, distanceEl].forEach(function (label) {
        if (side === 'right') {
          label.style.left = 'calc(' + percent.toFixed(3) + '% + ' + READOUT_GAP_PX + 'px)';
          label.style.right = '';
        } else {
          label.style.right = 'calc(' + (100 - percent).toFixed(3) + '% + ' + READOUT_GAP_PX + 'px)';
          label.style.left = '';
        }
      });
    }

    /**
     * Draw the cursor index as a vertical line across the lane (SNOW-1019),
     * with its dot and its figures (2026-10-02).
     *
     * Placed by share, the rule the leg fills follow: sample i owns the
     * i-th of N shares, and the line sits at its middle. Removed while the
     * index is null.
     *
     * @param {?number} index The cursor index.
     */
    function cursor(index) {
      var line = lane.querySelector('[data-route-rail-cursor]');
      if (index === null || index === undefined || !(sampleCount > 0)) {
        if (line) line.remove();
        paintReadout(null);
        return;
      }
      var box = self.pwaRouteRailCore.BOX;
      if (!line) {
        line = svgEl('line', {
          'data-route-rail-cursor': '',
          y1: '0',
          y2: String(box.height),
          stroke: 'currentColor',
          'stroke-width': '1.5',
          'vector-effect': 'non-scaling-stroke',
          'pointer-events': 'none',
          class: 'text-text-1',
        });
        lane.appendChild(line);
      }
      var x = (((index + 0.5) / sampleCount) * box.width).toFixed(2);
      line.setAttribute('x1', x);
      line.setAttribute('x2', x);
      paintReadout(index);
    }

    /**
     * The sample under a viewport x on the lane, clamped to the route.
     *
     * The inverse of `cursor`'s placement: sample i owns the i-th of N
     * equal shares of the lane's width.
     *
     * @param {number} clientX The pointer's x.
     * @returns {?number} Null with no legs drawn or no lane width.
     */
    function indexAt(clientX) {
      if (!(sampleCount > 0)) return null;
      var rect = lane.getBoundingClientRect();
      if (!(rect.width > 0)) return null;
      var at = Math.floor(((clientX - rect.left) / rect.width) * sampleCount);
      return Math.min(sampleCount - 1, Math.max(0, at));
    }

    return Object.freeze({ draw: draw, cursor: cursor, indexAt: indexAt, lane: lane });
  }

  self.pwaRouteProfile = Object.freeze({
    mount: mount,
    read: read,
    sampleCountOf: sampleCountOf,
  });
})();
