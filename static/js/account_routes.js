/*
 * static/js/account_routes.js — the account page's routes list (SNOW-713).
 *
 * /account/routes/ renders routes/partials/_route_list.html, whose rows are
 * the shared UGC row (includes/_ugc_panel_row.html) carrying a pencil and a
 * trash. The trash needs nothing from here — it is a plain HTMX form and
 * route_delete answers an empty 200 the row's own hx-swap turns into a
 * removal. The pencil does, and a pencil nothing listens to is a dead
 * control, which is worse than no pencil at all.
 *
 * So this module is one behaviour: rename. window.pwaInlineRename owns the
 * interaction (reveal the editor, Enter or blur commits, Escape cancels, an
 * emptied field leaves the title alone) and window.pwaRowRenameCommit owns
 * the POST — the same two modules the map's four panels use. What is this
 * page's own is what happens to the response: it IS this row, in this shape,
 * so it is swapped straight back in.
 *
 * That is the whole difference from the map panel. static/js/routes.js throws
 * the response away and re-reads its list, because it renders its rows into a
 * sheet body that is re-cloned on every open; here the row on the page is the
 * row the endpoint answered with, so a refetch would be a round-trip to
 * change one label.
 *
 * WHY THIS IS A SIBLING OF account_favourites.js AND NOT A SHARED MODULE.
 * The genuinely shared half is already extracted — row_rename_commit.js is
 * that extraction, made by SNOW-711 for exactly this reason. What is left
 * between the two account surfaces is a selector and an attribute name, plus
 * a large piece account_favourites.js has and this does not: a favourite row
 * carries a disclosure chevron, so that module also owns an expand/collapse
 * state machine and has to carry an open card across a rename swap. A route
 * has no detail page and therefore no disclosure, so none of that applies.
 * Merging them would mean a shared module where half the code runs for one
 * caller — the project's own "no abstractions until two callers need them"
 * rule, read the way round it is meant.
 *
 * Nothing is bound to a row directly: every listener is delegated on the
 * document, because each rename replaces a row wholesale.
 *
 * ONLINE-ONLY, like every other surface's rename and delete. Creates are
 * queued for offline replay (window.pwaMutationQueue); edits to an existing
 * row are not, and this module does not change that.
 *
 * No user-facing strings. A failure reveals the page's own hidden HTMX error
 * banner (accounts/partials/_htmx_error_banner.html) rather than writing a
 * message from here: that copy belongs in a template, where makemessages can
 * see it (docs/i18n.md).
 */

(function accountRoutesInit() {
  'use strict';

  var LIST_SELECTOR = '[data-route-list]';

  /**
   * The CSRF token for a plain fetch off this page.
   *
   * Read from the DOM at call time, not cached: every row is replaced on
   * rename. Any row's Remove form carries it, and they all carry the same
   * one.
   *
   * @returns {string} The token, or '' when no row has rendered.
   */
  function csrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  /**
   * Reveal the page's hidden HTMX failure banner.
   *
   * The same banner a failed Remove reveals through HTMX's own listener —
   * this page's rename is a plain fetch, so it fires no HTMX event and has
   * to say so itself.
   *
   * @returns {void}
   */
  function reportFailure() {
    var banner = document.getElementById('htmx-error-banner');
    if (banner) banner.classList.remove('hidden');
  }

  /**
   * Replace a row with the markup the rename endpoint answered with.
   *
   * The response is routes/partials/_route.html — this route, with its new
   * name — which since SNOW-711 is the row every surface renders, so it can
   * be swapped in as it stands.
   *
   * @param {HTMLElement} row The row that was renamed.
   * @param {string} html The endpoint's response body.
   * @returns {void}
   */
  function swapRow(row, html) {
    var holder = document.createElement('template');
    holder.innerHTML = html.trim();
    var next = holder.content.firstElementChild;
    if (!next) return;

    row.replaceWith(next);
    // The new row's Remove form carries hx-* attributes nothing has looked
    // at yet: HTMX only processes what it swapped in itself, and this swap
    // was ours. Without this the trash on a just-renamed row does nothing.
    if (typeof htmx !== 'undefined') htmx.process(next);
  }

  /**
   * Handle a click that starts and commits a row's inline rename.
   *
   * @param {MouseEvent} event The click.
   * @returns {boolean} Whether this click was a rename.
   */
  function handleRenameClick(event) {
    if (!window.pwaRowRenameCommit) return false;
    var target = /** @type {HTMLElement} */ (event.target);
    var list = target && target.closest ? target.closest(LIST_SELECTOR) : null;
    return window.pwaRowRenameCommit.handleClick(event, {
      uuidAttribute: 'data-route-rename',
      urlTemplate: list ? list.getAttribute('data-rename-url-template') : '',
      csrfToken: csrfToken,
      onCommitted: function (response, row) {
        return response.text().then(function (html) {
          swapRow(row, html);
        });
      },
      onFailed: reportFailure,
    });
  }

  document.addEventListener('click', handleRenameClick);
})();
