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
 * SNOW-658: the roundel opens a PANEL, not the create form. The panel
 * (#favourite-list-template, in _favourites_surface.html) lists the user's
 * own pins — loaded over HTMX from favourites:list at ?variant=map, whose
 * row (favourites/partials/_favourite_row_map.html) is the shared UGC row:
 * a label, a muted meta line, and two icon controls — a pencil and a
 * trash. Remove is that row's own HTMX form and needs nothing here;
 * Rename — the pencil, and only the pencil — puts the row's own label
 * into an inline edit (static/js/inline_rename.js, shared with the
 * downloads panel) and posts the committed name from below. The panel
 * offers [data-panel-add] to place another, and carries the "Display on
 * the map" switch that used to be a row in the layers menu (it drives
 * window.pwaFavouritesOverlay in map.js). This
 * follows SNOW-634's downloads pattern: user-generated data gets its own
 * roundel, its own panel, and its panel owns the overlay switch.
 *
 * Flow when eligible (authenticated), using the shared place-picker
 * (SNOW-475 — static/js/place_picker.js) rather than a draggable marker,
 * which on touch screens is occluded by the dragging finger:
 *   1. Tap #favourite-add-btn — opens the sheet on the list panel; tap
 *      [data-panel-add] inside it to show the create form (cloned from
 *      #favourite-create-template, which carries a real {% csrf_token %}
 *      rendered server-side — there is no per-request form-load endpoint
 *      here, unlike report_form_url), seeded from the map's current centre,
 *      and arm PlacePicker.activate() so the fixed centre pin appears and
 *      the form's coords track every pan.
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
 *   Tap opens the panel with a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url) in place of the list and the add CTA —
 *   no map interaction is armed. The overlay switch stays: it is a view
 *   control for a layer that simply has nothing in it yet, and a control
 *   that vanishes reads as a bug (SNOW-658).
 */

(function () {
  'use strict';

  const btn = document.getElementById('favourite-add-btn');
  const sheet = document.getElementById('favourite-sheet');
  const createTemplate = document.getElementById('favourite-create-template');
  if (!btn || !sheet || !createTemplate) return;
  // SNOW-658: the panel body. Optional on purpose — a surface that renders
  // the create template but not this one still gets the create flow.
  const listTemplate = document.getElementById('favourite-list-template');

  // SNOW-620: server-translated copy, read back from the template
  // _favourites_surface.html renders. This module builds its whole sheet in
  // JS, so these strings never passed through a template and makemessages
  // could not see them. The literals are the English fallback — see
  // static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('favourites-strings-template', {
    // SNOW-658: 'close' and 'sheet-title' went with buildSheetHeader — every
    // state this module renders now clones a template that includes the real
    // includes/_sheet_header.html.
    'signin-prompt': 'Sign in to save a favourite.',
    'signin-cta': 'Sign in',
    'unnamed-pin': 'Unnamed pin',
    'name-label': 'Favourite name',
    remove: 'Remove',
    // SNOW-658: the map row's Rename is an inline edit on the row's own
    // label now, so the prompt's copy is gone — the editor's aria-label is
    // rendered by the row template, where makemessages can see it. Only
    // the failure line is still JS-rendered copy.
    'rename-failed': "That name couldn't be saved. Try again.",
    'list-failed': "Your favourites couldn't be loaded — check your connection.",
  });

  const CREATE_URL = btn.dataset.favouriteCreateUrl;
  const LIST_URL = btn.dataset.favouriteListUrl;
  const RENAME_URL_TEMPLATE = btn.dataset.favouriteRenameUrlTemplate;
  const DELETE_URL_TEMPLATE = btn.dataset.favouriteDeleteUrlTemplate;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.favouritesEligible === 'true';

  // SNOW-499: resort-popup star create URL — read from #map's own dataset
  // (not #favourite-add-btn's) since it is emitted alongside the other
  // #map data-*-url attributes and gated the same way
  // (favourites_eligible), independent of whether #map exists at all.
  const mapEl = document.getElementById('map');
  const RESORT_CREATE_URL = mapEl ? mapEl.dataset.resortCreateUrl : undefined;

  // ---------------------------------------------------------------------------
  // Module-level helpers.
  // ---------------------------------------------------------------------------

  /** Return the map if it is up, else null.
   *
   * SNOW-610: reads window.snowdeskMapState — map.js's one named channel to
   * its shared state — rather than the bare ``MAP`` identifier. Both work
   * (a top-level ``let`` in a classic script is visible by bare name), but
   * only one is greppable, and the near-identical ``window.MAP`` spelling
   * silently yields undefined.
   *
   * Still guarded: map.js may not have run, and ``map`` is null until the
   * style loads.
   *
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return window.snowdeskMapState ? window.snowdeskMapState.map : null;
  }

  // ---------------------------------------------------------------------------
  // Sheet controller + toast — SNOW-608's shared static/js/map_sheet.js, which
  // owns the open/focus cycle, the three dismissal routes (Escape,
  // click-outside, overlays.js's [data-action="dismiss"]) and their common
  // teardown. report.js attaches to its own sheet the same way.
  // ---------------------------------------------------------------------------

  // SNOW-658: the two hand-wired exclusivity pairs this attach used to carry
  // — an ``onBeforeOpen`` dispatching snowdesk:favourite-detail-close, and a
  // snowdesk:map-detail-opening listener closing this sheet — are gone. Both
  // said "this sheet and the anchored detail popup are mutually exclusive",
  // one direction each, and neither said anything about the layers menu, the
  // downloads sheet or the report sheet. MapSheet.attach now registers every
  // sheet with window.pwaMapOverlays (static/js/map_overlay_exclusivity.js),
  // which closes whatever else is open whichever surface opens.
  const controller = window.MapSheet.attach(sheet, {
    triggerSelector: '#favourite-add-btn',
  });
  const closeSheet = controller.close;
  const showToast = window.MapSheet.toast;

  // SNOW-658: ``buildSheetHeader`` lived here — a DOM-API reimplementation of
  // templates/includes/_sheet_header.html for the one state that had no
  // server-rendered markup, the anonymous sign-in CTA. That state is now
  // rendered inside #favourite-list-template, which includes the real partial,
  // so the copy has no callers and the two can no longer drift.

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
    if (typeof htmx !== 'undefined') htmx.process(sheet);
    // SNOW-538: the name field is deliberately NOT focused. Focusing it
    // raised the on-screen keyboard the instant the sheet opened, before
    // the user had panned the map to place the pin — half the map gone,
    // and the sheet shoved up over the pin, for an optional field most
    // pins never use. openSheet() has already moved focus to the sheet
    // itself, so the form is still reachable from the keyboard.
  }

  /** Build the anonymous sign-in CTA — a prompt plus a link to the sign-in
   * page. Built with createElement (matching buildDetailRow) rather than
   * innerHTML string concatenation: the same DOM-not-markup discipline the
   * rest of this module uses for anything URL/name-bearing.
   * @returns {HTMLDivElement}
   */
  function buildSigninCta() {
    const wrap = document.createElement('div');
    const p = document.createElement('p');
    p.className = 'text-sm text-text-2 mb-3';
    p.textContent = STRINGS['signin-prompt'];
    wrap.appendChild(p);
    if (SIGNIN_URL) {
      const a = document.createElement('a');
      a.setAttribute('href', SIGNIN_URL);
      a.className =
        'block w-full rounded-pill bg-status-info-bg text-status-info-text text-sm font-medium text-center py-2 px-4';
      a.textContent = STRINGS['signin-cta'];
      wrap.appendChild(a);
    }
    return wrap;
  }

  // ---------------------------------------------------------------------------
  // The panel (SNOW-658) — the sheet's default body: the user's own pins, a
  // CTA to add another, and the map-overlay switch that used to be a layers-
  // menu row.
  //
  // Re-cloned from the template on every open rather than updated in place,
  // mirroring map_downloads_manager.js's render(): a stale row can then never
  // survive a re-open. That is exactly why every listener below is delegated
  // on the SHEET — a per-element listener would have to be rebound each time.
  // ---------------------------------------------------------------------------

  /** Clone #favourite-list-template into the sheet and populate it.
   *
   * Eligible: the rows load over HTMX from favourites:list. Anonymous: the
   * rows and the add CTA are removed and a sign-in CTA takes their place —
   * but the overlay switch stays, because it is a view control for the map
   * behind the sheet, not a row in a list the visitor doesn't have.
   *
   * @returns {boolean} Whether the panel was rendered — false when the
   *   surface carries no list template, so the caller can fall back.
   */
  function showListPanel() {
    if (!listTemplate) return false;
    sheet.replaceChildren();
    sheet.appendChild(listTemplate.content.cloneNode(true));

    // Reflect the overlay's REAL state rather than a flag of this module's
    // own, the way map_downloads_manager.js's render() reads isVisible().
    const toggle = sheet.querySelector('#map-favourites-overlay-toggle');
    if (toggle) toggle.checked = !!window.pwaFavouritesOverlay?.isVisible?.();

    if (!IS_ELIGIBLE) {
      const addButton = sheet.querySelector('[data-panel-add]');
      if (addButton) addButton.remove();
      const rows = sheet.querySelector('[data-favourites-rows]');
      if (rows) rows.replaceChildren(buildSigninCta());
      return true;
    }
    loadRows();
    return true;
  }

  /** (Re)load the panel's rows from favourites:list over HTMX.
   *
   * The URL is used VERBATIM — the server puts ``?variant=map`` on it so
   * the sheet gets the lean row template (favourites/partials/
   * _favourite_list_map.html), and rebuilding the path here would silently
   * drop it and render the manage page's markup instead.
   *
   * Called on open, and again after a rename lands — the same
   * re-read-the-truth move map_downloads_manager.js's rename makes when it
   * calls render(), rather than patching the row in place from what we
   * think we just wrote.
   *
   * @returns {void}
   */
  function loadRows() {
    const rows = sheet.querySelector('[data-favourites-rows]');
    if (!rows || !LIST_URL || typeof htmx === 'undefined') return;
    htmx.ajax('GET', LIST_URL, { target: rows, swap: 'innerHTML' });
  }

  // SNOW-658: the roundel TOGGLES, matching the layers pill and the downloads
  // roundel — a second tap on the control that opened the panel closes it.
  // Bound on the button, so it runs before MapSheet's own document-level
  // click-outside handler, which then sees a closed sheet and does nothing.
  btn.addEventListener('click', function () {
    if (controller.isOpen()) {
      closeSheet();
      return;
    }
    controller.open();
    if (showListPanel()) return;
    // No list template on this surface. The create flow is still reachable,
    // and a visitor still gets the sign-in CTA — an empty sheet would be a
    // dead end with no explanation.
    if (IS_ELIGIBLE) startCreateFlow();
    else sheet.replaceChildren(buildSigninCta());
  });

  /** Show the create form seeded from the map centre and arm the place-picker.
   * @returns {void}
   */
  function startCreateFlow() {
    const map = getMap();
    if (!map) return;
    // SNOW-475: show the create form immediately, seeded from the map's
    // current centre, then arm the shared place-picker so the fixed centre
    // pin appears and writeCreateCoords tracks every pan.
    const centre = map.getCenter();
    showCreateForm(centre.lat, centre.lng);
    // occludedBy: the sheet is measured so the pin is lifted clear of it
    // (SNOW-538) — activate() re-seeds the coords from the pin's real
    // position, correcting the map-centre seed above.
    window.PlacePicker?.activate({
      onChange: writeCreateCoords,
      occludedBy: sheet,
    });
  }

  // SNOW-658: "Add a favourite" — what the roundel itself used to do.
  // Delegated on the sheet so it survives the body being re-cloned.
  //
  // stopPropagation is load-bearing, not caution. MapSheet's click-outside
  // dismissal asks ``sheet.contains(event.target)`` from a DOCUMENT listener,
  // which runs after this one — and by then this handler has replaced the
  // sheet's body, so the button that was clicked is detached and no longer
  // "inside" anything. The sheet would close itself the instant the create
  // form appeared, with nothing on screen to explain it.
  //
  // The trade-off: this hides the click from EVERY document-level listener,
  // not just that one (map_basemap_picker.js's outside-click close,
  // map_legend.js, overlays.js's [data-action="dismiss"]).
  // None of them needs it today — each of those surfaces has already closed by
  // the time this sheet is open — but a future document-level listener that
  // must see clicks inside this sheet would fail silently here.
  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (handleRenameClick(event)) return;
    if (!target.closest('[data-panel-add]')) return;
    event.stopPropagation();
    if (!IS_ELIGIBLE) return;
    startCreateFlow();
  });

  /** Handle a click that starts a row's inline rename (SNOW-658).
   *
   * Hugo's design: "Where a row can be renamed, that happens in place on
   * the label." The row's pencil opens the editor already sitting hidden
   * in the row's own markup; window.pwaInlineRename owns the
   * focus/keyboard/blur state machine — shared with the downloads panel,
   * which renames the same way — and calls back exactly once, with a name
   * worth writing. Escape, an unchanged name and an emptied field never
   * reach here. The label was a second trigger for a day; Hugo's "we have
   * inline editing & the pencil - choose one" left the pencil.
   *
   * This replaces a window.prompt that lasted one day: it covered the row
   * being renamed, could not be styled, and read as browser chrome on a
   * touch device.
   *
   * NOT stopPropagation'd, unlike the add CTA above: this handler leaves
   * the sheet's body in place, so there is no reason to hide the click
   * from every other document-level listener.
   *
   * Online-only, matching the manage page's own rename (a plain HTMX post)
   * and this module's resort-unfavourite path — creates are queued for
   * offline replay, edits to existing rows are not.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a rename, so the caller
   *   stops rather than also testing the add CTA.
   */
  function handleRenameClick(event) {
    const row = window.pwaInlineRename?.rowFor(event.target);
    if (!row) return false;
    if (!RENAME_URL_TEMPLATE) return true;

    const button = row.querySelector('[data-favourite-rename]');
    const favUuid = button ? button.getAttribute('data-favourite-rename') : '';
    if (!favUuid) return true;

    window.pwaInlineRename.begin(row, function (name) {
      fetch(RENAME_URL_TEMPLATE.replace('__UUID__', favUuid), {
        method: 'POST',
        headers: {
          // favourite_rename is @require_htmx; this is a plain fetch.
          'HX-Request': 'true',
          'X-CSRFToken': getCsrfToken(),
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ name: name }).toString(),
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error('rename failed');
          // Re-read the list rather than patch the row: the response is
          // the MANAGE page's row partial, which is deliberately a
          // different shape from the map's, so it cannot be swapped in
          // here.
          loadRows();
          document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
        })
        .catch(function () {
          showToast(STRINGS['rename-failed']);
        });
    });
    return true;
  }

  // SNOW-658: the overlay switch drives window.pwaFavouritesOverlay directly —
  // show()/hide() are the only writers of that overlay's visibility, and
  // showListPanel() reads isVisible() back, so the two can never drift. No
  // re-render: nothing else in the panel depends on this state.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target || !target.matches) return;
    if (!target.matches('#map-favourites-overlay-toggle')) return;
    if (target.checked) window.pwaFavouritesOverlay?.show();
    else window.pwaFavouritesOverlay?.hide();
  });

  // SNOW-658: the list is fetched, and this panel opens offline while its
  // list does not load offline. Say so, rather than leaving the loading line
  // up forever — and never fall through to favourites:list's own empty state,
  // which would tell the user they have no pins when the request merely
  // failed. Both htmx failure events are covered: responseError is a non-2xx
  // reply, sendError is no reply at all (the offline case).
  //
  // This is not the only writer of that element: favourites_offline.js listens
  // for the same two events and repaints the CACHED favourite cards over this
  // line (SNOW-418). Cached data winning is the intended outcome — the line
  // below is the fallback for a user with nothing cached, where
  // favourites_offline.js returns early and leaves it standing. Two things
  // produce that order, and breaking either silently inverts it: that module
  // binds on document.body while this one binds on document (so it runs first
  // as the event bubbles), and it awaits IndexedDB before writing (so its
  // write lands after this synchronous one). Pinned by
  // tests/js/test_favourites_panel_failed_load.js.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, function (event) {
      const rows = sheet.querySelector('[data-favourites-rows]');
      if (!rows || !event.detail || event.detail.target !== rows) return;
      const p = document.createElement('p');
      p.className = 'text-sm text-text-2';
      p.textContent = STRINGS['list-failed'];
      rows.replaceChildren(p);
    });
  }

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
    nameInput.placeholder = STRINGS['unnamed-pin'];
    nameInput.setAttribute('aria-label', STRINGS['name-label']);
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
    removeBtn.textContent = STRINGS.remove;
    deleteForm.appendChild(removeBtn);

    row.appendChild(renameForm);
    row.appendChild(deleteForm);
    return row;
  }

  // SNOW-414 / SNOW-499: an existing favourite's rename/delete detail now
  // opens in a popup anchored to the pin (a fixed point → pinned overlay),
  // not the docked create sheet. map.js owns anchoring, so it passes an empty
  // ``detail.container`` ([data-favourite-detail]) for this module to fill —
  // keeping the CSRF/URL/DOM-not-innerHTML wiring here — then anchors the
  // filled container in the popup. The container id/attribute is what the
  // htmx rename/delete lifecycle below scopes to (it used to scope to the
  // sheet).
  document.addEventListener('snowdesk:favourite-selected', function (event) {
    if (!RENAME_URL_TEMPLATE || !DELETE_URL_TEMPLATE) return;
    const detail = event.detail || {};
    const favUuid = detail.uuid;
    const container = detail.container;
    if (!favUuid || !container) return;

    window.PlacePicker?.deactivate();
    // SNOW-658: this used to close the sheet itself, because the sheet and
    // the detail popup are mutually exclusive. map.js announces the popup's
    // own open to window.pwaMapOverlays, which closes this sheet along with
    // every other overlay — so the closing happens either way, and no longer
    // needs one surface to know about another.
    container.appendChild(buildDetailRow(favUuid, detail.name || ''));
    if (typeof htmx !== 'undefined') htmx.process(container);
  });

  // ---------------------------------------------------------------------------
  // htmx response handling — rename success, delete success.
  //
  // Create is no longer htmx-driven: SNOW-479 routes it through the client
  // mutation queue (see the #favourite-create-form submit interceptor below).
  // Only the pin-detail rename/delete forms built by buildDetailRow() still use
  // hx-post, and (SNOW-499) they live in the anchored detail popup's
  // [data-favourite-detail] container, not the create sheet. Two-step: mark on
  // htmx:beforeRequest whether the element making the request lives inside that
  // container (checked *before* any swap runs, so elt.closest() sees a properly
  // attached tree — outerHTML swaps like the delete form's own row removal
  // detach the element by the time a later event fires), then act on
  // htmx:afterRequest (last event in htmx's lifecycle, so any swap has
  // completed) by inspecting the container's current contents. A rename swaps
  // its _favourite.html (carrying [data-favourite-uuid]) back into
  // #favourite-<uuid> inside the container; a delete empties it, and we close
  // the popup.
  //
  // The mark is stamped on the request's own XMLHttpRequest, not on a
  // module-level variable: both listeners are document-level, so every HTMX
  // request on the page runs through them — report.js's form-load
  // htmx.ajax() shares the map homepage with this surface — and a shared
  // variable is rewritten by whichever request fires last, with no way to
  // correlate an afterRequest back to the beforeRequest that set it. htmx
  // carries the same xhr object on both events, so the mark rides the
  // request it describes.
  // ---------------------------------------------------------------------------

  const DETAIL_REQUEST_MARK = 'snowdeskFavouriteDetailRequest';
  // SNOW-658: the same mark idiom for a request made from a row INSIDE the
  // panel — today only its Remove form, which posts and swaps itself out.
  // Marked on beforeRequest for the same reason the detail one is: an
  // outerHTML swap detaches the element, so by afterRequest there is no
  // tree left to ask where it came from.
  const PANEL_ROW_REQUEST_MARK = 'snowdeskFavouritePanelRowRequest';

  document.addEventListener('htmx:beforeRequest', function (event) {
    const detail = event.detail || {};
    if (!detail.xhr) return;
    const elt = detail.elt;
    detail.xhr[DETAIL_REQUEST_MARK] = !!(
      elt && elt.closest && elt.closest('[data-favourite-detail]')
    );
    detail.xhr[PANEL_ROW_REQUEST_MARK] = !!(
      elt && elt.closest && elt.closest('[data-favourites-rows]')
    );
  });

  // A row removed from the panel is a favourite that no longer exists, so the
  // map's own pins have to be refetched — nothing else does it. Deleting from
  // the pin popup already dispatched this (above); deleting from the panel
  // silently did not, and the pin stayed on the map until a reload.
  document.addEventListener('htmx:afterRequest', function (event) {
    const xhr = event.detail && event.detail.xhr;
    if (!xhr || !xhr[PANEL_ROW_REQUEST_MARK]) return;
    if (!event.detail.successful) return;
    window.pwaTelemetry?.emit('map.favourite.deleted', {});
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
  });

  document.addEventListener('htmx:afterRequest', function (event) {
    const xhr = event.detail && event.detail.xhr;
    if (!xhr || !xhr[DETAIL_REQUEST_MARK]) return;
    if (!event.detail.successful) return; // errors handled by responseError below

    const container = document.querySelector('[data-favourite-detail]');
    if (container && container.querySelector('[data-favourite-uuid]')) {
      // Renamed successfully (the detail row's _favourite.html swapped back in).
      document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
      return;
    }
    // No row left in the container => delete succeeded (favourites:delete
    // returns an empty 200 body, removing the row). Close the popup.
    window.pwaTelemetry?.emit('map.favourite.deleted', {});
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    document.dispatchEvent(new CustomEvent('snowdesk:favourite-detail-close'));
  });

  document.addEventListener('htmx:responseError', function (event) {
    const elt = event.detail && event.detail.elt;
    if (!elt || !elt.closest) return;
    if (!elt.closest('[data-favourite-detail]')) return;
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

    // enqueue() is defensively non-fatal — see MapSheet.canQueueMutations()
    // for why the check has to come first rather than off the promise.
    if (!window.MapSheet.canQueueMutations()) {
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
    if (
      (CREATE_URL && url.indexOf(CREATE_URL) !== -1) ||
      (RESORT_CREATE_URL && url.indexOf(RESORT_CREATE_URL) !== -1)
    ) {
      document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    }
  });

  // ---------------------------------------------------------------------------
  // Resort-popup star (SNOW-499) — favourite/unfavourite a resort from the
  // minimal map-pin popup (public/templates/public/partials/_resort_popup.html).
  // Delegated from document — the popup markup is injected by map.js well
  // after this script's own load-time query selectors ran, and this handler
  // must keep working even when the surface-gating early return above would
  // otherwise apply to code defined further down (it doesn't here — this is
  // reached only when favourites_visible is true, since favourites.js itself
  // is only loaded in that case; the star only ever renders in the popup
  // when the requesting user is additionally authenticated).
  //
  // Not favourited: enqueued through window.pwaMutationQueue exactly like
  // the create-form flow above (SNOW-479) — offline-capable, and the queue
  // itself dispatches snowdesk:favourites-changed once the create actually
  // lands (see mutation_queue.js's drain()); dispatching it here too would
  // just race a stale refetch. Already favourited: unfavourited via a plain
  // online POST to the existing delete endpoint (delete stays online-only,
  // matching the dropped-pin favourite detail flow above).
  // ---------------------------------------------------------------------------

  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const star = target && target.closest ? target.closest('[data-resort-star]') : null;
    if (!star) return;
    event.preventDefault();

    const resortId = star.dataset.resortId;
    const lat = parseFloat(star.dataset.resortLat);
    const lon = parseFloat(star.dataset.resortLon);
    const name = star.dataset.resortName || '';
    const alreadyFavourited = star.dataset.favourited === 'true';
    const favUuid = star.dataset.favouriteUuid || '';

    if (!alreadyFavourited) {
      if (!IS_ELIGIBLE || !RESORT_CREATE_URL) return;

      // Same device-availability guard as the create-form submit handler.
      if (!window.MapSheet.canQueueMutations()) {
        showToast('Could not save this favourite on this device — please try again.');
        return;
      }

      const body = new URLSearchParams({
        csrfmiddlewaretoken: getCsrfToken(),
        resort_id: resortId,
      }).toString();

      window.pwaMutationQueue
        .enqueue({
          method: 'POST',
          url: RESORT_CREATE_URL,
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            // favourite_create_from_resort is @require_htmx; the replay is a
            // plain fetch.
            'HX-Request': 'true',
          },
          body: body,
        })
        .catch(function () {});

      if (!isNaN(lat) && !isNaN(lon)) {
        document.dispatchEvent(
          new CustomEvent('snowdesk:favourite-pending', {
            detail: { lat: lat, lon: lon, name: name },
          }),
        );
      }

      window.pwaTelemetry?.emit('map.favourite.created', {
        offline: !navigator.onLine,
        resort: true,
      });

      // The popup's own star state can't be trusted fresh (no uuid yet —
      // the create is queued, possibly offline) — close it rather than
      // show a half-updated star. The next tap re-fetches accurate state.
      document.dispatchEvent(new CustomEvent('snowdesk:resort-popup-close'));
      return;
    }

    // Already favourited — unfavourite via the existing delete path
    // (online-only, matching the dropped-pin detail flow's delete).
    if (!favUuid || !DELETE_URL_TEMPLATE) return;

    fetch(DELETE_URL_TEMPLATE.replace('__UUID__', favUuid), {
      method: 'POST',
      headers: {
        'HX-Request': 'true',
        'X-CSRFToken': getCsrfToken(),
      },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('delete failed');
        window.pwaTelemetry?.emit('map.favourite.deleted', { resort: true });
        document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
        document.dispatchEvent(new CustomEvent('snowdesk:resort-popup-close'));
      })
      .catch(function () {
        showToast('Could not remove this favourite — please try again.');
      });
  });
}());
