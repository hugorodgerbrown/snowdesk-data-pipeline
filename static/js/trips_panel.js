// @ts-check
/*
 * static/js/trips_panel.js — the installed phone app's Saved → Trips sheet
 * (SNOW-1479).
 *
 * The fourth Saved sheet (public/partials/_trips_surface.html): the reader's
 * trips, organised or joined, upcoming soonest first and then past. It has
 * no roundel; static/js/app_tabbar.js opens it from the Saved tabs through
 * the bridge this module publishes, window.pwaTripsSheet.
 *
 *   - THE LIST LOADS ON EVERY OPEN. The body is re-cloned from
 *     #trips-sheet-template each time, and trips:panel_list answers the rows
 *     over HTMX into [data-trips-rows], so a closed sheet keeps nothing. A
 *     failed load says so in its own line, never "no trips yet", which
 *     would be a wrong statement about the reader's own data. Online only,
 *     as Places and Today are.
 *   - AN ORGANISER'S ROW HAS A "…" MENU (trips/partials/_trip_row_menu_items.html):
 *       Share  — mints the trip's link (trips:share_create, which rotates a
 *                live one, as the trip list's own Share does) and hands it to
 *                the share sheet through window.pwaShare; only the
 *                clipboard and failure outcomes speak.
 *       Rename — the row's inline editor (inline_rename.js), committed to
 *                trips:rename by row_rename_commit.js; the list is re-read.
 *       Delete — an HTMX form with hx-confirm, posting with ?panel=1 so the
 *                answer is an empty 200; the row swaps out and
 *                row_removed.js tells this module to re-read the list.
 *     A participant's row has no menu: its name links to the trip.
 *   - Every listener is delegated on the sheet, because its body is
 *     replaced on every open.
 *
 * Rendered only for a signed-in reader, so this module returns at once for
 * a visitor.
 */
(function tripsPanelInit() {
  'use strict';

  const found = document.getElementById('trips-sheet');
  const template = document.getElementById('trips-sheet-template');
  if (!found || !(template instanceof HTMLTemplateElement)) return;
  if (!window.MapSheet) return;
  // A const, so the null check above holds inside the closures below.
  const sheet = found;

  var FALLBACKS = {
    error: "Your trips couldn't be loaded — check your connection.",
    'rename-failed': "That name couldn't be saved. Try again.",
    'share-copied': 'Link copied.',
    'share-failed': "That couldn't be shared. Try again.",
  };
  const STRINGS = window.pwaStrings
    ? window.pwaStrings.read('trips-strings-template', FALLBACKS)
    : FALLBACKS;

  // The uuid the rows container's URL templates are reversed with: the
  // endpoints are built at click time from the uuid on the control.
  const DUMMY_UUID = '00000000-0000-0000-0000-000000000000';

  const controller = window.MapSheet.attach(sheet, {
    onTeardown: function () {
      sheet.replaceChildren();
    },
  });

  /** @returns {HTMLElement|null} The rows container now in the sheet. */
  function rowsEl() {
    return sheet.querySelector('[data-trips-rows]');
  }

  /**
   * An endpoint for one trip, from a URL template on the rows container.
   *
   * @param {string} attribute The container's data attribute naming it.
   * @param {string} uuid The trip's uuid, or "__UUID__" for a template.
   * @returns {string}
   */
  function urlFor(attribute, uuid) {
    const rows = rowsEl();
    const template = rows ? rows.getAttribute(attribute) || '' : '';
    return template ? template.replace(DUMMY_UUID, uuid) : '';
  }

  /** @returns {string} The page's CSRF token, or "". */
  function csrfToken() {
    const input = /** @type {HTMLInputElement|null} */ (
      document.querySelector('input[name="csrfmiddlewaretoken"]')
    );
    return input ? input.value : '';
  }

  /** @param {string} message */
  function toast(message) {
    if (window.MapSheet && typeof window.MapSheet.toast === 'function') {
      window.MapSheet.toast(message);
    }
  }

  /** (Re)load the rows from trips:panel_list. */
  function loadRows() {
    const rows = rowsEl();
    const url = rows ? rows.dataset.tripsUrl || '' : '';
    if (!rows || !url || typeof htmx === 'undefined') return;
    htmx.ajax('GET', url, { target: rows, swap: 'innerHTML' });
  }

  /** Fill the sheet from its template and load the trips. */
  function render() {
    sheet.replaceChildren(template.content.cloneNode(true));
    loadRows();
  }

  /** Open the sheet on the reader's trips. */
  function open() {
    controller.open();
    render();
  }

  /** Close the sheet. */
  function close() {
    controller.close();
  }

  /**
   * A row's Share: mint the link, then hand it to the platform.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a share.
   */
  function handleShare(event) {
    const target = /** @type {Element} */ (event.target);
    const control = target.closest('[data-trip-panel-share]');
    if (!control) return false;
    const uuid = control.getAttribute('data-trip-panel-share') || '';
    const url = urlFor('data-trips-share-url-template', uuid);
    const share = window.pwaShare;
    if (!uuid || !url || !share) return true;
    share
      .createShare(url, csrfToken())
      .then(function (/** @type {string} */ link) {
        return share.shareOrCopy(link);
      })
      .then(function (/** @type {string} */ outcome) {
        if (outcome === 'copied') toast(STRINGS['share-copied']);
        else if (outcome === 'failed') toast(STRINGS['share-failed']);
      })
      .catch(function () {
        toast(STRINGS['share-failed']);
      });
    return true;
  }

  /**
   * A row's Rename: the inline editor, committed to trips:rename.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a rename.
   */
  function handleRename(event) {
    if (!window.pwaRowRenameCommit) return false;
    return window.pwaRowRenameCommit.handleClick(event, {
      uuidAttribute: 'data-trip-rename',
      urlTemplate: urlFor('data-trips-rename-url-template', '__UUID__'),
      csrfToken: csrfToken,
      onCommitted: loadRows,
      onFailed: function () {
        toast(STRINGS['rename-failed']);
      },
    });
  }

  sheet.addEventListener('click', function (event) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (handleShare(event)) return;
    handleRename(event);
  });

  // A failed load says so, rather than leaving the loading line up.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, function (event) {
      const rows = rowsEl();
      const detail = /** @type {CustomEvent} */ (event).detail;
      if (!rows || !detail || detail.target !== rows) return;
      const p = document.createElement('p');
      p.className = 'text-sm text-text-2';
      p.textContent = STRINGS.error;
      rows.replaceChildren(p);
    });
  }

  // A deleted trip's row has swapped out; re-read the list, whose empty
  // state only a fresh response carries.
  window.pwaRowRemoved?.watch('[data-trips-rows]', loadRows);

  window.pwaTripsSheet = Object.freeze({
    open: open,
    close: close,
    isOpen: controller.isOpen,
  });
}());
