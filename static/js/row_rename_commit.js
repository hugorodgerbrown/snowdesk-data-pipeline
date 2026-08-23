/*
 * static/js/row_rename_commit.js — committing a UGC row's inline rename to
 * the server (SNOW-711).
 *
 * The server half of static/js/inline_rename.js. That module owns the
 * INTERACTION — the pencil reveals the editor, Enter or blur commits,
 * Escape cancels, an emptied field leaves the title alone — and calls back
 * once with a name worth writing. This module owns what three surfaces then
 * do with that name, which had been the same twenty lines in each of them:
 *
 *   1. read the row's own uuid off the pencil (the delegated handler needs
 *      no closure over the rendered row);
 *   2. POST it to a `__UUID__`-templated rename URL as a form-encoded
 *      `name`, with `HX-Request` — every one of those endpoints is
 *      @require_htmx and this is a plain fetch — and the CSRF token the
 *      calling surface can reach;
 *   3. hand the response back to the caller, which is the ONLY part that
 *      differs.
 *
 * WHY THE CALLERS STILL DIFFER AT STEP 3. The map panels
 * (static/js/favourites.js, static/js/routes.js) throw the response away
 * and re-read their list: favourite_rename and route_rename answer with the
 * ACCOUNT page's row, which carries a disclosure the map row does not, so
 * it cannot be swapped into a map panel. The account page
 * (static/js/account_favourites.js) swaps that same response straight into
 * the row it came from, because there it is exactly the right shape. The
 * refetch on the map is therefore not something this extraction removes —
 * see routes.js's own note on why teaching those endpoints a `?variant=`
 * parameter is the wrong trade.
 *
 * ONLINE-ONLY, on every caller. Creates are queued for offline replay
 * (window.pwaMutationQueue); edits to an existing row are not, and this
 * module deliberately does not change that.
 *
 * No user-facing strings: a failed commit is reported by the caller, in
 * the idiom of its own surface (a map toast, the account page's HTMX error
 * banner). See docs/i18n.md for why a string built here could not be
 * translated.
 */

(function rowRenameCommitInit() {
  'use strict';

  /**
   * Handle a click that may start and commit a row's inline rename.
   *
   * Shaped to be called from a surface's own delegated click listener and
   * to answer the question that listener asks next: "was this a rename, or
   * should I keep testing?" A click that is not on a pencil returns false
   * and is left entirely alone.
   *
   * A misconfigured or unidentifiable row returns TRUE without writing: the
   * click WAS a rename, it simply cannot be served, and reporting it as
   * "not a rename" would send it on to the caller's add-CTA test.
   *
   * @param {MouseEvent} event The click.
   * @param {{
   *   uuidAttribute: string,
   *   urlTemplate: string,
   *   csrfToken: function(): string,
   *   onCommitted: function(Response, HTMLElement): void,
   *   onFailed: function(): void,
   * }} options
   *   `uuidAttribute` is the panel's own hook on the pencil
   *   (`data-favourite-rename`, `data-route-rename`) carrying the row's
   *   uuid; `urlTemplate` is the rename endpoint with `__UUID__` where the
   *   uuid goes; `csrfToken` is called at commit time, not at bind time,
   *   because the element carrying the token may have been re-rendered
   *   since; `onCommitted` receives the unread Response and the row it
   *   belongs to; `onFailed` covers a non-2xx, a dropped request, and a
   *   throw out of `onCommitted` alike — all three leave the row showing
   *   something the server did not agree to.
   * @returns {boolean} Whether this click was a rename.
   */
  function handleClick(event, options) {
    var row = window.pwaInlineRename && window.pwaInlineRename.rowFor(event.target);
    if (!row) return false;
    if (!options || !options.urlTemplate) return true;

    var button = row.querySelector('[' + options.uuidAttribute + ']');
    var uuid = button ? button.getAttribute(options.uuidAttribute) : '';
    if (!uuid) return true;

    window.pwaInlineRename.begin(row, function (name) {
      fetch(options.urlTemplate.replace('__UUID__', uuid), {
        method: 'POST',
        headers: {
          'HX-Request': 'true',
          'X-CSRFToken': options.csrfToken ? options.csrfToken() : '',
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ name: name }).toString(),
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error('rename failed');
          if (options.onCommitted) return options.onCommitted(resp, row);
          return undefined;
        })
        .catch(function () {
          if (options.onFailed) options.onFailed();
        });
    });
    return true;
  }

  window.pwaRowRenameCommit = Object.freeze({
    handleClick: handleClick,
  });
})();
