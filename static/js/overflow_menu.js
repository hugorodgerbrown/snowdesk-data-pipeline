/*
 * static/js/overflow_menu.js — reusable "…" kebab overflow menu (SNOW-645).
 *
 * The DOM half of includes/_overflow_menu.html — see that partial's own
 * header for why this exists and what it replaces.
 *
 * Delegated and instance-agnostic: ANY number of [data-overflow-menu]
 * wrappers on the page (one per "Manage downloads" row, today) are driven
 * by the ONE pair of listeners below — there is no per-instance JS to
 * bind, which matters because map_downloads_manager.js re-clones every
 * row from its <template> on every render() (see that module's own
 * header for why), discarding any listener bound to the previous copy.
 * A per-instance IIFE closure — the shape static/js/map_basemap_picker.js's
 * own (single, hardcoded #basemap-menu) popover uses — would have to be
 * rebound after every render.
 *
 * Mirrors that same popover's open/outside-click/Escape behaviour,
 * generalised from one hardcoded element to "whichever
 * [data-overflow-menu] the event landed in or near". At most one menu is
 * ever open at once, matching every other popover in this app.
 *
 * Loaded once from apps/public/templates/public/partials/_map_downloads_sheet.html,
 * next to basemap_manage_core.js / map_downloads_manager.js — the three
 * scripts this sheet needs and no other page does.
 */

(function overflowMenuInit() {
  'use strict';

  var OPEN_ATTR = 'data-overflow-open';

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

  // Click (not pointerdown), same reasoning as the basemap picker's own
  // outside-click handler: an item selection inside an open menu has to
  // fire ITS OWN handler (map_downloads_manager.js's delegated
  // data-downloads-rename/-delete handling, bound on the sheet — a
  // descendant of document, so it runs first as the event bubbles) before
  // this one closes the menu out from under it.
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

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var open = document.querySelector('[data-overflow-menu][' + OPEN_ATTR + ']');
    if (!open) return;
    setOpen(open, false);
    var t = trigger(open);
    if (t) t.focus();
  });
})();
