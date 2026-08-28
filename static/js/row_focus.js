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

  var FOCUS_SELECTOR = '[data-row-focus]';

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

    var coordinates = parse(button.getAttribute('data-row-focus'));
    if (!coordinates) return true;

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
    if (!focus) return true;
    if (coordinates.length === 2) focus.point(coordinates[0], coordinates[1]);
    else focus.bounds(coordinates);
    return true;
  }

  window.pwaRowFocus = Object.freeze({
    handleClick: handleClick,
    parse: parse,
  });
})();
