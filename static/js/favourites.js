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
 *   3. Save is intercepted (SNOW-479) and routed through
 *      window.pwaMutationQueue.enqueue() instead of htmx — so an offline tap
 *      is captured immediately, persisted to IndexedDB, and replayed with a
 *      stable Idempotency-Key on reconnect (mirrors static/js/report.js). On
 *      enqueue we dispatch snowdesk:favourite-pending for map.js to draw an
 *      optimistic "pending" pin, emit telemetry, show the optimistic
 *      confirmation (with a "will sync" line when offline), and deactivate the
 *      place-picker. The pending pin becomes the real server pin when the
 *      queue drains and re-dispatches snowdesk:favourites-changed; a permanent
 *      failure on replay (e.g. the 409 at the favourites cap) fires the
 *      queue's own toast + nav badge and drops the pending pin via the
 *      pwa:mutation-failed-permanent listener below.
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
  // Module-level helpers.
  // ---------------------------------------------------------------------------

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
    // i18n: hardcoded English pre-launch; mirrors _sheet_header.html.
    title.textContent = titleText;
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.setAttribute('data-action', 'dismiss');
    // i18n: hardcoded English pre-launch; mirrors _sheet_header.html.
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.className = 'text-text-2 hover:text-text-1 text-lg leading-none px-1';
    closeBtn.textContent = '×';
    header.appendChild(title);
    header.appendChild(closeBtn);
    return header;
  }

  // SNOW-486: #favourite-sheet is a [data-overlay] element (see
  // includes/_overlay_sheet.html), so a click on any
  // [data-action="dismiss"] inside it — the Cancel button, the header ×,
  // the confirmation's Close button — is already caught by
  // static/js/overlays.js's shared delegated handler, which hides the
  // sheet (the attribute idiom it already used) and dispatches
  // overlay:dismissed. This listens for that event to run the teardown
  // the shared handler doesn't know about: deactivating the place-picker
  // and clearing the sheet's content so the next open starts fresh.
  document.addEventListener('overlay:dismissed', function (event) {
    const el = event.detail && event.detail.overlay;
    if (el !== sheet) return;
    window.PlacePicker?.deactivate();
    sheet.innerHTML = '';
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

    window.PlacePicker?.deactivate();

    sheet.innerHTML = '';

    sheet.appendChild(buildSheetHeader('Favourite'));
    sheet.appendChild(buildDetailRow(favUuid, detail.name || ''));
    if (typeof htmx !== 'undefined') htmx.process(sheet); // eslint-disable-line no-undef
    openSheet();
  });

  // ---------------------------------------------------------------------------
  // htmx response handling — rename success, delete success.
  //
  // Create is no longer htmx-driven: SNOW-479 routes it through the client
  // mutation queue (see the #favourite-create-form submit interceptor below).
  // Only the pin-detail rename/delete forms built by buildDetailRow() still use
  // hx-post. Two-step: mark on htmx:beforeRequest whether the element making the
  // request lives inside #favourite-sheet (checked *before* any swap runs, so
  // elt.closest() sees a properly attached tree — outerHTML swaps like the
  // delete form's own row removal detach the element by the time a later event
  // fires), then act on htmx:afterRequest (last event in htmx's lifecycle, so
  // any swap has already completed) by inspecting #favourite-sheet's current
  // contents. A rename swaps its _favourite.html (carrying [data-favourite-uuid])
  // back into #favourite-<uuid> inside the sheet; a delete empties the row.
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
      // Renamed successfully (the detail row's _favourite.html swapped back in).
      document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
      return;
    }
    // No row left in the sheet => delete succeeded (favourites:delete returns
    // an empty 200 body, removing the row).
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

  // ---------------------------------------------------------------------------
  // Create submit (SNOW-479) — routed through window.pwaMutationQueue instead
  // of htmx, so an offline "Save" tap is captured immediately, persisted to
  // IndexedDB, and replayed with a stable Idempotency-Key on reconnect. Mirrors
  // static/js/report.js's report-form submit. Delegated from document so it
  // survives the sheet being re-rendered on every open.
  // ---------------------------------------------------------------------------

  document.addEventListener('submit', function (event) {
    if (!event.target || event.target.id !== 'favourite-create-form') return;
    // preventDefault unconditionally — the endpoint is @require_htmx, so a
    // native form POST would 400; there is no meaningful no-JS fallback here.
    event.preventDefault();
    if (!IS_ELIGIBLE || !CREATE_URL) return;

    const form = /** @type {HTMLFormElement} */ (event.target);

    // enqueue() is defensively non-fatal: when IndexedDB is missing or in the
    // terminal Reset-Required state it silently drops the operation and still
    // resolves, so detect those states up front and surface a toast rather than
    // showing a success confirmation for a pin that was never queued.
    const dbUnavailable =
      typeof window.pwaDb !== 'object' ||
      (typeof window.pwaDb.isResetRequired === 'function' &&
        window.pwaDb.isResetRequired());
    if (!window.pwaMutationQueue || dbUnavailable) {
      showToast('Could not save this pin on this device — please try again.');
      return;
    }

    // Every field (csrfmiddlewaretoken, lat, lon, name) is a plain input that
    // FormData captures — there is no submit-button value to patch in (unlike
    // report.js's problem buttons). CSRF rides in the body plus the same-origin
    // session cookie (fetch defaults to credentials: 'same-origin').
    const body = new URLSearchParams(new FormData(form)).toString();

    window.pwaMutationQueue
      .enqueue({
        method: 'POST',
        url: CREATE_URL,
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          // favourite_create is @require_htmx; the replay is a plain fetch.
          'HX-Request': 'true',
        },
        body: body,
      })
      .catch(function () {});

    // Optimistic pin — map.js draws a synthetic "pending" marker at the saved
    // coordinate so the save is visible immediately, offline or on. It is
    // replaced by the authoritative server pin once the queue drains and
    // re-dispatches snowdesk:favourites-changed (mutation_queue.js).
    const latInput = form.querySelector('input[name="lat"]');
    const lonInput = form.querySelector('input[name="lon"]');
    const nameInput = form.querySelector('input[name="name"]');
    const lat = latInput ? parseFloat(latInput.value) : NaN;
    const lon = lonInput ? parseFloat(lonInput.value) : NaN;
    if (!isNaN(lat) && !isNaN(lon)) {
      document.dispatchEvent(
        new CustomEvent('snowdesk:favourite-pending', {
          detail: { lat: lat, lon: lon, name: nameInput ? nameInput.value : '' },
        }),
      );
    }

    window.pwaTelemetry?.emit('map.favourite.created', {
      offline: !navigator.onLine,
    });

    // Optimistic confirmation — clone the template embedded in the surface
    // partial rather than waiting for the queued mutation to replay.
    const template = document.getElementById('favourite-confirmation-template');
    if (template) {
      sheet.innerHTML = '';
      sheet.appendChild(template.content.cloneNode(true));
      if (!navigator.onLine) {
        const pending = sheet.querySelector('[data-favourite-pending]');
        if (pending) pending.removeAttribute('hidden');
      }
    }

    window.PlacePicker?.deactivate();
  });

  // ---------------------------------------------------------------------------
  // Permanent-failure cleanup (SNOW-479) — if a queued favourite-create is
  // permanently rejected on replay (notably the 409 the server returns once the
  // user is at their favourites cap), the optimistic pending pin must not
  // linger. A permanent 4xx means the server replied, so we are online: re-
  // dispatching snowdesk:favourites-changed makes map.js refetch the
  // authoritative collection, which drops the synthetic pending feature. The
  // queue's own toast + nav badge already tell the user the save failed.
  // ---------------------------------------------------------------------------

  document.addEventListener('pwa:mutation-failed-permanent', function (event) {
    const url = (event.detail && event.detail.url) || '';
    if (CREATE_URL && url.indexOf(CREATE_URL) !== -1) {
      document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    }
  });
}());
