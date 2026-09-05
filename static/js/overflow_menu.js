/*
 * static/js/overflow_menu.js — reusable "…" kebab overflow menu (SNOW-645,
 * re-adopted and fixed by SNOW-830).
 *
 * The DOM half of includes/_overflow_menu.html — see that partial's own
 * header for why this exists and what it replaces.
 *
 * Delegated and instance-agnostic: ANY number of [data-overflow-menu]
 * wrappers on the page (one per route row, today) are driven by the ONE
 * set of listeners below — there is no per-instance JS to bind, which
 * matters because static/js/routes.js re-clones the whole sheet body on
 * every open (and map_downloads_manager.js re-clones every row on every
 * render()), discarding any listener bound to the previous copy. A
 * per-instance IIFE closure — the shape static/js/map_basemap_picker.js's
 * own (single, hardcoded #basemap-menu) popover uses — would have to be
 * rebound after every render.
 *
 * Mirrors that same popover's open/outside-click/Escape behaviour,
 * generalised from one hardcoded element to "whichever
 * [data-overflow-menu] the event landed in or near". At most one menu is
 * ever open at once, matching every other popover in this app.
 *
 * Loaded once from apps/public/templates/public/partials/_routes_surface.html,
 * next to share.js / routes.js — the scripts that surface needs and no
 * other page does. (It claimed to load from _map_downloads_sheet.html for
 * its whole life; nothing loaded it anywhere, because SNOW-658 dropped
 * the ellipsis menu from every panel the day after SNOW-645 built it.
 * SNOW-830 brought it back for the route row alone — see
 * includes/_overflow_menu.html's header for which panels have one and
 * which keep their visible trash.)
 *
 * THREE THINGS SNOW-830 HAD TO FIX before the primitive could be used on
 * a map panel at all. Each is fixed HERE rather than worked around at the
 * call site, because each is a property of the menu and not of the route
 * row:
 *
 *   1. POSITION IS `fixed`, MEASURED ON OPEN — not the `absolute right-0`
 *      the partial used to carry. Two clipping ancestors sit between a
 *      panel row and the viewport: includes/_ugc_panel.html's
 *      `overflow-y-auto` scroll container (declaring `overflow-y`
 *      computes `overflow-x` to `auto` as well, so it clips on both axes)
 *      and includes/_overlay_sheet.html's `overflow-hidden` sheet. An
 *      absolutely-positioned menu on the last row of a long list has
 *      nowhere to go. `fixed` escapes both because neither establishes a
 *      containing block — the sheet is plain `fixed` + `overflow-hidden`
 *      with no `transform`, `filter`, `backdrop-filter` or `contain`
 *      (`--shadow-glass` is a box-shadow, which creates none).
 *
 *   2. IT CLOSES ON SCROLL AND RESIZE. The menu is anchored to a row that
 *      is moving away, and a `fixed` box does not move with it. Closing
 *      beats repositioning: the user scrolled, so the row they opened the
 *      menu on is no longer the row under it. `scroll` does not bubble,
 *      so the listener is registered with `{capture: true}` on `document`
 *      to catch the rows container's own scrolling.
 *
 *   3. ESCAPE IS BOUND IN CAPTURE PHASE. static/js/map_sheet.js binds its
 *      own Escape-closes-the-sheet handler on `document` in BUBBLE phase,
 *      so a bubble-phase listener here could not stop it — both are on
 *      the same node and `stopPropagation` between two listeners on one
 *      node in one phase does nothing. Capture runs before every bubble
 *      listener on the way down, so an Escape that closes a menu can call
 *      `stopPropagation()` and the sheet never hears it. The call is made
 *      ONLY when a menu is actually open, so an Escape with none open
 *      falls through untouched and still closes the sheet.
 */

(function overflowMenuInit() {
  'use strict';

  var OPEN_ATTR = 'data-overflow-open';

  // The gap between the trigger and the menu, in CSS pixels — the 4px the
  // partial's own `mt-1` used to draw, now that JS owns the position. Also
  // the minimum clearance kept from every viewport edge.
  var GAP = 4;

  /**
   * @param {Element} wrapper A `[data-overflow-menu]` element.
   * @returns {HTMLButtonElement|null}
   */
  function trigger(wrapper) {
    return wrapper.querySelector('[data-overflow-trigger]');
  }

  /**
   * @param {Element} wrapper A `[data-overflow-menu]` element.
   * @returns {HTMLElement|null}
   */
  function menu(wrapper) {
    return wrapper.querySelector('[role="menu"]');
  }

  /**
   * Place an open menu against its trigger, in viewport coordinates.
   *
   * Right edge to right edge: the trigger is the last thing in a row, so
   * a menu hanging leftwards from it stays inside the panel while one
   * hanging rightwards would immediately leave it. Below the trigger by
   * preference, flipped above when the menu would not fit below — which
   * is the case this whole function exists for, the last row of a full
   * panel.
   *
   * Called AFTER the menu is unhidden, because a `hidden` element has no
   * box to measure.
   *
   * @param {HTMLElement} triggerEl The `[data-overflow-trigger]` button.
   * @param {HTMLElement} menuEl The `[role="menu"]` element, already shown.
   * @returns {void}
   */
  function place(triggerEl, menuEl) {
    var anchor = triggerEl.getBoundingClientRect();
    menuEl.style.position = 'fixed';
    // Cleared first so a re-open measures the menu's own natural box
    // rather than the one the previous placement squeezed it into.
    menuEl.style.top = '';
    menuEl.style.left = '';

    var width = menuEl.offsetWidth;
    var height = menuEl.offsetHeight;
    var viewportWidth = window.innerWidth || 0;
    var viewportHeight = window.innerHeight || 0;

    var left = anchor.right - width;
    if (left < GAP) left = GAP;
    if (viewportWidth && left + width > viewportWidth - GAP) {
      left = Math.max(GAP, viewportWidth - GAP - width);
    }

    var top = anchor.bottom + GAP;
    var fitsBelow = !viewportHeight || top + height <= viewportHeight - GAP;
    if (!fitsBelow) {
      var above = anchor.top - GAP - height;
      // Flip only if there is genuinely more room above: on a viewport
      // too short for the menu either way, hanging off the bottom (where
      // the page can still be scrolled) beats hanging off the top.
      if (above >= GAP) top = above;
    }

    menuEl.style.top = top + 'px';
    menuEl.style.left = left + 'px';
  }

  /**
   * @param {Element} wrapper A `[data-overflow-menu]` element.
   * @param {boolean} open
   * @returns {void}
   */
  function setOpen(wrapper, open) {
    var t = trigger(wrapper);
    var m = menu(wrapper);
    if (!t || !m) return;
    if (open) {
      wrapper.setAttribute(OPEN_ATTR, '');
    } else {
      wrapper.removeAttribute(OPEN_ATTR);
    }
    t.setAttribute('aria-expanded', open ? 'true' : 'false');
    m.hidden = !open;
    if (open) {
      place(t, m);
    } else {
      // Leave nothing behind: the next open re-measures from scratch, and
      // a closed menu carrying last time's coordinates is a trap for
      // anyone reading the DOM.
      m.style.position = '';
      m.style.top = '';
      m.style.left = '';
    }
  }

  /**
   * Close every open menu except `except` — at most one is ever open at
   * once, matching every other popover in this app (the basemap picker,
   * the layers menu).
   *
   * @param {Element|null} except
   * @returns {void}
   */
  function closeAllExcept(except) {
    var open = document.querySelectorAll('[data-overflow-menu][' + OPEN_ATTR + ']');
    for (var i = 0; i < open.length; i += 1) {
      if (open[i] !== except) setOpen(open[i], false);
    }
  }

  /**
   * @returns {Element|null} The one open menu's wrapper, if any.
   */
  function openWrapper() {
    return document.querySelector('[data-overflow-menu][' + OPEN_ATTR + ']');
  }

  // Click (not pointerdown), same reasoning as the basemap picker's own
  // outside-click handler: an item selection inside an open menu has to
  // fire ITS OWN handler (routes.js's delegated share/rename handling,
  // bound on the sheet — a descendant of document, so it runs first as
  // the event bubbles) before this one closes the menu out from under it.
  document.addEventListener('click', function (event) {
    var target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;

    var openTrigger = target.closest('[data-overflow-trigger]');
    if (openTrigger) {
      var wrapper = openTrigger.closest('[data-overflow-menu]');
      if (!wrapper) return;
      var willOpen = !wrapper.hasAttribute(OPEN_ATTR);
      // Only one open at a time: opening this one closes every other;
      // closing this one (a second click on its own trigger) just closes
      // everything, itself included.
      closeAllExcept(willOpen ? wrapper : null);
      setOpen(wrapper, willOpen);
      return;
    }

    // Anything else — an item inside an open menu (handled by the
    // caller's own listener first, per the comment above) or genuinely
    // outside — closes every open menu.
    closeAllExcept(null);
  });

  // Capture phase, and stopPropagation only when there is something to
  // close — see reason 3 in this module's header. Without it, one Escape
  // closes the menu AND the sheet the menu sits in.
  document.addEventListener(
    'keydown',
    function (event) {
      if (event.key !== 'Escape') return;
      var open = openWrapper();
      if (!open) return;
      event.stopPropagation();
      setOpen(open, false);
      var t = trigger(open);
      if (t) t.focus();
    },
    true
  );

  // A `fixed` menu does not travel with the row it was opened from, so
  // both of these close it rather than re-place it. `scroll` does not
  // bubble: capture is what catches the panel's own scroll container
  // rather than only a scroll of the document.
  document.addEventListener(
    'scroll',
    function () {
      if (openWrapper()) closeAllExcept(null);
    },
    true
  );

  window.addEventListener('resize', function () {
    if (openWrapper()) closeAllExcept(null);
  });
})();
