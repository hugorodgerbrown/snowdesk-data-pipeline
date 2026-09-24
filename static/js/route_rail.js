/*
 * static/js/route_rail.js — rail one's DOM half: fills
 * templates/includes/_route_rail.html for the open route (SNOW-1018).
 *
 * map.js's `activateRoute` calls `window.pwaRouteRail.open(feature)` with
 * the route's feature from the routes GeoJSON cache — the cached copy, not
 * the rendered one, because a feature read back from the map's tiles has
 * lost the third ordinate the profile is drawn from. The rail closes when
 * the route detail sheet does, whichever of its five dismissal routes
 * closed it (Escape, click-outside, the overlay registry, overlays.js's
 * dismiss, a programmatic close): watching the sheet's `hidden` attribute
 * is the one place all five meet, where a call from each would be five
 * places to forget one.
 *
 * WHAT THIS MODULE OWNS. The rail's markup, filled per open; one route
 * cursor per open route (`createRouteCursor`, static/js/route_cursor_core.js,
 * whose first caller this is); and the rail's four actions. All the
 * arithmetic — tick step, figures line, where each leg's fill sits — is
 * route_rail_core.js's, and all the copy is the partial's strings template.
 *
 * PRESSING A LEG. Each leg's fill is a focusable `role="button"` path.
 * Pressing it publishes the leg to the cursor as the open leg; pressing
 * the open leg again closes it. The pressed state — `aria-pressed` and,
 * through it, the raised fill (src/css/main.css, `.route-rail-leg`) —
 * follows the CURSOR, not the click, so when rail two (SNOW-1017) closes a
 * leg from its own side this rail un-presses without being told.
 *
 * A route with no `slope` has no `legs` (apps/routes/views.py) and no
 * sample count to build a cursor over, so it gets the identity block and
 * the outline alone: no leg targets, nothing to press.
 *
 * THE ACTIONS. Plan a trip is a link; Share and Rename reuse
 * window.pwaShare and window.pwaRowRenameCommit exactly as the routes
 * panel does; Delete confirms and posts to routes:delete. Each change
 * announces `snowdesk:routes-changed`, which is what makes the map and an
 * open routes panel re-read the list. A pending share has no uuid and
 * none of these endpoints would answer for it, so its menu is hidden.
 *
 * THE BOTTOM CHROME. While open, `#map` carries `data-route-rail-open` and
 * `--route-rail-height`, the rail's measured height; static/css/map.css
 * raises `--map-bottom-row-offset` from the pair, which moves every
 * bottom-anchored control at once.
 *
 * Publishes (frozen `window.pwaRouteRail`):
 *
 *   open(feature)  — fill and show the rail for one route feature
 *   close()        — hide it and drop its cursor
 *   isOpen()       — whether it is showing
 *   cursor()       — the open route's cursor, or null (for SNOW-1017)
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

  /** The open route's cursor, or null. */
  var cursor = null;
  /** Removes this rail's subscription to `cursor`. */
  var unsubscribe = null;
  /** The open route's legs, as they came off the wire. */
  var legs = [];
  /** @type {{uuid: ?string, name: string}} */
  var current = { uuid: null, name: '' };

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
   * First and last elevation of the profile, for the figures' range.
   *
   * @param {object} profile A readProfile result.
   * @returns {{start: ?number, end: ?number}}
   */
  function profileEnds(profile) {
    if (!profile || !profile.hasElevation) return { start: null, end: null };
    var first = profile.runs[0];
    var last = profile.runs[profile.runs.length - 1];
    return { start: first[0].e, end: last[last.length - 1].e };
  }

  /**
   * Draw the lane: the leg fills, the outline, the tick marks, and the
   * tick labels under it.
   *
   * @param {object} profile A readProfile result.
   * @param {number} sampleCount N, the length of `slope.angles`; 0 when
   *   the route has never been sampled.
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
   * Press a leg: open it, or close it when it is already the open one.
   *
   * @param {Element} path A `.route-rail-leg` path.
   */
  function pressLeg(path) {
    if (!cursor) return;
    var from = Number(path.getAttribute('data-leg-from'));
    var to = Number(path.getAttribute('data-leg-to'));
    var open = cursor.state().openLeg;
    if (open && open.from === from && open.to === to) {
      cursor.closeLeg();
      return;
    }
    var leg = legs.find(function (candidate) {
      return candidate.from === from && candidate.to === to;
    });
    if (leg) cursor.openLeg(leg);
  }

  /** Write the rail's height onto #map, for the bottom-chrome offset. */
  function publishHeight() {
    if (!mapEl || rail.hidden) return;
    mapEl.style.setProperty('--route-rail-height', rail.offsetHeight + 'px');
  }

  /**
   * Point the menu at the open route, or hide it for a pending share.
   */
  function fillMenu() {
    if (actionsEl) actionsEl.hidden = !current.uuid;
    if (!current.uuid) return;
    if (planTripEl && PLAN_TRIP_URL) {
      planTripEl.setAttribute(
        'href',
        PLAN_TRIP_URL + '?route=' + encodeURIComponent(current.uuid),
      );
    }
    if (renameEl) renameEl.setAttribute('data-route-rename', current.uuid);
  }

  /**
   * Fill and show the rail for one route.
   *
   * @param {{geometry?: {coordinates?: Array}, properties?: object}} feature
   *   The route feature from the routes GeoJSON cache.
   * @returns {boolean} Whether the rail opened.
   */
  function open(feature) {
    var profileCore = self.pwaElevationProfileCore;
    var railCore = self.pwaRouteRailCore;
    if (!feature || !profileCore || !railCore) return false;
    var props = feature.properties || {};

    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;

    current = {
      uuid: props.uuid ? String(props.uuid) : null,
      name: props.name || STRINGS.untitled,
    };
    var slope = props.pending ? null : readJson(props.slope);
    var wireLegs = readJson(props.legs);
    legs = Array.isArray(wireLegs) ? wireLegs : [];
    var sampleCount = slope && Array.isArray(slope.angles) ? slope.angles.length : 0;
    if (sampleCount > 0 && self.pwaRouteCursorCore) {
      cursor = self.pwaRouteCursorCore.createRouteCursor(sampleCount);
      unsubscribe = cursor.subscribe(paintState);
    } else {
      legs = [];
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
    fillMenu();
    paintState(cursor ? cursor.state() : null);

    rail.hidden = false;
    if (mapEl) mapEl.setAttribute('data-route-rail-open', '');
    publishHeight();
    return true;
  }

  /** Hide the rail and drop its cursor. */
  function close() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;
    legs = [];
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
    if (path) pressLeg(path);
  });

  lane.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var target = /** @type {Element} */ (event.target);
    var path = target && target.closest ? target.closest('.route-rail-leg') : null;
    if (!path) return;
    event.preventDefault();
    pressLeg(path);
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

  // The rail lives exactly as long as the route detail sheet: see the
  // header for why this watches the attribute rather than each close path.
  var detailSheet = document.getElementById('route-detail-sheet');
  if (detailSheet && typeof MutationObserver === 'function') {
    new MutationObserver(function () {
      if (detailSheet.hasAttribute('hidden') && !rail.hidden) close();
    }).observe(detailSheet, { attributes: true, attributeFilter: ['hidden'] });
  }

  window.addEventListener('resize', publishHeight);

  window.pwaRouteRail = Object.freeze({
    open: open,
    close: close,
    isOpen: function () { return !rail.hidden; },
    cursor: function () { return cursor; },
    element: rail,
  });
}());
