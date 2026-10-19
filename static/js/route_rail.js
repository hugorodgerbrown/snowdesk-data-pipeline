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
 * NO SHEET BEHIND IT (SNOW-1095). The route detail sheet (SNOW-973) used
 * to open on the same tap; the rail took its top half — name, figures,
 * profile — and the sheet kept one terrain line behind the menu's
 * "Terrain" item until SNOW-1095 removed the item and the sheet.
 *
 * ITS OWN LIFETIME. The rail is not registered with window.pwaMapOverlays,
 * which would close it the moment any other overlay announced itself. It
 * closes on its own × (`[data-route-rail-close]`), on a tap on empty map
 * (map.js), on Escape when nothing else is open to take that Escape and no
 * point is placed, on a claim or a delete, and it is refilled in place
 * when another route is tapped.
 *
 * ONE BOTTOM SHEET ON A PHONE (2026-10-19). Below 640px the rail is a
 * detent sheet like the region, place and weather sheets, and
 * map_sheet.js's bindDetents closes whichever of them is open when another
 * opens: a route opened closes the region sheet, and a region sheet opened
 * by a search pick closes the route. That is the one exception to its own
 * lifetime, and it holds only where they would stack on the same foot.
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
 * in the title, the leg's own figures — was removed, along with the mouse
 * moving the point by hovering. A TAP on the profile's top line — loosely,
 * OUTLINE_TOLERANCE_PX either side — places the point at that distance; a
 * tap on the route's line on the map places it at the nearest sample
 * (map.js's tapOpenRoute). Either writes the cursor, and everything else
 * follows it.
 *
 * SCRUBBING (2026-10-19, restoring SNOW-1065's drag). A press anywhere on
 * the profile, at any height, that moves more than DRAG_PX sideways
 * scrubs: the point follows the pointer's x for as long as it is held,
 * with any pointer, and the click the drag ends with is swallowed. The
 * lane is `touch-action: pan-y` (static/css/map.css), so a press that
 * starts sideways reaches this script while one that starts upward still
 * scrolls; once a scrub has started, its touchmoves are cancelled, so the
 * finger may drift up or down off the profile and keep scrubbing. The profile only:
 * the route's line on the map stays tap-to-move.
 *
 * THE POINT HEADER (SNOW-1064, SNOW-1068). `open` attaches the point
 * header (route_point_card.js) to the new cursor, with the arrays it reads
 * the point's words from, and `close` detaches it. The header follows the
 * cursor itself, under the profile and its hint, and its wheel clears the
 * point; this module marks the rail `data-route-rail-point` while a point
 * is placed (`paintState`). The route's title and meta line stay put
 * (2026-10-18). The × always closes the route.
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
 * THE ACTIONS. Plan a trip is a link; Share and Rename reuse
 * window.pwaShare and window.pwaRowRenameCommit exactly as the routes
 * panel does; Delete confirms and posts to routes:delete. Each change
 * announces `snowdesk:routes-changed`, which is what makes the map and an
 * open routes panel re-read the list. A pending share has no uuid and none
 * of the owner endpoints would answer for it, so it has no menu at all,
 * and its Save — `options.claim`, the control map.js builds — sits in the
 * identity block (`[data-route-rail-claim]`), where the recipient lands.
 *
 * A BOTTOM SHEET ON A PHONE (SNOW-1099). Below 640px the rail is a sheet
 * with detents (window.MapSheet.bindDetents, bound on the first open), and
 * each detent reveals one more layer (2026-10-18): PEEK, where a tap on a
 * route opens it, is the header, the profile and the hint
 * (`data-sheet-peek`); HALF adds the point header (`data-sheet-half`); and
 * FULL — staff only, declared by the partial — is the point debug readout
 * (`[data-route-rail-debug]`): the staff terrain table's figures for the
 * segment under the point, fetched from /_route-terrain/<uuid>/?format=json
 * the first time the sheet reaches FULL for a route, never before. A point
 * placed while the sheet peeks raises it to HALF. The sheet is below the
 * map's dot, so the leader line stops on its top edge (`isSheet`), and
 * map.js pads the camera's foot rather than its head. At `sm` and above
 * none of this applies.
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
 *   open(feature, {claim?})
 *                  — fill and show the rail for one route feature;
 *                    `claim` is a pending share's Save control
 *   close()        — hide it and drop its cursor
 *   isOpen()       — whether it is showing
 *   cursorPoint()  — the panel's bottom edge below the cursor line (a
 *                    phone sheet's top edge), viewport px, or null; the
 *                    leader line's stop
 *   isSheet()      — whether the rail is the phone's bottom sheet
 *                    (SNOW-1099)
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
  /** How far a press must move sideways, px, before it scrubs. */
  var DRAG_PX = 6;

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
  var closeEl = rail.querySelector('[data-route-rail-close]');
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
  var claimEl = rail.querySelector('[data-route-rail-claim]');
  // SNOW-1099: the phone sheet's staff-only FULL detent — the point debug
  // readout — and the terrain rows it reads.
  var debugEl = /** @type {HTMLElement|null} */ (rail.querySelector('[data-route-rail-debug]'));
  var debugHintEl = rail.querySelector('[data-route-rail-debug-hint]');
  var TERRAIN_URL_TEMPLATE = rail.dataset.routeTerrainUrlTemplate || '';
  var NIL_UUID = '00000000-0000-0000-0000-000000000000';

  // SNOW-1099: on a phone the rail is a bottom sheet with detents
  // (window.MapSheet.bindDetents). Bound on the first open rather than at
  // load, so the order the deferred scripts run in does not matter; null
  // without the sheet modules, and the rail is then the pinned panel alone.
  /** @type {?{open: function(): void, reset: function(): void, set: function(string): void, current: function(): (string|null)}} */
  var detents = null;
  var detentsBound = false;
  /** The uuid whose terrain rows are loaded or loading, or null. */
  var terrainFor = null;
  /** The open route's staff terrain rows, by segment index. */
  var debugRows = [];

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
   * Mark the rail with whether a point is placed (SNOW-1068).
   *
   * The point header itself follows the cursor (route_point_card.js),
   * under the profile; the route's title and meta line stay (2026-10-18).
   * The mark is what static/css/map.css reads to show the point header
   * from sm up only while there is a point to read.
   *
   * @param {?number} index The cursor index; null with no point.
   */
  function paintPointMode(index) {
    var point = index !== null;
    // A point placed while the phone sheet peeks (a tap on the route's line
    // or on the profile) raises it to half, where the point header answers
    // the tap — what the hint at peek promises.
    if (point && detents && detents.current() === 'peek') detents.set('half');
    if (point) rail.setAttribute('data-route-rail-point', '');
    else rail.removeAttribute('data-route-rail-point');
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
    paintDebug(index);
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
   * Every item is an owner action, so the whole menu shows only for a
   * route this visitor owns: a pending share, with no uuid, has none.
   */
  function fillMenu() {
    var owned = !!current.uuid;
    if (actionsEl) actionsEl.hidden = !owned;
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
   * @param {{claim?: ?Node}} [options]
   *   `claim` is a pending share's Save control, seated in the identity
   *   block.
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
    resetTerrain();
    bindSheet();
    if (detents) detents.open();
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
    fillClaim(null);
    paintPointMode(null);
    rail.hidden = true;
    if (mapEl) mapEl.removeAttribute('data-route-rail-open');
    if (detents) detents.reset();
    resetTerrain();
    announceRailChanged();
  }

  // ---- the phone sheet (SNOW-1099) ---------------------------------------

  /** Bind the rail's detents once, if the sheet modules are on the page. */
  function bindSheet() {
    if (detentsBound) return;
    detentsBound = true;
    var sheet = window.MapSheet;
    if (!sheet || typeof sheet.bindDetents !== 'function') return;
    detents = sheet.bindDetents(rail, { onClose: close });
    if (!detents || !debugEl || typeof MutationObserver !== 'function') return;
    // The terrain rows load the first time the sheet reaches FULL for a
    // route, never before: most opens never go there. Leaving FULL puts the
    // sheet's scroll back at the top; map.css hides the readout below full.
    new MutationObserver(function () {
      if (rail.getAttribute('data-detent') === 'full') loadTerrain();
      else rail.scrollTop = 0;
    }).observe(rail, { attributes: true, attributeFilter: ['data-detent'] });
  }

  /** Forget the previous route's terrain rows. */
  function resetTerrain() {
    terrainFor = null;
    debugRows = [];
    paintDebug(null);
  }

  /**
   * Fetch the open route's staff terrain rows for the point debug readout,
   * once per route. Staff only: the partial renders the readout and the URL
   * for staff alone, and the view behind it is staff-gated. A pending share
   * has no uuid, and so no rows.
   */
  function loadTerrain() {
    if (!debugEl || !TERRAIN_URL_TEMPLATE || !current.uuid) return;
    if (terrainFor === current.uuid) return;
    var uuid = current.uuid;
    terrainFor = uuid;
    fetch(TERRAIN_URL_TEMPLATE.replace(NIL_UUID, encodeURIComponent(uuid)), { credentials: 'same-origin' })
      .then(function (resp) { return resp.ok ? resp.json() : { rows: [] }; })
      .then(function (body) {
        // The route may have closed or changed while the rows loaded.
        if (terrainFor !== uuid) return;
        debugRows = body && Array.isArray(body.rows) ? body.rows : [];
        paintDebug(cursor ? cursor.state().index : null);
      })
      .catch(function () {
        // The readout reads "–" without the rows; the next FULL retries.
        if (terrainFor === uuid) terrainFor = null;
      });
  }

  // ---- the point debug readout (staff, FULL on a phone; SNOW-1099) -------
  //
  // Rail two's debug rail, brought back for the point the profile places
  // (SNOW-1065 removed it with rail two). Every figure is the staff terrain
  // table's own, for the segment under the point, so the readout and
  // /_route-terrain/ cannot disagree. English only: a staff surface.

  /**
   * A figure with its unit, or "–" for one that is not known.
   *
   * @param {*} value
   * @param {string} unit
   * @returns {string}
   */
  function debugFigure(value, unit) {
    return typeof value === 'number' && isFinite(value) ? value + unit : '–';
  }

  /**
   * A bearing in degrees with its eight-point compass name.
   *
   * @param {*} degrees
   * @returns {string}
   */
  function debugBearing(degrees) {
    if (typeof degrees !== 'number' || !isFinite(degrees)) return '–';
    var names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    return degrees + '° ' + names[Math.round(degrees / 45) % 8];
  }

  /**
   * The bank in words ("Falling 46.8° to skier's left"); a positive bank
   * falls to the right (bank.py's sign).
   *
   * @param {*} bank
   * @returns {string}
   */
  function debugBanking(bank) {
    if (typeof bank !== 'number' || !isFinite(bank)) return '–';
    if (bank === 0) return 'Level (0°)';
    return 'Falling ' + Math.abs(bank) + '° to skier’s ' + (bank > 0 ? 'right' : 'left');
  }

  /**
   * How far the track points from the fall line, 0–180°: 0 straight down
   * it, 90 across it, 180 straight up it.
   *
   * @param {*} bearing
   * @param {*} aspect
   * @returns {string}
   */
  function debugOffFallLine(bearing, aspect) {
    if (typeof bearing !== 'number' || typeof aspect !== 'number') return '–';
    var delta = Math.abs(bearing - aspect) % 360;
    return Math.round(delta > 180 ? 360 - delta : delta) + '°';
  }

  /**
   * The smoothed track gradient and the segments it was summed over
   * ("-12.4° · 190..194"), naming any in the window that contributed
   * nothing (rejected, or with no height), and flagging a rejected segment.
   *
   * @param {*} row
   * @returns {string}
   */
  function debugTrack(row) {
    if (!row) return '–';
    var text = debugFigure(row.track_gradient_deg, '°');
    var from = row.track_gradient_from;
    var to = row.track_gradient_to;
    if (Number.isInteger(from) && Number.isInteger(to)) {
      var skipped = [];
      for (var k = from; k <= to; k += 1) {
        var other = debugRows[k];
        if (!other || other.track_gradient_rejected
            || typeof other.ele_from_m !== 'number' || typeof other.ele_to_m !== 'number') {
          skipped.push(k);
        }
      }
      text += ' · ' + from + '..' + to + (skipped.length ? ' less ' + skipped.join(', ') : '');
    }
    return row.track_gradient_rejected ? text + ' · rejected' : text;
  }

  /**
   * Fill the readout for the segment under the point; every field reads
   * "–" with no point, or before the rows arrive.
   *
   * @param {?number} index
   */
  function paintDebug(index) {
    if (!debugEl) return;
    var row = index === null ? null : debugRows[index] || null;
    if (debugHintEl) /** @type {HTMLElement} */ (debugHintEl).hidden = index !== null;
    var values = !row ? {} : {
      sample: String(row.i) + ' of ' + debugRows.length,
      km: typeof row.from_m === 'number' && typeof row.length_m === 'number'
        ? (row.from_m / 1000).toFixed(3) + ' km → ' + ((row.from_m + row.length_m) / 1000).toFixed(3) + ' km'
        : '–',
      heights: debugFigure(row.ele_from_m, '') + ' → ' + debugFigure(row.ele_to_m, ' m'),
      aspect: debugBearing(row.aspect_deg),
      angle: debugFigure(row.angle_deg, '°'),
      bearing: debugBearing(row.bearing_deg),
      track: debugTrack(row),
      bank: debugBanking(row.roll_deg),
      fall: debugOffFallLine(row.bearing_deg, row.aspect_deg),
    };
    Array.prototype.forEach.call(
      debugEl.querySelectorAll('[data-route-rail-debug-field]'),
      function (cell) {
        var value = values[cell.getAttribute('data-route-rail-debug-field')];
        cell.textContent = value || '–';
        cell.title = value || '';
      },
    );
  }

  /**
   * Tell the leader line (route_leader.js) the rail opened, closed or was
   * refilled, so it follows the new cursor or clears (SNOW-1019).
   */
  function announceRailChanged() {
    document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed', { detail: null }));
  }

  /**
   * Whether the rail is showing as the phone's bottom sheet rather than the
   * panel pinned top-left: it rests at a detent, which only happens on a
   * phone (map_sheet.js's bindDetents sets data-detent, and map.css gives it
   * a position only below 640px).
   *
   * @returns {boolean}
   */
  function isSheet() {
    if (!rail.hasAttribute('data-detent')) return false;
    if (typeof window.matchMedia !== 'function') return true;
    return !window.matchMedia('(min-width: 640px)').matches;
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
    var box = rail.getBoundingClientRect();
    return {
      x: laneRect.left + fraction * laneRect.width,
      // SNOW-1099: on a phone the panel is a sheet BELOW the dot, so the
      // leader stops on its top edge instead.
      y: isSheet() ? box.top : box.bottom,
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
  // (2026-10-02); a drag anywhere on it scrubs (below). The target is the line, loosely — OUTLINE_TOLERANCE_PX
  // either side — not the whole lane: a tap down in the fill by the ticks
  // sending the point somewhere was unexpected.
  lane.addEventListener('click', function (event) {
    if (swallowClick) {
      swallowClick = false;
      return;
    }
    var index = outlineIndexAt(event);
    if (index !== null) cursor.setIndex(index);
  });

  /**
   * The press being tracked for a scrub: which pointer, where it went
   * down, and whether it has moved far enough sideways to be one.
   *
   * @type {?{id: number, x: number, dragging: boolean}}
   */
  var press = null;
  /** Set by a scrub's end, so the click it ends with places nothing more. */
  var swallowClick = false;

  // A press anywhere on the lane — on the line or down in the fill — that
  // moves DRAG_PX sideways scrubs the point along the profile. A press
  // that never moves that far is left to the click above.
  lane.addEventListener('pointerdown', function (event) {
    if (!cursor || !(sampleCount > 0)) return;
    // One finger scrubs: a second landing mid-scrub, a right-click or a
    // pen's barrel button starts nothing.
    if (press && press.dragging && press.id !== event.pointerId) return;
    if (event.isPrimary === false || event.button !== 0) return;
    press = { id: event.pointerId, x: event.clientX, dragging: false };
    swallowClick = false;
  });

  lane.addEventListener('pointermove', function (event) {
    if (!press || press.id !== event.pointerId) return;
    // A press released off the lane before it became a scrub never sent
    // this lane its pointerup (capture is taken only once it IS one), so
    // a move with no button held ends it here.
    if (!press.dragging && event.buttons === 0) {
      press = null;
      return;
    }
    if (!press.dragging) {
      if (Math.abs(event.clientX - press.x) <= DRAG_PX) return;
      press.dragging = true;
      lane.style.cursor = 'ew-resize';
      if (lane.setPointerCapture) {
        try {
          lane.setPointerCapture(event.pointerId);
        } catch (_err) {
          // A synthetic or already-released pointer cannot be captured.
        }
      }
    }
    var index = indexAt(event.clientX);
    if (index !== null && cursor) cursor.setIndex(index);
  });

  // Once a scrub has started, the finger owns the profile until it lifts:
  // `pan-y` alone lets a later upward drift start a scroll, which cancels
  // the pointer mid-scrub. Chrome fires pointermove before touchmove, so
  // the press is already a scrub by the time this runs.
  lane.addEventListener(
    'touchmove',
    function (event) {
      if (press && press.dragging && event.cancelable) event.preventDefault();
    },
    { passive: false },
  );

  /**
   * End the tracked press; a scrub swallows the click it ends with.
   *
   * @param {PointerEvent} event The pointer event.
   */
  function endPress(event) {
    if (!press || press.id !== event.pointerId) return;
    swallowClick = press.dragging;
    press = null;
  }
  lane.addEventListener('pointerup', endPress);
  // Only the lane's own capture ending ends the press. A touch is
  // implicitly captured by what it landed on — a leg's path — and taking
  // the capture for the lane fires that path's lostpointercapture, which
  // bubbles here mid-scrub.
  lane.addEventListener('lostpointercapture', function (event) {
    if (event.target === lane) endPress(event);
  });
  lane.addEventListener('pointercancel', function (event) {
    endPress(event);
    swallowClick = false;
  });

  // A mouse shows the pointer where a click would place the point, and a
  // sideways arrow over the rest of the profile, where a drag scrubs.
  lane.addEventListener('mousemove', function (event) {
    if (press && press.dragging && press.id !== event.pointerId) return;
    if (!cursor || !(sampleCount > 0)) {
      lane.style.cursor = '';
      return;
    }
    lane.style.cursor = outlineIndexAt(event) !== null ? 'pointer' : 'ew-resize';
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
    isSheet: isSheet,
    element: rail,
  });
}());
