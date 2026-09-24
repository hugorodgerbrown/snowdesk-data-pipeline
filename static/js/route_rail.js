/*
 * static/js/route_rail.js — rail one's DOM half: fills
 * templates/includes/_route_rail.html for the open route (SNOW-1018).
 *
 * A tap on a saved route opens THIS, and only this: map.js's
 * `activateRoute` calls `window.pwaRouteRail.open(feature, options)` with
 * the route's feature from the routes GeoJSON cache — the cached copy, not
 * the rendered one, because a feature read back from the map's tiles has
 * lost the third ordinate the profile is drawn from.
 *
 * THE SHEET IS BEHIND THE MENU. The route detail sheet (SNOW-973) used to
 * open on the same tap. The rail took its top half — name, figures,
 * profile — and the sheet keeps the terrain lines and the day's bulletin
 * reading, one press away on the menu's "Terrain and bulletin" item. The
 * sheet's body is built by map.js, which owns the map state it reads, and
 * handed over as `options.details`: a function this module calls on that
 * press, never at open, so the reading asks for the day the map is showing
 * when the reader asks for it.
 *
 * ITS OWN LIFETIME. The rail stays open while the sheet opens and closes
 * over it — it is not registered with window.pwaMapOverlays, which would
 * close it the moment the sheet it opened announced itself. It closes on
 * its own × (`[data-route-rail-close]`), on Escape when nothing else is
 * open to take that Escape and no leg is open, on a claim or a delete, and it is refilled in
 * place when another route is tapped.
 *
 * WHAT THIS MODULE OWNS. The rail's markup, filled per open; one route
 * cursor per open route (`createRouteCursor`, static/js/route_cursor_core.js,
 * whose first caller this is); and the rail's menu. All the
 * arithmetic — tick step, figures line, where each leg's fill sits — is
 * route_rail_core.js's, and all the copy is the partial's strings template.
 *
 * PRESSING A LEG. Each leg's fill is a focusable `role="button"` path.
 * Pressing it publishes the leg to the cursor as the open leg, and rail
 * two (route_rail_two.js, SNOW-1019) opens under this rail on it.
 * Pressing another leg switches. Pressing INSIDE the open leg moves rail
 * two's window to the place pressed rather than closing the leg; the leg
 * closes on rail two's own ×, or on Escape — the first Escape closes the
 * leg, the next the rail. The pressed state — `aria-pressed` and, through
 * it, the raised fill (src/css/main.css, `.route-rail-leg`) — follows the
 * CURSOR, not the click, so when rail two closes a leg from its own side
 * this rail un-presses without being told.
 *
 * RAIL TWO'S WINDOW. Rail two reports what it shows through `onView`, and
 * this rail draws a bracket (`[data-route-rail-window]`) over that part of
 * the open leg — none while rail two shows the whole leg. Rail two's
 * height joins the rail's, so `onResize` re-publishes it.
 *
 * The cursor is sized from `slope.angles` when the route has one, and
 * otherwise from the legs themselves (the last `to` plus one): legs are a
 * fact about the geometry and ride on an unsampled route too, in the
 * segment indices its record will have (apps/routes/services/leg_wire.py).
 * Only a route with no legs at all — no elevation, or too short — gets the
 * outline alone, with nothing to press.
 *
 * THE ACTIONS. Terrain and bulletin calls `options.details`; Plan a trip
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
 * THE BOTTOM CHROME. While open, `#map` carries `data-route-rail-open` and
 * `--route-rail-height`, the rail's measured height; static/css/map.css
 * raises `--map-bottom-row-offset` from the pair, which moves every
 * bottom-anchored control at once.
 *
 * Publishes (frozen `window.pwaRouteRail`):
 *
 *   open(feature, {details?, claim?})
 *                  — fill and show the rail for one route feature;
 *                    `details` opens the route detail sheet, `claim` is
 *                    a pending share's Save control
 *   close()        — hide it and drop its cursor
 *   isOpen()       — whether it is showing
 *   cursor()       — the open route's cursor, or null; map.js follows it
 *                    to dim every leg but the open one (SNOW-1017)
 *   element        — the rail itself, measured by map.js's fit padding
 */

(function routeRailInit() {
  'use strict';

  var rail = document.getElementById('route-rail');
  if (!rail) return;
  var mapEl = document.getElementById('map');

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-rail-strings-template', {
    'unit-m': '%(value)s m',
    'unit-km': '%(value)s km',
    'figure-distance': '%(km)s km',
    'figure-ascent': '▲%(m)s m',
    'figure-descent': '▼%(m)s m',
    'figure-range': '%(start)s→%(end)s m',
    'leg-climb': 'Leg %(i)s — climb',
    'leg-descent': 'Leg %(i)s — descent',
    'lane-label': 'Elevation profile of %(name)s',
    'readout-hint': 'Press a leg to open it.',
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
  var figuresEl = rail.querySelector('[data-route-rail-figures]');
  var lane = rail.querySelector('[data-route-rail-lane]');
  var ticksEl = rail.querySelector('[data-route-rail-ticks]');
  var readoutEl = rail.querySelector('[data-route-rail-readout]');
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
  /** @type {{uuid: ?string, name: string}} */
  var current = { uuid: null, name: '' };
  /** Opens the open route's detail sheet; null when there is none. */
  var openDetails = null;

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
   * The label a leg is announced and read out by.
   *
   * @param {{i: number, climbing: boolean}} leg
   * @returns {string}
   */
  function legLabel(leg) {
    return interpolate(STRINGS[leg.climbing ? 'leg-climb' : 'leg-descent'], {
      i: String(leg.i),
    });
  }

  /**
   * The route's start and end elevation, for the figures' range.
   *
   * Only a reading taken AT the end is that end. When the GPX's first or
   * last point carries no `<ele>`, readProfile's first run begins inside
   * the route, or its last run stops short of the finish, and the nearest
   * reading is somewhere along the track — a height the route passes, not
   * the one it starts or finishes at. That end is then null, and
   * formatFigures leaves the range out rather than state half of it.
   *
   * @param {object} profile A readProfile result.
   * @returns {{start: ?number, end: ?number}}
   */
  function profileEnds(profile) {
    if (!profile || !profile.hasElevation) return { start: null, end: null };
    var first = profile.runs[0][0];
    var lastRun = profile.runs[profile.runs.length - 1];
    var last = lastRun[lastRun.length - 1];
    return {
      start: first.d === 0 ? first.e : null,
      end: last.d >= profile.distanceM ? last.e : null,
    };
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
        'data-leg-from': String(fill.leg.from),
        'data-leg-to': String(fill.leg.to),
        role: 'button',
        tabindex: '0',
        'aria-pressed': 'false',
        'aria-label': legLabel(fill.leg),
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
        // on theirs, so "0 km" is not cut off at the rail's left edge.
        label.className = fraction === 0
          ? 'absolute top-0'
          : 'absolute top-0 -translate-x-1/2';
        label.style.left = (fraction * 100).toFixed(3) + '%';
        label.textContent = tick.label;
        ticksEl.appendChild(label);
      },
    );
  }

  /**
   * Bring the pressed state and the readout in line with the cursor.
   *
   * @param {?{openLeg: ?{from: number, to: number, i: number, climbing: boolean}}} state
   */
  function paintState(state) {
    var open = state && state.openLeg;
    lane.querySelectorAll('.route-rail-leg').forEach(function (path) {
      var pressed = !!open
        && Number(path.getAttribute('data-leg-from')) === open.from
        && Number(path.getAttribute('data-leg-to')) === open.to;
      path.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    });
    if (open) {
      readoutEl.textContent = legLabel(open);
    } else {
      readoutEl.textContent = legs.length && cursor ? STRINGS['readout-hint'] : '';
    }
  }

  /**
   * The sample a press on the lane landed on, inside one leg.
   *
   * @param {?MouseEvent} event The press, or null for a key.
   * @param {{from: number, to: number}} leg The leg pressed.
   * @returns {number} The leg's middle when the press has no position.
   */
  function pressedIndex(event, leg) {
    var middle = Math.floor((leg.from + leg.to) / 2);
    if (!event || typeof event.clientX !== 'number') {
      var index = cursor ? cursor.state().index : null;
      return index === null ? middle : index;
    }
    var rect = lane.getBoundingClientRect();
    if (!(rect.width > 0)) return middle;
    var at = Math.floor(((event.clientX - rect.left) / rect.width) * sampleCount);
    return Math.min(leg.to, Math.max(leg.from, at));
  }

  /**
   * Press a leg: open it, or move rail two's window within it when it is
   * already the open one.
   *
   * @param {Element} path A `.route-rail-leg` path.
   * @param {?MouseEvent} event The click, or null for a key.
   */
  function pressLeg(path, event) {
    if (!cursor) return;
    var from = Number(path.getAttribute('data-leg-from'));
    var to = Number(path.getAttribute('data-leg-to'));
    var open = cursor.state().openLeg;
    if (open && open.from === from && open.to === to) {
      if (window.pwaRouteRailTwo) {
        window.pwaRouteRailTwo.centreOn(pressedIndex(event, open));
      } else {
        cursor.closeLeg();
      }
      return;
    }
    var leg = legs.find(function (candidate) {
      return candidate.from === from && candidate.to === to;
    });
    if (leg) cursor.openLeg(leg);
  }

  /**
   * Draw the bracket over the part of the open leg rail two shows.
   *
   * @param {?{from: number, to: number}} view Rail two's window, in
   *   continuous sample units; null when rail two is hidden.
   * @param {?{from: number, to: number}} leg The open leg.
   */
  function drawWindow(view, leg) {
    var bracket = lane.querySelector('[data-route-rail-window]');
    var whole = !view || !leg || !(sampleCount > 0)
      || view.to - view.from >= leg.to - leg.from + 1 - 1e-6;
    if (whole) {
      if (bracket) bracket.remove();
      return;
    }
    var box = self.pwaRouteRailCore.BOX;
    if (!bracket) {
      bracket = svgEl('rect', {
        'data-route-rail-window': '',
        y: '1',
        height: String(box.height - 2),
        rx: '2',
        fill: 'currentColor',
        'fill-opacity': '0.08',
        stroke: 'currentColor',
        'stroke-width': '1.5',
        'vector-effect': 'non-scaling-stroke',
        'pointer-events': 'none',
        class: 'text-text-1',
      });
      lane.appendChild(bracket);
    }
    bracket.setAttribute('x', ((view.from / sampleCount) * box.width).toFixed(2));
    bracket.setAttribute(
      'width',
      (((view.to - view.from) / sampleCount) * box.width).toFixed(2),
    );
  }

  /** Write the rail's height onto #map, for the bottom-chrome offset. */
  function publishHeight() {
    if (!mapEl || rail.hidden) return;
    mapEl.style.setProperty('--route-rail-height', rail.offsetHeight + 'px');
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
   *   Terrain and bulletin item; `claim` is a pending share's Save
   *   control, seated in the identity block.
   * @returns {boolean} Whether the rail opened.
   */
  function open(feature, options) {
    var profileCore = self.pwaElevationProfileCore;
    var railCore = self.pwaRouteRailCore;
    if (!feature || !profileCore || !railCore) return false;
    var props = feature.properties || {};

    if (window.pwaRouteRailTwo) window.pwaRouteRailTwo.detach();
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;

    var opts = options || {};
    current = {
      uuid: props.uuid ? String(props.uuid) : null,
      name: props.name || STRINGS.untitled,
    };
    openDetails = typeof opts.details === 'function' ? opts.details : null;
    var slope = props.pending ? null : readJson(props.slope);
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
    var ends = profileEnds(profile);
    var spanM = typeof props.distance_m === 'number' ? props.distance_m : profile.distanceM;

    nameEl.textContent = current.name;
    figuresEl.textContent = railCore.formatFigures(
      {
        distance_m: props.distance_m,
        ascent_m: props.ascent_m,
        descent_m: props.descent_m,
        elevation_start: ends.start,
        elevation_end: ends.end,
      },
      STRINGS,
    );
    drawLane(profile, sampleCount, spanM);
    if (cursor && window.pwaRouteRailTwo) {
      window.pwaRouteRailTwo.attach({
        cursor: cursor,
        slope: slope,
        profile: profile,
        legs: legs,
        sampleCount: sampleCount,
        spanM: spanM,
        onView: drawWindow,
        onResize: publishHeight,
      });
    }
    fillMenu();
    fillClaim(props.pending ? opts.claim || null : null);
    paintState(cursor ? cursor.state() : null);

    rail.hidden = false;
    if (mapEl) mapEl.setAttribute('data-route-rail-open', '');
    publishHeight();
    return true;
  }

  /**
   * Hide the rail and drop its cursor.
   *
   * The open leg is closed FIRST, while every subscriber is still
   * listening, so the map (SNOW-1017) hears `openLeg: null` and restores
   * the legs it dimmed. That covers the rail's own ×, Escape and backdrop
   * closes, none of which the map sees.
   */
  function close() {
    // Empty the cursor before letting it go, so every surface following
    // it — rail two, and the map's leg dimming, selection and cursor dot
    // (map.js's bindRouteCursor) — hears the route close and clears.
    if (cursor) {
      cursor.clearSelection();
      cursor.setIndex(null);
      cursor.closeLeg();
    }
    if (window.pwaRouteRailTwo) window.pwaRouteRailTwo.detach();
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;
    legs = [];
    sampleCount = 0;
    openDetails = null;
    fillClaim(null);
    rail.hidden = true;
    if (mapEl) {
      mapEl.removeAttribute('data-route-rail-open');
      mapEl.style.removeProperty('--route-rail-height');
    }
  }

  // ---- presses ----------------------------------------------------------

  lane.addEventListener('click', function (event) {
    var target = /** @type {Element} */ (event.target);
    var path = target && target.closest ? target.closest('.route-rail-leg') : null;
    if (path) pressLeg(path, /** @type {MouseEvent} */ (event));
  });

  lane.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var target = /** @type {Element} */ (event.target);
    var path = target && target.closest ? target.closest('.route-rail-leg') : null;
    if (!path) return;
    event.preventDefault();
    pressLeg(path, null);
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

  // Escape closes the open leg first, and the rail on the next one — but
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
    if (cursor && cursor.state().openLeg) {
      cursor.closeLeg();
      return;
    }
    close();
  });

  window.addEventListener('resize', publishHeight);

  window.pwaRouteRail = Object.freeze({
    open: open,
    close: close,
    isOpen: function () { return !rail.hidden; },
    cursor: function () { return cursor; },
    element: rail,
  });
}());
