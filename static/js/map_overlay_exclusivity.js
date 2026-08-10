/*
 * static/js/map_overlay_exclusivity.js — one open map overlay at a time
 * (SNOW-658).
 *
 * Only one overlay can be open over the map at any moment, so opening any
 * one of them has to close every other. Until this module that rule was
 * enforced by hand, one pair at a time, by whichever ticket added the
 * newest surface:
 *
 *   map_downloads_manager.js  called window.pwaLayersMenu?.close() on the
 *                             way in — the downloads sheet knew about the
 *                             layers menu and about nothing else.
 *   favourites.js             dispatched snowdesk:favourite-detail-close
 *                             from onBeforeOpen, and listened for
 *                             snowdesk:map-detail-opening — the favourite
 *                             sheet and the anchored detail popup knew
 *                             about each other, and about nothing else.
 *
 * Six surfaces wired pairwise is fifteen relationships; the code had three
 * of them. That is why the set was incomplete rather than wrong — every
 * pair had to be remembered by the surface added last, and the report
 * sheet, added without any of this, closed nothing and was closed by
 * nothing.
 *
 * So each surface now states one fact about itself — "I am an overlay,
 * here is how to ask whether I am open and how to close me" — and one
 * fact when it opens: "I am opening". This module owns the rest, and a
 * surface added later gets the whole matrix by registering, without
 * naming a single sibling.
 *
 *   register(name, controller)  controller is { isOpen(), close() }.
 *                               Registering a name twice replaces the
 *                               first entry (a module re-imported under
 *                               test, or a surface re-attached after a
 *                               swap) rather than leaving a stale
 *                               controller listening.
 *   opening(name)               close every OTHER registered overlay.
 *                               Called by the opening surface before it
 *                               reveals itself, so the two are never on
 *                               screen together for a frame.
 *   closeAll()                  close every registered overlay.
 *   names()                     the registered names, sorted — what the
 *                               exclusivity matrix in
 *                               tests/js/test_map_overlay_exclusivity.js
 *                               runs over, so a surface that forgets to
 *                               register fails there rather than on
 *                               Hugo's screen.
 *
 * The registered set is the layers menu, the three UGC sheets (downloads,
 * favourites, field observations), the anchored map-detail popup (a resort
 * or an existing favourite pin), the legend card and the help tour's
 * coachmark.
 *
 * The last two came in behind the rest, and are the clearest illustration of
 * why the pairwise wiring kept missing cases: both already had a dismiss
 * handler that looked like it covered this — "any click outside me closes
 * me" — and neither did. Their own toggles call ``stopPropagation`` (they
 * have to; without it the opening click reads as a click away), so no other
 * surface's identical handler ever saw them open, and a surface opened
 * programmatically rather than by a document click never reached theirs.
 *
 * ``close()`` is only called on a surface that reports itself open, so a
 * controller's close path never runs for a surface already closed — the
 * sheets' own teardown (map_sheet.js) empties the sheet body, which is
 * not free and not idempotent-looking to a reader of the DOM.
 *
 * Loaded from public/home.html ahead of every registering module (all
 * deferred, so document order is execution order). Every call site uses
 * optional chaining anyway: a page that renders one of these surfaces
 * without this module still works, it simply stops being exclusive.
 */

(function mapOverlayExclusivityInit() {
  'use strict';

  /**
   * The registered overlays, by name.
   *
   * @type {Map<string, { isOpen: function(): boolean, close: function(): void }>}
   */
  var overlays = new Map();

  /**
   * Register an overlay, replacing any earlier entry under the same name.
   *
   * @param {string} name Stable identifier — the element's own DOM id
   *   wherever it has one, so a failure names something greppable.
   * @param {{ isOpen: function(): boolean, close: function(): void }} controller
   *   How to ask whether this overlay is open, and how to close it.
   * @returns {void}
   */
  function register(name, controller) {
    if (!name || !controller) return;
    overlays.set(name, controller);
  }

  /**
   * Close every registered overlay except ``name``.
   *
   * Called by a surface as it opens. Passing the name of something never
   * registered is not an error — it closes everything, which is the right
   * answer for a surface that opts into the rule without needing to be
   * closed by anyone else.
   *
   * @param {string} name The overlay doing the opening.
   * @returns {void}
   */
  function opening(name) {
    overlays.forEach(function (controller, registered) {
      if (registered === name) return;
      if (!controller.isOpen()) return;
      controller.close();
    });
  }

  /**
   * Close every registered overlay.
   *
   * @returns {void}
   */
  function closeAll() {
    opening(null);
  }

  /**
   * The registered names, sorted.
   *
   * @returns {string[]}
   */
  function names() {
    return Array.from(overlays.keys()).sort();
  }

  window.pwaMapOverlays = Object.freeze({
    register: register,
    opening: opening,
    closeAll: closeAll,
    names: names,
  });
}());
