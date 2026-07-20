/*
 * static/js/favourites.js — SNOW-414 saved-pin add/rename/delete affordance.
 *
 * Loaded when the favourites flag is active (favourites_visible=True),
 * regardless of whether the user is authenticated. The floating
 * #favourite-add-btn carries the data attributes that drive branching:
 *
 *   data-favourites-eligible="true|false"        — True for authenticated users.
 *   data-signin-url="<url>"                      — Sign-in page for the anon CTA.
 *   data-favourite-create-url="<url>"            — HTMX create endpoint (eligible only).
 *   data-favourite-rename-url-template="<url>"   — __UUID__-templated rename endpoint.
 *   data-favourite-delete-url-template="<url>"   — __UUID__-templated delete endpoint.
 *
 * Flow when eligible (authenticated), using the shared place-picker
 * (SNOW-475 — static/js/place_picker.js) rather than a draggable marker,
 * which on touch screens is occluded by the dragging finger:
 *   1. Tap #favourite-add-btn — opens the sheet, shows the create form
 *      immediately (cloned from #favourite-create-template, which carries a
 *      real {% csrf_token %} rendered server-side — there is no per-request
 *      form-load endpoint here, unlike report_form_url), seeded from the
 *      map's current centre, and arms PlacePicker.activate() so the fixed
 *      centre pin appears and the form's coords track every pan.
 *   2. writeCreateCoords (PlacePicker's onChange) keeps the form's hidden
 *      lat/lon inputs in sync with the map centre.
 *   3. Save submits the form's own hx-post to favourite_create_url. On
 *      success (the swapped-in content carries [data-favourite-uuid]) we
 *      dispatch snowdesk:favourites-changed for map.js to refresh the
 *      overlay, emit telemetry, deactivate the place-picker, and close the
 *      sheet. On the favourites-cap response (_favourite_limit.html, no
 *      [data-favourite-uuid]) the sheet is left open showing the message.
 *
 * Pin-detail flow (existing favourite, tapped on the map):
 *   map.js dispatches snowdesk:favourite-selected {uuid, name} on a
 *   'favourites-pin' click. There is no "fetch one favourite" GET endpoint
 *   (SNOW-413 only shipped POST create/rename/delete), so the rename/delete
 *   row is reconstructed here via DOM APIs (never innerHTML of the
 *   user-supplied name — SNOW-414 must not `mark_safe` untrusted content)
 *   using the __UUID__-templated URLs and the CSRF token value already
 *   present (inert) inside #favourite-create-template. The reconstructed
 *   row deliberately mirrors favourites/partials/_favourite.html's shape
 *   (same element id, same hx-target) so once the *first* rename or delete
 *   round-trips, the server's own partial slots back into the same place
 *   and every subsequent interaction is handled by its own embedded
 *   hx-post/hx-target/hx-swap wiring with no further JS involved.
 *
 * Flow when not eligible (anonymous):
 *   Tap opens the sheet with a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url) — no map interaction is armed.
 */

(function () {
  'use strict';

  const btn = document.getElementById('favourite-add-btn');
  const sheet = document.getElementById('favourite-sheet');
  const createTemplate = document.getElementById('favourite-create-template');
  if (!btn || !sheet || !createTemplate) return;

  const CREATE_URL = btn.dataset.favouriteCreateUrl;
  const RENAME_URL_TEMPLATE = btn.dataset.favouriteRenameUrlTemplate;
  const DELETE_URL_TEMPLATE = btn.dataset.favouriteDeleteUrlTemplate;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.favouritesEligible === 'true';

  // ---------------------------------------------------------------------------
  // Module-level create-flow state.
  // ---------------------------------------------------------------------------

  /** True from the moment #favourite-add-btn is tapped until create succeeds
   * or the sheet is cancelled — distinguishes the create afterSwap branch
   * from a rename afterSwap (both responses carry [data-favourite-uuid]).
   * @type {boolean} */
  let creatingFavourite = false;

  /** Return the global MAP object if available, else null. Guards against
   * the case where map.js has not yet initialised.
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null; // eslint-disable-line no-undef
  }

  // ---------------------------------------------------------------------------
  // Toast helper — reuses the project-wide toast markup conventions
  // (mirrors report.js's showToast).
  // ---------------------------------------------------------------------------

  function showToast(message) {
    const existing = document.getElementById('favourite-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'favourite-toast';
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.className = [
      'flex', 'fixed', 'bottom-4', 'left-1/2', '-translate-x-1/2', 'z-50',
      'items-center', 'gap-3', 'max-w-md', 'rounded-full',
      'bg-status-warning-bg', 'text-status-warning-text',
      'px-4', 'py-2', 'text-sm', 'shadow-lg',
    ].join(' ');

    const span = document.createElement('span');
    span.textContent = message;
    toast.appendChild(span);

    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.setAttribute('aria-label', 'Dismiss');
    dismiss.className = 'text-status-warning-text opacity-70 hover:opacity-100 px-1 leading-none';
    dismiss.textContent = '×';
    dismiss.addEventListener('click', function () { toast.remove(); });
    toast.appendChild(dismiss);

    document.body.appendChild(toast);
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 6000);
  }

  // ---------------------------------------------------------------------------
  // Sheet open / close
  // ---------------------------------------------------------------------------

  function openSheet() {
    sheet.removeAttribute('hidden');
    sheet.focus();
  }

  function closeSheet() {
    window.PlacePicker?.deactivate();
    creatingFavourite = false;
    sheet.setAttribute('hidden', '');
    sheet.innerHTML = '';
  }

  /** Build a sheet-header row (title + persistent × close button), mirroring
   * templates/includes/_sheet_header.html for states built via DOM APIs
   * rather than server-rendered markup (pin-detail, anonymous sign-in CTA).
   * @param {string} titleText
   * @returns {HTMLDivElement}
   */
  function buildSheetHeader(titleText) {
    const header = document.createElement('div');
    header.className = 'flex items-center justify-between px-2 pt-1 pb-3';
    const title = document.createElement('span');
    title.className = 'text-sm font-semibold text-text-1';
    title.textContent = titleText;
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.setAttribute('data-action', 'close-favourite-sheet');
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.className = 'text-text-2 hover:text-text-1 text-lg leading-none px-1';
    closeBtn.textContent = '×';
    header.appendChild(title);
    header.appendChild(closeBtn);
    return header;
  }

  // Delegate close from Cancel / Close buttons inside the sheet — survives
  // every htmx swap since it's bound on document (mirrors report.js).
  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target && target.closest && target.closest('[data-action="close-favourite-sheet"]')) {
      closeSheet();
    }
  });

  // Esc dismisses the sheet, matching report.js.
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (sheet.hasAttribute('hidden')) return;
    closeSheet();
  });

  // Click-outside dismisses the sheet — but not while the user is
  // interacting with the map or place-picker to position a pin, and not
  // on the opening trigger itself (which has its own click handler).
  document.addEventListener('click', function (event) {
    if (sheet.hasAttribute('hidden')) return;
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('#favourite-sheet')) return;
    if (target.closest('#favourite-add-btn')) return;
    if (target.closest('#map')) return;
    if (target.closest('[data-place-picker]')) return;
    closeSheet();
  });

  // ---------------------------------------------------------------------------
  // Create flow — show the create form and arm the place-picker (SNOW-475).
  // ---------------------------------------------------------------------------

  /** Write lat/lon into the create form's hidden inputs.
   * @param {number} lat
   * @param {number} lon
   */
  function writeCreateCoords(lat, lon) {
    const form = document.getElementById('favourite-create-form');
    if (!form) return;
    const latInput = form.querySelector('input[name="lat"]');
    const lonInput = form.querySelector('input[name="lon"]');
    if (latInput) latInput.value = lat;
    if (lonInput) lonInput.value = lon;
  }

  /** Clone #favourite-create-template into the sheet and seed its coords.
   * @param {number} lat
   * @param {number} lon
   */
  function showCreateForm(lat, lon) {
    sheet.innerHTML = '';
    sheet.appendChild(createTemplate.content.cloneNode(true));
    writeCreateCoords(lat, lon);
    if (typeof htmx !== 'undefined') htmx.process(sheet); // eslint-disable-line no-undef
    const nameInput = document.getElementById('favourite-name-input');
    if (nameInput) nameInput.focus();
  }

  btn.addEventListener('click', function () {
    // Anonymous users: open the sheet with a sign-in CTA; no map interaction.
    if (!IS_ELIGIBLE) {
      openSheet();
      // Build the sign-in CTA with createElement (matching buildDetailRow)
      // rather than innerHTML string concatenation — the same DOM-not-markup
      // discipline the rest of this module uses for anything URL/name-bearing.
      sheet.replaceChildren();
      sheet.appendChild(buildSheetHeader('Favourite'));
      if (SIGNIN_URL) {
        const wrap = document.createElement('div');
        wrap.className = 'px-2 py-4';
        const p = document.createElement('p');
        p.className = 'text-sm text-text-2 mb-3';
        p.textContent = 'Sign in to save a favourite.';
        const a = document.createElement('a');
        a.setAttribute('href', SIGNIN_URL);
        a.className =
          'block w-full rounded-pill bg-status-info-bg text-status-info-text text-sm font-medium text-center py-2 px-4';
        a.textContent = 'Sign in';
        wrap.appendChild(p);
        wrap.appendChild(a);
        sheet.appendChild(wrap);
      } else {
        const p = document.createElement('p');
        p.className = 'px-2 py-4 text-sm text-text-2';
        p.textContent = 'Sign in to save a favourite.';
        sheet.appendChild(p);
      }
      return;
    }

    const map = getMap();
    if (!map) return;

    openSheet();
    creatingFavourite = true;

    // SNOW-475: show the create form immediately, seeded from the map's
    // current centre, then arm the shared place-picker so the fixed centre
    // pin appears and writeCreateCoords tracks every pan.
    const centre = map.getCenter();
    showCreateForm(centre.lat, centre.lng);
    window.PlacePicker?.activate({ onChange: writeCreateCoords });
  });

  // ---------------------------------------------------------------------------
  // Pin-detail flow — rename / delete an existing favourite.
  // ---------------------------------------------------------------------------

  // Escaping untrusted content (the user-supplied favourite name) is avoided
  // entirely below — every dynamic value is set via DOM properties (.value,
  // .textContent) rather than string-templated innerHTML. See buildDetailRow().

  /** Read the CSRF token value from the (inert) create-form template — reused
   * for the dynamically-built rename/delete forms since Django issues one
   * CSRF token per session, valid across every form on the page.
   * @returns {string}
   */
  function getCsrfToken() {
    const input = createTemplate.content.querySelector(
      'input[name="csrfmiddlewaretoken"]',
    );
    return input ? input.value : '';
  }

  /** Build a hidden CSRF input for a dynamically-constructed form.
   * @returns {HTMLInputElement}
   */
  function buildCsrfInput() {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrfmiddlewaretoken';
    input.value = getCsrfToken();
    return input;
  }

  /** Build the rename+delete row for an existing favourite, mirroring
   * favourites/partials/_favourite.html's shape/ids so the server's own
   * partial can slot back into the same #favourite-<uuid> element on the
   * first successful round-trip.
   * @param {string} favUuid
   * @param {string} name
   * @returns {HTMLDivElement}
   */
  function buildDetailRow(favUuid, name) {
    const row = document.createElement('div');
    row.id = 'favourite-' + favUuid;
    row.dataset.favouriteUuid = favUuid;
    row.className = 'flex items-center gap-2 rounded-tag border border-border bg-card px-3 py-2 text-sm';

    const renameForm = document.createElement('form');
    renameForm.setAttribute('hx-post', RENAME_URL_TEMPLATE.replace('__UUID__', favUuid));
    renameForm.setAttribute('hx-target', '#favourite-' + favUuid);
    renameForm.setAttribute('hx-swap', 'outerHTML');
    renameForm.setAttribute('hx-trigger', 'change');
    renameForm.className = 'flex-1';
    renameForm.appendChild(buildCsrfInput());

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.name = 'name';
    nameInput.value = name || '';
    nameInput.placeholder = 'Unnamed pin';
    nameInput.setAttribute('aria-label', 'Favourite name');
    nameInput.className = 'w-full bg-transparent text-text-1 placeholder:text-text-3 focus:outline-none';
    renameForm.appendChild(nameInput);

    const deleteForm = document.createElement('form');
    deleteForm.setAttribute('hx-post', DELETE_URL_TEMPLATE.replace('__UUID__', favUuid));
    deleteForm.setAttribute('hx-target', '#favourite-' + favUuid);
    deleteForm.setAttribute('hx-swap', 'outerHTML');
    deleteForm.appendChild(buildCsrfInput());

    const removeBtn = document.createElement('button');
    removeBtn.type = 'submit';
    removeBtn.className = 'shrink-0 text-xs font-medium text-text-3 underline hover:text-status-error-text';
    removeBtn.textContent = 'Remove';
    deleteForm.appendChild(removeBtn);

    row.appendChild(renameForm);
    row.appendChild(deleteForm);
    return row;
  }

  document.addEventListener('snowdesk:favourite-selected', function (event) {
    if (!RENAME_URL_TEMPLATE || !DELETE_URL_TEMPLATE) return;
    const detail = event.detail || {};
    const favUuid = detail.uuid;
    if (!favUuid) return;

    creatingFavourite = false;
    window.PlacePicker?.deactivate();

    sheet.innerHTML = '';

    sheet.appendChild(buildSheetHeader('Favourite'));
    sheet.appendChild(buildDetailRow(favUuid, detail.name || ''));
    if (typeof htmx !== 'undefined') htmx.process(sheet); // eslint-disable-line no-undef
    openSheet();
  });

  // ---------------------------------------------------------------------------
  // htmx response handling — create/rename success, delete success, cap error.
  //
  // Two-step: mark on htmx:beforeRequest whether the element making the
  // request lives inside #favourite-sheet (checked *before* any swap runs,
  // so elt.closest() sees a properly attached tree — outerHTML swaps like
  // the delete form's own row removal detach the element by the time a
  // later event fires), then act on htmx:afterRequest (last event in
  // htmx's lifecycle, so any swap has already completed) by inspecting
  // #favourite-sheet's current contents rather than the swap target —
  // the create form swaps into #favourite-sheet itself while the detail
  // row's rename/delete forms swap into #favourite-<uuid> (mirroring
  // favourites/partials/_favourite.html's own hx-target), so checking
  // "what's in the sheet now" covers both without caring which target
  // each form used.
  // ---------------------------------------------------------------------------

  let sheetRequestPending = false;

  document.addEventListener('htmx:beforeRequest', function (event) {
    const elt = event.detail && event.detail.elt;
    sheetRequestPending = !!(elt && elt.closest && elt.closest('#favourite-sheet'));
  });

  document.addEventListener('htmx:afterRequest', function (event) {
    if (!sheetRequestPending) return;
    sheetRequestPending = false;
    if (!event.detail.successful) return; // errors handled by responseError below

    const favouriteRow = sheet.querySelector('[data-favourite-uuid]');
    if (favouriteRow) {
      // Created (create form) or renamed (detail row) successfully.
      document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
      if (creatingFavourite) {
        window.pwaTelemetry?.emit('map.favourite.created', {});
        closeSheet();
      }
      return;
    }

    if (creatingFavourite) {
      // The favourites-cap message (_favourite_limit.html) swapped into the
      // sheet instead of a favourite row — leave it displayed.
      return;
    }
    // Detail-sheet path with no row left in the sheet => delete succeeded
    // (favourites:delete returns an empty 200 body, removing the row).
    window.pwaTelemetry?.emit('map.favourite.deleted', {});
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    closeSheet();
  });

  document.addEventListener('htmx:responseError', function (event) {
    const elt = event.detail && event.detail.elt;
    if (!elt || !elt.closest) return;
    if (!elt.closest('#favourite-sheet')) return;
    const message =
      (event.detail.xhr && event.detail.xhr.responseText) ||
      'Something went wrong — please try again.';
    showToast(message);
  });
}());
