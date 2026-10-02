/*
 * static/js/route_rail.js — the route panel's DOM half: fills
 * templates/includes/_route_rail.html for the open route (SNOW-1018; one
 * panel pinned top-left, with a route or point header, since SNOW-1068;
 * points only since 2026-10-02).
 *
 * A tap on a saved route opens THIS, and only this: map.js's
 * `activateRoute` calls `window.pwaRouteRail.open(feature, options)` with
 * the route's feature from the routes GeoJSON cache — the cached copy, not
 * the rendered one, because a feature read back from the map's tiles has
 * lost the third ordinate the profile is drawn from.
 *
 * THE SHEET IS BEHIND THE MENU. The route detail sheet (SNOW-973) used to
 * open on the same tap. The rail took its top half — name, figures,
 * profile — and the sheet keeps the terrain lines, one press away on the
 * menu's "Terrain" item. The sheet's body is built by map.js, which owns
 * the map state it reads, and handed over as `options.details`: a function
 * this module calls on that press, never at open.
 *
 * ITS OWN LIFETIME. The rail stays open while the sheet opens and closes
 * over it — it is not registered with window.pwaMapOverlays, which would
 * close it the moment the sheet it opened announced itself. It closes on
 * its own × (`[data-route-rail-close]`), on a tap on empty map (map.js),
 * on Escape when nothing else is open to take that Escape and no point is
 * placed, on a claim or a delete, and it is refilled in place when
 * another route is tapped.
 *
 * WHAT THIS MODULE OWNS. The rail's markup, filled per open; one route
 * cursor per open route (`createRouteCursor`, static/js/route_cursor_core.js,
 * whose first caller this is); and the rail's menu. All the
 * arithmetic — tick step, meta line, where each leg's fill sits, the
 * point's figures — is
 * route_rail_core.js's, and all the copy is the partial's strings template.
 *
 * ONE RAIL (SNOW-1065). The route's name as the title, its figures as the
 * subtitle in the routes list's meta-line format (`formatMetaLine`), then
 * the profile. Rail two — the TERRAIN row with its band strip, bank wedges
 * and readout — was retired: the point card reads a point instead.
 *
 * POINTS ONLY (2026-10-02). The leg fills are drawn and never pressed:
 * leg selection — a leg highlighted on the map and the profile, " • Leg 3"
 * in the title, the leg's own figures — was removed, along with dragging
 * along the profile and the mouse moving the point by hovering. A TAP on
 * the profile's top line — loosely, OUTLINE_TOLERANCE_PX either side —
 * places the point at that distance; a tap on the route's
 * line on the map places it at the nearest sample (map.js's
 * tapOpenRoute). Either writes the cursor, and everything else follows it.
 *
 * THE POINT HEADER (SNOW-1064, SNOW-1068). `open` attaches the point
 * header (route_point_card.js) to the new cursor, with the arrays it reads
 * the point's words from, and `close` detaches it. The header follows the
 * cursor itself, and its wheel clears the point; this module hides the
 * title and the meta line while a point is placed (`paintState`) and puts
 * the route's name in the eyebrow. The × always closes the route.
 *
 * THE CURSOR LINE (SNOW-1019) AND ITS READOUT (2026-10-02). The cursor
 * index is drawn across the lane as a vertical line
 * (`[data-route-rail-cursor]`), placed by share, with a dot where it
 * crosses the outline (`[data-route-rail-dot]`) and two figures beside it
 * (`[data-route-rail-readout]`): the elevation at the top, the distance
 * from the start at the bottom. They sit right of the line, left-aligned,
 * and move to its left, right-aligned, when they would not fit
 * (`readoutSide`). The dot and the figures are HTML over the lane, not SVG
 * inside it, for the tick labels' reason: the lane is stretched with
 * `preserveAspectRatio="none"`, which would squash a circle or a glyph.
 *
 * The cursor is sized from `slope.angles` when the route has one, and
 * otherwise from the legs themselves (the last `to` plus one): legs are a
 * fact about the geometry and ride on an unsampled route too, in the
 * segment indices its record will have (apps/routes/services/leg_wire.py).
 * Only a route with no legs at all — no elevation, or too short — gets the
 * outline alone, with no point to place.
 *
 * THE ACTIONS. Terrain calls `options.details`; Plan a trip
 * is a link; Share and Rename reuse window.pwaShare and
 * window.pwaRowRenameCommit exactly as the routes panel does; Delete
 * confirms and posts to routes:delete. Each change announces
 * `snowdesk:routes-changed`, which is what makes the map and an open
 * routes panel re-read the list. A pending share has no uuid and none of
 * the owner endpoints would answer for it, so its menu keeps the details
 * item alone (`[data-route-rail-owner]` marks the rest), and its Save —
 * `options.claim`, the control map.js builds — sits in the identity block
 * (`[data-route-rail-claim]`), where the recipient lands.
 *
 * PINNED TOP-LEFT (SNOW-1068). While open, `#map` carries
 * `data-route-rail-open`, which static/css/map.css reads to withdraw the
 * controls a phone does not keep (SNOW-1067). Nothing on the map moves
 * for the panel. A ResizeObserver announces `snowdesk:route-rail-resized`
 * whenever the panel's content changes its height (a point header, a
 * wrapped title), for the leader line and map.js's keep-in-view check.
 *
 * Publishes (frozen `window.pwaRouteRail`):
 *
 *   open(feature, {details?, claim?})
 *                  — fill and show the rail for one route feature;
 *                    `details` opens the route detail sheet, `claim` is
 *                    a pending share's Save control
 *   close()        — hide it and drop its cursor
 *   isOpen()       — whether it is showing
 *   cursorPoint()  — the panel's bottom edge below the cursor line,
 *                    viewport px, or null; the leader line's stop
 *   cursor()       — the open route's cursor, or null; map.js follows it
 *                    to draw the index as a dot on the line, and writes
 *                    the index back from a tap on it (SNOW-1019)
 *   element        — the rail itself, measured by map.js's fit padding
 */

(function routeRailInit() {
  'use strict';

  var rail = document.getElementById('route-rail');
  if (!rail) return;
  var mapEl = document.getElementById('map');

  var SVG_NS = 'http://www.w3.org/2000/svg';
  /** The space between the cursor line and its figures, px: clear of the dot. */
  var READOUT_GAP_PX = 9;
  /** How far above or below the profile's top line a tap still counts, px. */
  var OUTLINE_TOLERANCE_PX = 20;

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-rail-strings-template', {
    'unit-m': '%(value)s m',
    'unit-km': '%(value)s km',
    'meta-km': '%(km)skm',
    'meta-both': '%(km)skm · %(ascent)sm ↑ · %(descent)sm ↓',
    'meta-ascent': '%(km)skm · %(ascent)sm ↑',
    'meta-descent': '%(km)skm · %(descent)sm ↓',
    'meta-hm': '%(hours)sh%(minutes)sm',
    'meta-m': '%(minutes)sm',
    'meta-duration': '%(figures)s · %(duration)s',
    'lane-label': 'Elevation profile of %(name)s',
    untitled: 'Untitled route',
    'delete-confirm': "Delete %(name)s? You'll need the .gpx file again to put it back.",
    'delete-failed': "That route couldn't be deleted. Try again.",
    'rename-failed': "That name couldn't be saved. Try again.",
    'share-copied': 'Link copied.',
    'share-failed': "That link couldn't be created. Try again.",
  });
  var interpolate = self.pwaStrings.interpolate;

  var RENAME_URL_TEMPLATE = rail.dataset.routeRenameUrlTemplate || '';
  var SHARE_URL_TEMPLATE = rail.dataset.routeShareUrlTemplate || '';
  var DELETE_URL_TEMPLATE = rail.dataset.routeDeleteUrlTemplate || '';
  var PLAN_TRIP_URL = rail.dataset.routePlanTripUrl || '';

  var nameEl = rail.querySelector('[data-route-rail-name]');
  var eyebrowEl = document.getElementById('route-rail-eyebrow');
  var titleEl = rail.querySelector('[data-route-rail-title]');
  var closeEl = rail.querySelector('[data-route-rail-close]');
  /** The eyebrow's route text, as the partial rendered it. */
  var EYEBROW_TEXT = eyebrowEl ? eyebrowEl.textContent : '';
  var metaEl = rail.querySelector('[data-route-rail-meta]');
  var lane = rail.querySelector('[data-route-rail-lane]');
  var ticksEl = rail.querySelector('[data-route-rail-ticks]');
  var readoutEl = rail.querySelector('[data-route-rail-readout]');
  var dotEl = rail.querySelector('[data-route-rail-dot]');
  var elevationEl = rail.querySelector('[data-route-rail-elevation]');
  var distanceEl = rail.querySelector('[data-route-rail-distance]');
  var actionsEl = rail.querySelector('[data-route-rail-actions]');
  var planTripEl = rail.querySelector('[data-route-rail-plan-trip]');
  var renameEl = rail.querySelector('[data-route-rename]');
  var renameInput = rail.querySelector('[data-row-rename-input]');
  var detailsEl = rail.querySelector('[data-route-rail-details]');
  var claimEl = rail.querySelector('[data-route-rail-claim]');

  /** The open route's cursor, or null. */
  var cursor = null;
  /** Removes this rail's subscription to `cursor`. */
  var unsubscribe = null;
  /** The open route's legs, as they came off the wire. */
  var legs = [];
  /** N, the segments the open route's legs index; 0 with no legs. */
  var sampleCount = 0;
  /** The open route's readProfile result, for the cursor's point on it. */
  var currentProfile = null;
  /** @type {{uuid: ?string, name: string}} */
  var current = { uuid: null, name: '' };
  /** Opens the open route's detail sheet; null when there is none. */
  var openDetails = null;
  /** The open route's own meta line. */
  var routeMeta = '';
  /** The route's length a point's distance is a share of. */
  var currentSpanM = 0;

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

  /** @returns {string} The CSRF token the partial rendered. */
  function csrfToken() {
    var input = rail.querySelector('[data-route-rail-csrf] input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  /** @param {string} message */
  function toast(message) {
    if (window.MapSheet && window.MapSheet.toast) window.MapSheet.toast(message);
  }

  /** Tell the map and any open routes panel that the list changed. */
  function announceRoutesChanged() {
    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed', { detail: null }));
  }

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
   * Draw the lane: the leg fills, the outline, the tick marks, and the
   * tick labels under it.
   *
   * @param {object} profile A readProfile result.
   * @param {number} sampleCount N, the segments the legs index; 0 when
   *   the route has no legs.
   * @param {number} spanM The route's length for the tick labels.
   */
  function drawLane(profile, sampleCount, spanM) {
    var core = self.pwaRouteRailCore;
    var box = core.BOX;
    lane.replaceChildren();
    ticksEl.replaceChildren();
    lane.setAttribute('viewBox', '0 0 ' + box.width + ' ' + box.height);
    lane.setAttribute(
      'aria-label',
      interpolate(STRINGS['lane-label'], { name: current.name }),
    );

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
    core.ticks(spanM, { m: STRINGS['unit-m'], km: STRINGS['unit-km'] }).forEach(
      function (tick) {
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
        // on theirs, so "0 km" is not cut off at the rail's left edge. None
        // wraps: an absolute box near the right edge shrinks to the space
        // left, which broke "15 km" over two lines on a phone.
        label.className = fraction === 0
          ? 'absolute top-0 whitespace-nowrap'
          : 'absolute top-0 -translate-x-1/2 whitespace-nowrap';
        label.style.left = (fraction * 100).toFixed(3) + '%';
        label.textContent = tick.label;
        ticksEl.appendChild(label);
      },
    );
  }

  /**
   * Draw the cursor index as a vertical line across the lane (SNOW-1019),
   * with its dot and its figures (2026-10-02).
   *
   * Placed by share, the rule the leg fills follow: sample i owns the
   * i-th of N shares, and the line sits at its middle. Hidden — removed —
   * while the index is null.
   *
   * @param {?number} index The cursor index.
   */
  function drawCursorLine(index) {
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
   * Place the dot and the two figures for the cursor index, or hide them.
   *
   * The dot sits where the line crosses the outline (`profileY`); a point
   * on a stretch with no elevation gets no dot and no elevation, and still
   * its distance. The figures go right of the line, left-aligned, unless
   * they would not fit before the lane's right edge (`readoutSide`).
   *
   * @param {?number} index The cursor index; null to hide.
   */
  function paintReadout(index) {
    if (!readoutEl) return;
    var core = self.pwaRouteRailCore;
    if (index === null || !(sampleCount > 0) || !currentProfile) {
      readoutEl.hidden = true;
      return;
    }
    var units = { m: STRINGS['unit-m'], km: STRINGS['unit-km'] };
    var fraction = (index + 0.5) / sampleCount;
    var percent = fraction * 100;
    var elevation = core.pointElevation(currentProfile, index, sampleCount, units);
    var distance = core.pointDistance(index, sampleCount, currentSpanM, units);
    var y = core.profileY(currentProfile, fraction * currentProfile.distanceM);

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
   * Switch the header to a point, or back to the route (SNOW-1068).
   *
   * The point header itself follows the cursor (route_point_card.js);
   * here the title and the meta line give way to it and the eyebrow names
   * the route. The point's distance is on the profile, beside the cursor
   * line (2026-10-02).
   *
   * @param {?number} index The cursor index; null with no point.
   */
  function paintPointMode(index) {
    var point = index !== null;
    if (point) rail.setAttribute('data-route-rail-point', '');
    else rail.removeAttribute('data-route-rail-point');
    if (titleEl) titleEl.hidden = point;
    if (metaEl) metaEl.hidden = point;
    if (eyebrowEl) eyebrowEl.textContent = point ? current.name : EYEBROW_TEXT;
  }

  /**
   * Bring the header and the cursor line in line with the cursor.
   *
   * @param {?{index?: ?number}} state
   */
  function paintState(state) {
    var index = state && typeof state.index === 'number' ? state.index : null;
    drawCursorLine(index);
    paintPointMode(index);
  }

  /**
   * The sample under a viewport x on the lane, clamped to the route.
   *
   * @param {number} clientX The pointer's x.
   * @returns {?number} Null while the lane has no width.
   */
  function indexAt(clientX) {
    var rect = lane.getBoundingClientRect();
    if (!(rect.width > 0)) return null;
    var at = Math.floor(((clientX - rect.left) / rect.width) * sampleCount);
    return Math.min(sampleCount - 1, Math.max(0, at));
  }

  /**
   * Fit the menu to the open route.
   *
   * The details item shows whenever there is a sheet to open; the owner
   * items only for a route this visitor owns, each hidden individually so
   * a pending share keeps the details item. The whole menu hides only when
   * nothing in it would do anything.
   */
  function fillMenu() {
    var owned = !!current.uuid;
    if (detailsEl) detailsEl.closest('li').hidden = !openDetails;
    rail.querySelectorAll('[data-route-rail-owner]').forEach(function (item) {
      item.hidden = !owned;
    });
    if (actionsEl) actionsEl.hidden = !owned && !openDetails;
    if (!owned) return;
    if (planTripEl && PLAN_TRIP_URL) {
      planTripEl.setAttribute(
        'href',
        PLAN_TRIP_URL + '?route=' + encodeURIComponent(current.uuid),
      );
    }
    if (renameEl) renameEl.setAttribute('data-route-rename', current.uuid);
  }

  /**
   * How many segments the open route's cursor runs over.
   *
   * `slope.angles` when the route has been sampled; otherwise the legs'
   * own extent, since they tile the segments exactly (the last `to` is
   * N − 1). Zero when there is neither.
   *
   * @param {?{angles?: Array}} slope The feature's parsed `slope`.
   * @param {Array<{to: number}>} wireLegs The feature's parsed `legs`.
   * @returns {number}
   */
  function sampleCountOf(slope, wireLegs) {
    if (slope && Array.isArray(slope.angles) && slope.angles.length) {
      return slope.angles.length;
    }
    var last = wireLegs.length ? wireLegs[wireLegs.length - 1] : null;
    return last && Number.isInteger(last.to) ? last.to + 1 : 0;
  }

  /**
   * Seat a pending share's Save control, or empty the slot.
   *
   * @param {?Node} node The control map.js built, or null.
   */
  function fillClaim(node) {
    if (!claimEl) return;
    claimEl.replaceChildren();
    claimEl.hidden = !node;
    if (node) claimEl.appendChild(node);
  }

  /**
   * Fill and show the rail for one route.
   *
   * @param {{geometry?: {coordinates?: Array}, properties?: object}} feature
   *   The route feature from the routes GeoJSON cache.
   * @param {{details?: function(): *, claim?: ?Node}} [options]
   *   `details` opens the route detail sheet, called on the menu's
   *   Terrain item; `claim` is a pending share's Save
   *   control, seated in the identity block.
   * @returns {boolean} Whether the rail opened.
   */
  function open(feature, options) {
    var profileCore = self.pwaElevationProfileCore;
    var railCore = self.pwaRouteRailCore;
    if (!feature || !profileCore || !railCore) return false;
    var props = feature.properties || {};

    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;

    var opts = options || {};
    current = {
      uuid: props.uuid ? String(props.uuid) : null,
      name: props.name || STRINGS.untitled,
    };
    openDetails = typeof opts.details === 'function' ? opts.details : null;
    // A pending share carries the same slope record as an owned route
    // (the server builds both with one function), and the point card reads
    // its terrain from it — so it is read for both. The map's rule that a
    // pending line draws no slope is the map's alone.
    var slope = readJson(props.slope);
    var wireLegs = readJson(props.legs);
    legs = Array.isArray(wireLegs) ? wireLegs : [];
    sampleCount = sampleCountOf(slope, legs);
    if (sampleCount > 0 && self.pwaRouteCursorCore) {
      cursor = self.pwaRouteCursorCore.createRouteCursor(sampleCount);
      unsubscribe = cursor.subscribe(paintState);
    } else {
      legs = [];
      sampleCount = 0;
    }

    var coordinates = feature.geometry && feature.geometry.coordinates;
    var profile = profileCore.readProfile(Array.isArray(coordinates) ? coordinates : []);
    var spanM = typeof props.distance_m === 'number' ? props.distance_m : profile.distanceM;

    nameEl.textContent = current.name;
    routeMeta = railCore.formatMetaLine(
      {
        distance_m: props.distance_m,
        ascent_m: props.ascent_m,
        descent_m: props.descent_m,
        duration_s: props.duration_s,
      },
      STRINGS,
    );
    if (metaEl) metaEl.textContent = routeMeta;
    currentProfile = profile;
    currentSpanM = spanM;
    drawLane(profile, sampleCount, spanM);
    // SNOW-1064: the point header follows the rail's cursor.
    if (window.pwaRoutePointCard) {
      if (cursor) {
        window.pwaRoutePointCard.attach({
          cursor: cursor,
          slope: slope,
          coordinates: coordinates,
          profile: profile,
          legs: legs,
          sampleCount: sampleCount,
          spanM: spanM,
        });
      } else {
        window.pwaRoutePointCard.detach();
      }
    }
    fillMenu();
    fillClaim(props.pending ? opts.claim || null : null);
    paintState(cursor ? cursor.state() : null);

    rail.hidden = false;
    if (mapEl) mapEl.setAttribute('data-route-rail-open', '');
    announceRailChanged();
    return true;
  }

  /**
   * Hide the rail and drop its cursor.
   *
   * The point is cleared FIRST, while every subscriber is still
   * listening, so the map (map.js's bindRouteCursor) clears its dot. That
   * covers the rail's own ×, Escape and backdrop closes, none of which the
   * map sees.
   */
  function close() {
    if (cursor) cursor.setIndex(null);
    if (window.pwaRoutePointCard) window.pwaRoutePointCard.detach();
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;
    legs = [];
    sampleCount = 0;
    currentProfile = null;
    currentSpanM = 0;
    routeMeta = '';
    openDetails = null;
    fillClaim(null);
    paintPointMode(null);
    rail.hidden = true;
    if (mapEl) mapEl.removeAttribute('data-route-rail-open');
    announceRailChanged();
  }

  /**
   * Tell the leader line (route_leader.js) the rail opened, closed or was
   * refilled, so it follows the new cursor or clears (SNOW-1019).
   */
  function announceRailChanged() {
    document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed', { detail: null }));
  }

  /**
   * The leader line's stop on this panel, in viewport px.
   *
   * A notch on the panel's BOTTOM EDGE, directly below the profile's
   * cursor line (SNOW-1068): the panel is pinned top-left, so the leader
   * runs up from the map's dot and stops at the card, in line with the
   * cursor line above. The x is placed by share like the line itself.
   *
   * @returns {?{x: number, y: number}} Null with no cursor index, or while
   *   the rail is hidden.
   */
  function cursorPoint() {
    if (!cursor || rail.hidden || !(sampleCount > 0)) return null;
    var index = cursor.state().index;
    if (index === null) return null;
    var laneRect = lane.getBoundingClientRect();
    var fraction = (index + 0.5) / sampleCount;
    return {
      x: laneRect.left + fraction * laneRect.width,
      y: rail.getBoundingClientRect().bottom,
    };
  }

  // ---- presses ----------------------------------------------------------

  /**
   * The sample a press on the lane places, or null when it is not on the
   * profile's top line.
   *
   * @param {{clientX: number, clientY: number}} event The pointer event.
   * @returns {?number}
   */
  function outlineIndexAt(event) {
    if (!cursor || !(sampleCount > 0) || !currentProfile) return null;
    var rect = lane.getBoundingClientRect();
    if (!(rect.width > 0)) return null;
    var fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    var on = self.pwaRouteRailCore.onOutline(
      currentProfile,
      fraction,
      event.clientY - rect.top,
      rect.height,
      OUTLINE_TOLERANCE_PX,
    );
    return on ? indexAt(event.clientX) : null;
  }

  // A tap on the profile's top line places the point at that distance
  // (2026-10-02). The target is the line, loosely — OUTLINE_TOLERANCE_PX
  // either side — not the whole lane: a tap down in the fill by the ticks
  // sending the point somewhere was unexpected.
  lane.addEventListener('click', function (event) {
    var index = outlineIndexAt(event);
    if (index !== null) cursor.setIndex(index);
  });

  // A mouse shows the pointer only where a click would place the point.
  lane.addEventListener('mousemove', function (event) {
    lane.style.cursor = outlineIndexAt(event) !== null ? 'pointer' : '';
  });
  lane.addEventListener('mouseleave', function () {
    lane.style.cursor = '';
  });

  // ---- actions ----------------------------------------------------------

  /** Mint a share link for the open route and hand it to the platform. */
  function share() {
    if (!current.uuid || !SHARE_URL_TEMPLATE || !window.pwaShare) return;
    window.pwaShare
      .createShare(SHARE_URL_TEMPLATE.replace('__UUID__', current.uuid), csrfToken())
      .then(function (url) {
        window.pwaTelemetry?.emit('map.route.shared', {});
        return window.pwaShare.shareOrCopy(url);
      })
      .then(function (outcome) {
        if (outcome === 'copied') toast(STRINGS['share-copied']);
        else if (outcome === 'failed') toast(STRINGS['share-failed']);
      })
      .catch(function () {
        toast(STRINGS['share-failed']);
      });
  }

  /** Confirm, then delete the open route. */
  function remove() {
    if (!current.uuid || !DELETE_URL_TEMPLATE) return;
    var question = interpolate(STRINGS['delete-confirm'], { name: current.name });
    if (!window.confirm(question)) return;
    fetch(DELETE_URL_TEMPLATE.replace('__UUID__', current.uuid), {
      method: 'POST',
      headers: { 'HX-Request': 'true', 'X-CSRFToken': csrfToken() },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('route delete ' + resp.status);
        close();
        window.pwaRouteDetail?.close();
        announceRoutesChanged();
      })
      .catch(function () {
        toast(STRINGS['delete-failed']);
      });
  }

  // Bound on the rail, so it runs before overflow_menu.js's document-level
  // listener closes the menu out from under the item that was pressed.
  rail.addEventListener('click', function (event) {
    var target = /** @type {Element} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('[data-route-rail-close]')) {
      // The × always closes the route (2026-10-02): the point header's
      // wheel is what clears a point and keeps the route.
      close();
      return;
    }
    if (target.closest('[data-route-rail-details]')) {
      if (openDetails) openDetails();
      return;
    }
    if (target.closest('[data-route-rail-share]')) {
      share();
      return;
    }
    if (target.closest('[data-route-rail-delete]')) {
      remove();
      return;
    }
    // A placed point hides the title the rename edits, so it clears first.
    if (target.closest('[data-route-rename]') && cursor) cursor.setIndex(null);
    if (window.pwaRowRenameCommit && current.uuid) {
      window.pwaRowRenameCommit.handleClick(event, {
        uuidAttribute: 'data-route-rename',
        urlTemplate: RENAME_URL_TEMPLATE,
        csrfToken: csrfToken,
        onCommitted: function () {
          // inline_rename.js restored the label with the OLD text before
          // the write; the input still holds what was committed.
          var name = renameInput ? renameInput.value.trim() : '';
          if (name) {
            current.name = name;
            nameEl.textContent = name;
          }
          announceRoutesChanged();
        },
        onFailed: function () {
          toast(STRINGS['rename-failed']);
        },
      });
    }
  });

  // ---- lifetime ---------------------------------------------------------

  // Escape clears a placed point first, and closes the rail on the next
  // one — but
  // only an Escape nothing else was open to take. The sheets close on Escape from their own document listeners
  // (map_sheet.js), and overflow_menu.js takes one in the capture phase
  // for an open menu, so by the time this listener runs the surface that
  // Escape was meant for may already be shut. Whether anything was open
  // is therefore read BEFORE any of them run, on the window's capture
  // phase, and acted on after, on the document's bubble phase. An Escape
  // typed into a field (the rail's own rename) belongs to the field.
  var escapeWasTaken = false;
  window.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var target = /** @type {Element} */ (event.target);
    escapeWasTaken = !!(
      (target && target.closest && target.closest('input, textarea, select'))
      || document.querySelector('[data-overlay]:not([hidden])')
      || document.querySelector('[data-overflow-open]')
    );
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || escapeWasTaken || rail.hidden) return;
    // SNOW-1064: a point placed on the route clears first, as the point
    // header's wheel does, and keeps the route; the next Escape closes it.
    if (cursor && cursor.state().index !== null) {
      cursor.setIndex(null);
      return;
    }
    close();
  });

  // SNOW-1019: the panel's height changes with its CONTENT as well as with
  // the window — a point header, a long name wrapping to two lines. The change is announced for the leader line (which redraws)
  // and map.js (which re-checks the cursor's dot is not now under the
  // panel).
  if (typeof window.ResizeObserver === 'function') {
    new window.ResizeObserver(function () {
      if (rail.hidden) return;
      document.dispatchEvent(new CustomEvent('snowdesk:route-rail-resized', { detail: null }));
    }).observe(rail);
  }

  window.pwaRouteRail = Object.freeze({
    open: open,
    close: close,
    isOpen: function () { return !rail.hidden; },
    cursor: function () { return cursor; },
    cursorPoint: cursorPoint,
    element: rail,
  });
}());
