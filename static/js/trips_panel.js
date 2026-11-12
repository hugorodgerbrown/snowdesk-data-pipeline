// @ts-check
/*
 * static/js/trips_panel.js — the Saved → Trips sheet
 * (SNOW-1479).
 *
 * The fourth Saved sheet (public/partials/_trips_surface.html): the reader's
 * trips, organised or joined, upcoming soonest first and then past. It has
 * no roundel; static/js/saved_panel.js opens it from the Saved tabs through
 * the bridge this module publishes, window.pwaTripsSheet (SNOW-1481: on
 * every screen, not only in the installed app).
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
 *   - THE + IS A ROUTE PICKER (public/partials/_trip_route_picker.html). A
 *     trip is planned from one of the reader's own routes, so opening the
 *     menu reads routes:geojson (owner-scoped: a shared route the reader
 *     has not saved is not theirs, and a pending share's feature carries
 *     no uuid, so it is skipped) and lists each, in the endpoint's order —
 *     newest first, the routes panel's — as a link to
 *     trips:new?route=<uuid>, cloned from #trips-route-picker-item-template
 *     with route_rail_core.js's meta line (distance and ascent). No routes:
 *     one item, `data-saved-go="routes"`, which saved_panel.js turns into
 *     the Routes tab. A failed read says so in one line. Read on every
 *     open; a reply to an earlier open is dropped. The menu hangs down
 *     from a header trigger, so overflow_menu.js's placement on open still
 *     holds once the items arrive; the slot scrolls past a few routes.
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
  // Consts, so the checks above hold inside the closures below.
  const sheet = found;
  const tpl = /** @type {HTMLTemplateElement} */ (template);

  var FALLBACKS = {
    error: "Your trips couldn't be loaded — check your connection.",
    'rename-failed': "That name couldn't be saved. Try again.",
    'share-copied': 'Link copied.',
    'share-failed': "That couldn't be shared. Try again.",
    'picker-empty': 'Add a route first',
    'picker-error': "Your routes couldn't be loaded.",
    'picker-untitled': 'Untitled route',
    'meta-km': '%(km)skm',
    'meta-ascent': '%(km)skm · %(ascent)sm ↑',
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
    sheet.replaceChildren(tpl.content.cloneNode(true));
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

  // Bumped on every picker open, so a slow reply to an earlier one is
  // dropped rather than painted over a later one.
  let pickerRequest = 0;

  /**
   * One route's item in the picker, cloned from its template.
   *
   * @param {{uuid: string, name?: ?string, distance_m?: ?number,
   *   ascent_m?: ?number}} props The route's geojson properties.
   * @param {string} newUrl trips:new, to which `?route=<uuid>` is added.
   * @returns {HTMLElement|null}
   */
  function pickerItem(props, newUrl) {
    const itemTemplate = document.getElementById('trips-route-picker-item-template');
    if (!(itemTemplate instanceof HTMLTemplateElement)) return null;
    const fragment = /** @type {DocumentFragment} */ (itemTemplate.content.cloneNode(true));
    const link = /** @type {HTMLAnchorElement|null} */ (
      fragment.querySelector('[data-trip-route-picker-item]')
    );
    if (!link) return null;
    link.href = newUrl + '?route=' + encodeURIComponent(props.uuid);
    const name = link.querySelector('[data-trip-route-picker-name]');
    if (name) name.textContent = props.name || STRINGS['picker-untitled'];
    const meta = link.querySelector('[data-trip-route-picker-meta]');
    const core = self.pwaRouteRailCore;
    const line = core
      ? core.formatMetaLine({ distance_m: props.distance_m, ascent_m: props.ascent_m }, STRINGS)
      : '';
    if (meta) {
      if (line) meta.textContent = line;
      else meta.remove();
    }
    return link;
  }

  /**
   * The picker's one item with no routes: to the Routes tab.
   *
   * @returns {HTMLButtonElement}
   */
  function pickerEmpty() {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.setAttribute('data-saved-go', 'routes');
    button.className =
      'hover-affordance flex w-full items-start gap-3 px-3 py-2.5 text-left text-label font-semibold text-text-1';
    button.textContent = STRINGS['picker-empty'];
    return button;
  }

  /**
   * The picker's one line when the routes could not be read.
   *
   * @returns {HTMLParagraphElement}
   */
  function pickerError() {
    const p = document.createElement('p');
    p.className = 'px-3 py-2.5 text-sm text-text-2';
    p.textContent = STRINGS['picker-error'];
    return p;
  }

  /**
   * Read the reader's routes and fill the picker's slot.
   *
   * @param {HTMLElement} slot The `[data-trip-route-picker-items]` element.
   * @returns {Promise<void>}
   */
  function fillPicker(slot) {
    const url = slot.dataset.routesUrl || '';
    const newUrl = slot.dataset.tripNewUrl || '';
    pickerRequest += 1;
    const request = pickerRequest;
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) throw new Error('routes ' + response.status);
        return response.json();
      })
      .then(function (/** @type {{features?: Array<{properties?: Object}>}} */ data) {
        if (request !== pickerRequest) return;
        /** @type {HTMLElement[]} */
        const items = [];
        for (const feature of (data && data.features) || []) {
          const props = /** @type {{uuid?: string, pending?: boolean}} */ (
            (feature && feature.properties) || {}
          );
          // Owned routes only: a pending share has no uuid to plan from.
          if (!props.uuid || props.pending) continue;
          const item = pickerItem(
            /** @type {{uuid: string}} */ (props),
            newUrl,
          );
          if (item) items.push(item);
        }
        slot.replaceChildren(...(items.length ? items : [pickerEmpty()]));
      })
      .catch(function () {
        if (request !== pickerRequest) return;
        slot.replaceChildren(pickerError());
      });
  }

  /**
   * The picker's trigger, opening: read the routes into it.
   *
   * This listener is on the sheet, so it runs before overflow_menu.js's on
   * the document, while the wrapper still says whether the menu is open.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was the picker's trigger.
   */
  function handlePicker(event) {
    const target = /** @type {Element} */ (event.target);
    const trigger = target.closest('[data-overflow-trigger]');
    const wrapper = trigger ? trigger.closest('[data-overflow-menu]') : null;
    const slot = wrapper
      ? /** @type {HTMLElement|null} */ (wrapper.querySelector('[data-trip-route-picker-items]'))
      : null;
    if (!wrapper || !slot) return false;
    if (!wrapper.hasAttribute('data-overflow-open')) fillPicker(slot);
    return true;
  }

  sheet.addEventListener('click', function (event) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (handlePicker(event)) return;
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
