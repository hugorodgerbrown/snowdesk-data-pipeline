/*
 * static/js/row_removed.js — SNOW-752: notice a row leaving a UGC list.
 *
 * Every list of a user's own data — favourites, routes, field observations,
 * on the map panels and again on their account pages — removes a row the
 * same way: the row carries a plain HTMX form, the delete endpoint answers
 * an empty 200, and `hx-target`/`hx-swap="outerHTML"` on the row's own id
 * turns that into a removal with no JS involved.
 *
 * What the surfaces then need is identical too, and until this module they
 * each wrote it out: know that a row has gone, so the list can be re-read
 * (its empty state is a server-side clause, and the swap that emptied one
 * <li> cannot produce one) and the map told (a deleted pin, line or flag
 * has to leave the layer as well as the list).
 *
 * WHY THIS IS AWKWARD ENOUGH TO SHARE. The obvious version — one
 * `htmx:afterRequest` listener asking whether the request came from inside
 * the list — does not work. An `outerHTML` swap DETACHES the element that
 * made the request before that event fires, so `elt.closest(...)` runs
 * against an orphan and answers null. So it is two steps: mark on
 * `htmx:beforeRequest`, while the tree is still attached, and act on
 * `htmx:afterRequest`, the last event in htmx's lifecycle.
 *
 * And the mark cannot be a variable. Both listeners are document-level, so
 * every HTMX request on the page runs through them — the map homepage
 * carries four of these panels at once, plus report.js's own form-load
 * `htmx.ajax()` — and a shared variable is rewritten by whichever request
 * fires last, with no way to correlate an afterRequest back to the
 * beforeRequest that set it. htmx carries the same `xhr` object on both
 * events, so the mark rides the request it describes.
 *
 * That reasoning was written out three times (favourites.js, routes.js, and
 * report.js, each with a comment saying it was lifted from the last) before
 * SNOW-752 needed it three times more on the account pages. It is one of the
 * shared row modules now, beside static/js/row_focus.js,
 * static/js/inline_rename.js and static/js/row_rename_commit.js.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. It never re-reads a list, never
 * dispatches an event and never touches the map. Which list to re-read,
 * from which URL, and which overlay to announce are the caller's business
 * and differ on all six surfaces; recognising the removal is the part that
 * does not.
 *
 * Usage — the selector names the LIST this caller owns:
 *
 *     window.pwaRowRemoved.watch('[data-routes-rows]', function () {
 *       loadRows();
 *       document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));
 *     });
 *
 * TWO CONDITIONS, NOT ONE. A request counts only when it comes from a
 * `[data-row-remove]` element (the delete form itself — the hook the three
 * `_*_row_actions.html` partials put there) AND that element is inside the
 * caller's list.
 *
 * The list alone is not enough, because a row is not only a delete. On
 * /account/favourites/ the row's disclosure chevron hx-gets the pin's
 * detail card from inside the very same list, so a watcher keyed on the
 * list would re-read it every time a user expanded a card — closing the
 * card they had just opened. The form alone is not enough either: the map
 * homepage carries three of these lists at once, and a pin deleted in the
 * favourites panel must not make the routes panel refetch.
 *
 * The listeners are registered once per watch() and are never removed:
 * these lists are re-rendered wholesale on every read, so a listener bound
 * to a specific container would have to be rebound on each swap. Delegation
 * from the document is what makes a caller's single call at boot enough.
 *
 * A re-read made with `htmx.ajax(verb, url, {target})` cannot loop: that
 * call's source element defaults to `document.body`, which is no delete
 * form, so it is never marked.
 */

(function rowRemovedInit() {
  'use strict';

  // The delete form's own hook, rendered by favourites/, routes/ and
  // observations/'s `_*_row_actions.html`. One name across all three,
  // because "this request removes a row" is one fact however the row is
  // spelled — the same reasoning behind `data-row-rename` and
  // `data-row-focus` on the shared row.
  const REMOVE_SELECTOR = '[data-row-remove]';

  // One mark name per watch(), so two lists on the same page cannot read
  // each other's. A counter rather than the selector itself: a selector is
  // not a valid property name, and two callers watching the same selector
  // for different reasons is a legitimate thing to do.
  let watchCount = 0;

  /**
   * Call `handler` after a successful HTMX request made from inside a list.
   *
   * @param {string} listSelector CSS selector for the list container whose
   *   removals this caller wants — the scoping half of the test.
   * @param {Function} handler Called with no arguments, once per successful
   *   removal. Never called for a failed one: a delete that did not land
   *   left the row where it was, and re-reading would say otherwise.
   * @returns {void}
   */
  function watch(listSelector, handler) {
    if (typeof listSelector !== 'string' || typeof handler !== 'function') return;

    watchCount += 1;
    const mark = 'snowdeskRowRemoved' + watchCount;

    document.addEventListener('htmx:beforeRequest', function (event) {
      const detail = event.detail || {};
      if (!detail.xhr) return;
      const elt = detail.elt;
      if (!elt || !elt.closest) return;
      detail.xhr[mark] = !!(
        elt.closest(REMOVE_SELECTOR) && elt.closest(listSelector)
      );
    });

    document.addEventListener('htmx:afterRequest', function (event) {
      const detail = event.detail || {};
      if (!detail.xhr || !detail.xhr[mark]) return;
      if (!detail.successful) return;
      handler();
    });
  }

  // Frozen, like every other window.pwa* bridge in static/js — the export is
  // a contract, not a namespace for callers to extend.
  window.pwaRowRemoved = Object.freeze({ watch: watch });
}());
