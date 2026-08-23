/*
 * static/js/account_favourites.js — the account page's favourites list
 * (SNOW-711).
 *
 * /account/ lazy-loads favourites:list into its "My favourites" section and
 * gets the same row the map panels render (favourites/partials/
 * _favourite.html) — a label, a pencil, a trash, and one thing the map row
 * has not: a trailing chevron that expands the pin's detail card under its
 * own row. This module is the two behaviours that row needs on this page.
 *
 * 1 — RENAME. The pencil turns the label into an inline edit
 * (window.pwaInlineRename) and window.pwaRowRenameCommit posts the
 * committed name — the same two modules the favourites and routes panels
 * use on the map. What is this page's own is what happens next: the
 * response IS this row, in this shape, so it is swapped straight back in.
 * The map panels re-read their whole list instead, because that response
 * carries a disclosure their rows do not have.
 *
 * 2 — EXPAND. The chevron's own hx-get does the fetching; HTMX owns that
 * request, which is what keeps static/js/favourites_offline.js's SNOW-418
 * write-through working (it listens for htmx:afterOnLoad, and a fetch from
 * here would have to reproduce the event to be seen). This module owns the
 * STATE: it flips aria-expanded, and on the closing click it empties the
 * panel and stops the event before HTMX's own listener can re-fetch a card
 * the user just asked to put away.
 *
 * That last part is why the collapse listener is bound in the CAPTURE
 * phase on the document: HTMX binds ITS listener on the element itself, so
 * a capture-phase ancestor listener runs first and stopPropagation() there
 * keeps the event from ever reaching it. In the bubble phase the request
 * would already be away.
 *
 * Nothing is bound to a row or a list directly — every listener is
 * delegated on the document, because the list arrives over HTMX after this
 * script has run and each rename replaces a row wholesale.
 *
 * ONLINE-ONLY, like every other surface's rename and delete. A failure
 * reveals the page's own hidden HTMX error banner
 * (accounts/partials/_htmx_error_banner.html) rather than writing a
 * message from here: that copy belongs in a template, where makemessages
 * can see it (docs/i18n.md).
 */

(function accountFavouritesInit() {
  'use strict';

  var LIST_SELECTOR = '[data-favourite-list]';
  var DISCLOSURE_SELECTOR = '[data-row-disclosure]';
  // The expand target inside a row. Found by attribute rather than by the
  // `favourite-panel-<uuid>` id the template builds, so this module needs
  // no opinion about how that id is spelled.
  var PANEL_SELECTOR = '[data-row-panel]';

  /**
   * The CSRF token for a plain fetch off this page.
   *
   * Read from the DOM at call time, not cached: the list is swapped in
   * after this module runs, and every row replaced on rename. Any row's
   * Remove form carries it, and they all carry the same one.
   *
   * @returns {string} The token, or '' when the list has not arrived.
   */
  function csrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  /**
   * Reveal the page's hidden HTMX failure banner.
   *
   * The same banner accounts/partials/_htmx_error_banner.html reveals for
   * a failed HTMX request — this page's rename is a plain fetch, so it
   * fires no HTMX event and has to say so itself.
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
   * The response is favourites/partials/_favourite.html — this row, with
   * the new name — so it is swapped in rather than the list re-read: a
   * refetch would throw away every expanded card on the page to update one
   * label.
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

    // SNOW-711: the panel lives INSIDE the row's own <li>, so this swap
    // would take an expanded card down with the row it replaces. Carry the
    // card across instead: a rename changes the pin's label, not whether
    // the user has it open. The fresh row always renders closed and empty,
    // so its `aria-expanded` has to be corrected to match what we moved.
    //
    // (The panel used to be the row's next SIBLING, which survived this
    // swap by accident of not being part of it. Nesting is what makes
    // Remove take the card with the header — see _ugc_panel_row.html — and
    // it is why the rename path now has to do this deliberately.)
    var panel = row.querySelector(PANEL_SELECTOR);
    var nextPanel = next.querySelector(PANEL_SELECTOR);
    if (panel && nextPanel && panel.children.length) {
      nextPanel.replaceChildren.apply(nextPanel, Array.prototype.slice.call(panel.children));
      var control = next.querySelector(DISCLOSURE_SELECTOR);
      if (control) control.setAttribute('aria-expanded', 'true');
    }

    row.replaceWith(next);
    // The new row's Remove form and chevron carry hx-* attributes that
    // nothing has looked at yet: HTMX only processes what it swapped in
    // itself, and this swap was ours.
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
      uuidAttribute: 'data-favourite-rename',
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

  /**
   * Handle a click on a row's disclosure chevron.
   *
   * Expanding is left to the chevron's own hx-get; this only records the
   * state. Collapsing is entirely this module's: empty the panel, close
   * the control, and stop the event here so HTMX never sees the click and
   * cannot re-fetch what the user just closed.
   *
   * @param {MouseEvent} event The click, in the capture phase.
   * @returns {void}
   */
  function handleDisclosureClick(event) {
    var target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    var control = target.closest(DISCLOSURE_SELECTOR);
    if (!control) return;

    var panel = document.getElementById(control.getAttribute('aria-controls'));
    if (!panel) return;

    if (control.getAttribute('aria-expanded') === 'true') {
      event.preventDefault();
      event.stopPropagation();
      panel.replaceChildren();
      control.setAttribute('aria-expanded', 'false');
      return;
    }
    control.setAttribute('aria-expanded', 'true');
  }

  document.addEventListener('click', handleRenameClick);
  document.addEventListener('click', handleDisclosureClick, true);
})();
