/*
 * static/js/row_focus.js — framing the place a UGC panel row names.
 *
 * Hugo: "For routes, resorts, and observations, clicking on the name of an
 * item should zoom in to it." The row's name is a real `<button>` on those
 * three map panels (includes/_ugc_panel_row.html's `focus_target`), and
 * this is what the button does. The sibling of static/js/row_rename_commit.js,
 * in the same shape and for the same reason: three panels, one behaviour,
 * one owner.
 *
 * Three steps, and the ORDER of them is the whole module:
 *
 *   1. TURN THE OVERLAY ON. Flying to a route with the routes layer
 *      switched off lands on an empty map, which reads as a broken button
 *      rather than as a hidden layer. Somebody who presses "take me to
 *      this" means to see it, so the panel's own overlay bridge is asked
 *      to show — the same call the panel's "Display on the map" switch
 *      makes, so the preference persists and the lazy load runs. The
 *      ``/map/?favourite=<uuid>`` deep link set this precedent and its own
 *      note in static/js/map.js explains why flipping `visibility` by hand
 *      instead would be undone by the next thing to re-read the
 *      preference.
 *   2. CLOSE THE PANEL. Below `sm` the sheet is a full-width bottom dock
 *      covering the map (includes/_overlay_sheet.html), so a zoom behind
 *      it would be invisible on a phone — the device most likely to be
 *      holding this panel open. Closing on every width rather than only
 *      the narrow one keeps one behaviour to describe, and the roundel
 *      that opened the panel is one tap away.
 *   3. MOVE THE CAMERA, through window.pwaMapFocus. See that bridge in
 *      static/js/map.js for why it takes a coordinate rather than a uuid.
 *
 * SNOW-835 inserts a fourth step BETWEEN the close and the camera, for
 * the one panel whose rows name a basemap as well as a place: switch to
 * that basemap first. The downloads panel's squares are filtered to the
 * active basemap's tile template (`refreshDownloadedOverlay` in map.js),
 * so framing a Swisstopo area while the map is on OpenFreeMap lands on
 * ground with nothing drawn on it — the row took the camera with it and
 * left the basemap behind. It goes after the close so the sheet is
 * already out of the way while the new style loads, and the camera then
 * WAITS for `snowdesk:basemap-changed`: `setStyle` tears every layer off
 * the map and a `fitBounds` issued before the reinstall lands is silently
 * discarded, which would switch the basemap and go nowhere. Silent, like
 * everything else here — the group heading the row sits under already
 * names the basemap (SNOW-832).
 *
 * NO USER-FACING STRINGS. Nothing here is announced: the button's own
 * "Zoom to <name>" is server-rendered in the row, where makemessages can
 * see it (docs/i18n.md), and a press that cannot be served moves nothing
 * rather than explaining itself. That silence is deliberate — every way
 * this returns early is a state the user cannot act on (the map bundle
 * has not booted, the row carries no coordinate), and a toast for it
 * would be noise on a surface that is already showing them their list.
 */

(function rowFocusInit() {
  'use strict';

  // Two attributes, one control. `data-row-focus` carries degrees — the
  // three server-rendered panels' only form. `data-row-focus-region`
  // (SNOW-811) carries an EAWS micro-region id instead, for the downloads
  // panel's region rows: a region download is recorded against its id and
  // deliberately carries no bbox, because the polygon is already on the map
  // and a stored box would be a second, coarser copy of a boundary
  // MapLibre is drawing anyway. Resolving it is window.pwaMapFocus's job,
  // not this module's.
  var FOCUS_SELECTOR = '[data-row-focus], [data-row-focus-region]';

  // A third attribute, deliberately NOT in the selector above:
  // `data-row-focus-basemap` (SNOW-835) says which basemap to be on when
  // the camera lands, never on its own where to land, so it can no more
  // make a label pressable than an empty `data-row-focus` can. Only the
  // downloads panel's rows carry it, stamped on the clone by
  // map_downloads_manager.js's `applyRowFocus` — the three server-rendered
  // panels pass nothing, so every branch reading it is skipped for them
  // and their behaviour is the one this module had before.
  var BASEMAP_ATTRIBUTE = 'data-row-focus-basemap';

  // How long the camera waits for the new style before moving anyway. A
  // style that fails to load still leaves a map worth moving — offline on
  // a basemap this device has not downloaded resolves to SNOW-483's inline
  // fallback, which is a real style with real coordinates — so the wait
  // is bounded rather than a promise that can hang (see
  // docs/decisions/bounded-offline-read-paths.md for the same rule on the
  // read paths). Four seconds is long enough for a cold style + sprite
  // fetch on a slow connection and short enough that a user who pressed a
  // row is not left looking at an unmoved map wondering whether it
  // registered.
  var BASEMAP_SETTLE_MS = 4000;

  /**
   * Parse a row's `data-row-focus` into numbers.
   *
   * The attribute is plain comma-separated degrees, written by
   * includes/_ugc_panel_row.html through `stringformat:"f"` — see that
   * partial's `focus_target` note for why a locale-formatted float or a
   * JSON attribute would not survive the trip.
   *
   * @param {?string} value The attribute's value.
   * @returns {?number[]} Two numbers ([lon, lat]) or four ([west, south,
   *   east, north]); null for anything else, including a partially
   *   numeric string — a bbox missing an ordinate frames nothing, and
   *   guessing at it would fly the map somewhere arbitrary.
   */
  function parse(value) {
    if (!value) return null;
    var parts = String(value).split(',');
    if (parts.length !== 2 && parts.length !== 4) return null;
    var numbers = [];
    for (var i = 0; i < parts.length; i += 1) {
      var n = Number(parts[i].trim());
      // Number('') is 0, so an empty ordinate would otherwise pass as the
      // equator rather than as the malformed attribute it is.
      if (parts[i].trim() === '' || !isFinite(n)) return null;
      numbers.push(n);
    }
    return numbers;
  }

  /**
   * The basemap picker row to press to get onto `key`, if pressing one is
   * both possible and needed (SNOW-835).
   *
   * The swap is DELEGATED to that row rather than reimplemented here.
   * map_basemap_picker.js's own click handler dispatches
   * `snowdesk:basemap-changing` for the consumers that must surrender
   * control (the timelapse), persists the preference, moves `aria-checked`
   * across the radio group, closes the popover and only then calls
   * `resolveBasemapStyle(…).then(setStyle)`. A second implementation of
   * that sequence would be a preference left unpersisted and a popover
   * showing the wrong row checked — so this module's whole contribution is
   * finding the row and clicking it.
   *
   * The checked row is also how "which basemap am I on" is answered here,
   * the same source map_basemap_downloads.js's `activeBasemapKey` reads:
   * the picker DOM leads the render (it is updated synchronously on click,
   * before `setStyle` has resolved), which for this caller is the right
   * end of that lag — a second press while a swap is in flight sees the
   * incoming basemap and does not queue a redundant one.
   *
   * Iterates and compares `dataset.basemapKey` per element rather than
   * interpolating a stored key into a selector, the rule
   * map_basemap_downloads.js's `basemapLabel` already follows: the key
   * comes off a record on disk, and selector syntax in one would otherwise
   * be an injection.
   *
   * @param {?string} key The row's stored basemap key.
   * @returns {?HTMLElement} The picker row, or null when there is nothing
   *   to press: no key, no picker on the page, no row offering that key
   *   (a `swisstopo_light` record whose basemap has since been retired),
   *   the key is already active, or the row is inert. The caller frames
   *   without switching in every one of those cases.
   */
  function basemapSwitchTarget(key) {
    if (!key) return null;
    var menu = document.getElementById('basemap-menu');
    if (!menu) return null;
    var items = menu.querySelectorAll('.basemap-menu-item[data-basemap-key]');
    var match = null;
    for (var i = 0; i < items.length; i += 1) {
      if (items[i].dataset.basemapKey === key) {
        match = items[i];
        break;
      }
    }
    if (!match) return null;
    // Already on it: no setStyle, no flicker, and no wait — this is the
    // common case for every row in the group the map is already showing.
    if (match.getAttribute('aria-checked') === 'true') return null;
    // A row map_layer_sync_status.js has disabled (offline, and that
    // basemap's style is not cached) is inert — the picker's own handler
    // returns on the same attribute. Clicking it would do nothing and then
    // hold the camera for the full settle timeout, so the press frames
    // straight away instead.
    if (match.getAttribute('aria-disabled') === 'true') return null;
    return match;
  }

  /**
   * Run `then` once the new basemap's style has settled, or give up
   * waiting (SNOW-835).
   *
   * `setStyle` is asynchronous and wipes every app layer; map.js
   * re-installs them on `styledata` and ends by dispatching
   * `snowdesk:basemap-changed`. A camera move issued before that lands is
   * discarded by MapLibre without an error, so the wait is the feature —
   * and the timeout is what keeps a style that never loads from swallowing
   * the press entirely.
   *
   * @param {function(): void} then What to do when the style has settled.
   * @returns {void}
   */
  function afterBasemapSettles(then) {
    var settled = false;
    var timer = null;
    function done() {
      // Whichever of the two arrives first wins, and the other is torn
      // down: a listener left on `document` would fire again on the next
      // swap the user makes by hand, framing a row they pressed minutes
      // ago.
      if (settled) return;
      settled = true;
      document.removeEventListener('snowdesk:basemap-changed', done);
      if (timer !== null) clearTimeout(timer);
      then();
    }
    document.addEventListener('snowdesk:basemap-changed', done);
    timer = setTimeout(done, BASEMAP_SETTLE_MS);
  }

  /**
   * Move the camera to whichever address the row carried.
   *
   * Its own function since SNOW-835 because there are now two moments it
   * can run at — immediately, or after a basemap swap has settled — and
   * the answer must not differ between them.
   *
   * @param {Object} focus window.pwaMapFocus, read by the caller.
   * @param {?number[]} coordinates Two ordinates or four, as `parse`
   *   returns them.
   * @param {?string} regionId An EAWS micro-region id.
   * @returns {void}
   */
  function frame(focus, coordinates, regionId) {
    // Degrees win when a row somehow carries both: they are the exact
    // extent, where the region id is a lookup that can miss (see
    // pwaMapFocus.region on regions loading per country).
    if (coordinates && coordinates.length === 2) {
      focus.point(coordinates[0], coordinates[1]);
    } else if (coordinates) {
      focus.bounds(coordinates);
    } else if (focus.region) {
      focus.region(regionId);
    }
  }

  /**
   * Handle a click that may be a row asking to be framed on the map.
   *
   * Shaped like window.pwaRowRenameCommit.handleClick, and answering the
   * same question its callers ask next: "was this mine, or should I keep
   * testing?" A click that is not on a focus button returns false and is
   * left entirely alone.
   *
   * A focus button that cannot be served returns TRUE without moving
   * anything. The click WAS a focus — reporting it as "not mine" would
   * send it on to the caller's add-CTA test, which is a different action
   * on the same surface.
   *
   * Returns as soon as it has claimed the press either way: a row that
   * needs its basemap switching first (SNOW-835) frames LATER, from the
   * `snowdesk:basemap-changed` listener, so a true answer means "this was
   * mine", not "the camera has already moved".
   *
   * @param {MouseEvent} event The click.
   * @param {{
   *   overlay: ?object,
   *   close: ?function(): void,
   * }} options
   *   `overlay` is this panel's own window.pwa*Overlay bridge, switched on
   *   before the flight when the user has it off; `close` dismisses the
   *   panel so the map is visible when the camera arrives.
   * @returns {boolean} Whether this click was a focus.
   */
  function handleClick(event, options) {
    var target = event && event.target;
    var button = target && target.closest ? target.closest(FOCUS_SELECTOR) : null;
    if (!button) return false;

    var regionId = button.getAttribute('data-row-focus-region');
    var coordinates = parse(button.getAttribute('data-row-focus'));
    // Neither form resolved — a region row and a coordinate row are the
    // only two there are, so this is a button with nothing behind it. The
    // press WAS a focus (see the note above), so it is claimed and
    // silently dropped rather than passed on.
    if (!coordinates && !regionId) return true;

    var overlay = options && options.overlay;
    // isEnabled(), the persisted preference — not isVisible(), which is
    // what MapLibre is drawing. An overlay enabled offline with nothing
    // cached reads enabled and paints nothing, and calling show() again
    // for it would neither draw anything nor be what the user asked for.
    if (overlay && overlay.isEnabled && !overlay.isEnabled() && overlay.show) {
      overlay.show();
    }

    if (options && options.close) options.close();

    var focus = window.pwaMapFocus;
    // No map bundle, so nothing to frame — and therefore nothing to switch
    // the basemap FOR either. Persisting a new preference here would be a
    // side effect with no camera move to justify it, which is the same
    // reason an account-only row never carries the attribute at all.
    if (!focus) return true;

    // SNOW-835: the basemap the row's area was downloaded against. Absent
    // on every row of the other three panels, and on the downloads rows
    // that have nothing to switch to — so this resolves to null and the
    // synchronous path below is the one this module has always taken.
    var picker = basemapSwitchTarget(button.getAttribute(BASEMAP_ATTRIBUTE));
    if (!picker) {
      frame(focus, coordinates, regionId);
      return true;
    }

    // Listener first, click second. `setStyle` cannot resolve
    // synchronously, so the order is not load-bearing today — but binding
    // after the press is the shape that would miss the event if it ever
    // became so.
    afterBasemapSettles(function () {
      frame(focus, coordinates, regionId);
    });
    picker.click();
    return true;
  }

  window.pwaRowFocus = Object.freeze({
    handleClick: handleClick,
    parse: parse,
  });
})();
