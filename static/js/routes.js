/*
 * static/js/routes.js — SNOW-686 saved-routes panel: upload, list, rename
 * and delete; SNOW-687's "Display on the map" switch.
 *
 * The fourth UGC surface, built to the shape SNOW-634 set for downloads and
 * SNOW-658 generalised across favourites and field observations: its own
 * roundel, its own panel, rows rendered server-side and swapped in over
 * HTMX. static/js/favourites.js is the reference implementation and this
 * module deliberately tracks it — where the two differ, the difference is
 * noted below.
 *
 * Loaded from public/partials/_routes_surface.html (not home.html, and not
 * the map bundle — see that partial's own note) for every visitor,
 * regardless of whether the user is authenticated. The floating
 * #route-add-btn carries the data attributes that drive branching:
 *
 *   data-routes-eligible="true|false"         — True for authenticated users.
 *   data-signin-url="<url>"                   — Sign-in page for the anon CTA.
 *   data-route-create-url="<url>"             — Multipart upload endpoint.
 *   data-route-list-url="<url>"               — The list endpoint, ?variant=map.
 *   data-route-rename-url-template="<url>"    — __UUID__-templated rename.
 *
 * There is no delete URL template, unlike favourites.js: a route row's
 * Remove is a plain HTMX form rendered into the row server-side
 * (routes/partials/_route_row_actions.html), so nothing here ever
 * builds that URL. Only rename needs one, because its commit is a fetch
 * from an inline editor rather than a form submit.
 *
 * Flow when eligible (authenticated):
 *   1. Tap #route-add-btn — opens the sheet on the list panel, whose rows
 *      load over HTMX from routes:list at ?variant=map.
 *   2. Tap [data-panel-add] inside it — opens the native file picker by
 *      clicking the hidden #route-upload-input the surface partial renders.
 *   3. Choosing a file POSTs it straight to routes:create as multipart,
 *      then re-reads the list.
 *
 * Flow when not eligible (anonymous):
 *   Tap opens the panel with a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url) in place of the list and the add CTA.
 *
 * NO MAP STATE, still. Unlike favourites.js this module never reads
 * window.snowdeskMapState: a route's geometry comes out of the uploaded
 * file, so there is no place-picker to arm and no map centre to seed a
 * create form from.
 *
 * It DOES reach the map, as of SNOW-687, but only through
 * window.pwaRoutesOverlay — the frozen bridge map.js publishes, the same
 * one favourites.js uses for its own switch. Two rules there, and both are
 * load-bearing:
 *
 *   - the switch reflects ``isEnabled()``, the persisted PREFERENCE, never
 *     ``isVisible()``, which answers from the layers MapLibre is actually
 *     drawing. Enable the overlay offline with nothing cached and the two
 *     legitimately disagree — switch ON, roundel ring OFF — and that pair
 *     is the only way the user can see their request never reached the
 *     map. Painting the switch from paint would instead show their own
 *     setting silently flipping itself off.
 *   - show()/hide() are the only writers of that overlay's visibility, so
 *     the switch and the map cannot drift.
 *
 * The roundel's `data-overlay-shown` ring follows from the same bridge,
 * painted by static/js/map_roundel_overlay_state.js — nothing here writes
 * it.
 *
 * UPLOAD IS ONLINE-ONLY, and posts straight to routes:create rather than
 * through window.pwaMutationQueue. The queue persists JSON bodies in
 * IndexedDB (docs/mutation-queue.md); a multipart file is a different
 * shape, and making an upload replayable — storing the bytes, replaying the
 * boundary, deciding what a stale 5 MB body costs a device's quota — is
 * real scope of its own rather than a smaller version of what the queue
 * already does. SNOW-504's resort-favourite toggle is the precedent for a
 * UGC action that is deliberately online-only. So an offline tap is
 * refused up front with a line that says so, rather than accepted and
 * silently dropped. Rename and delete are online-only too, matching every
 * other panel's edits.
 *
 * EXCLUSIVITY. window.MapSheet.attach registers this sheet with
 * window.pwaMapOverlays (static/js/map_overlay_exclusivity.js) under its
 * own DOM id and calls opening() before revealing it, so the
 * one-open-overlay-at-a-time matrix picks the panel up without this module
 * naming a single sibling. The registered name is therefore 'route-sheet' —
 * the element's id, the convention every other registered surface follows
 * (tests/js/test_map_overlay_exclusivity_surfaces.js asserts the whole set
 * by id), so nothing here calls register() or opening() by hand.
 */

(function routesPanelInit() {
  'use strict';

  const btn = document.getElementById('route-add-btn');
  const sheet = document.getElementById('route-sheet');
  const listTemplate = document.getElementById('route-list-template');
  if (!btn || !sheet || !listTemplate) return;

  // The file picker and the CSRF token, both rendered OUTSIDE the sheet so
  // they survive the sheet's body being re-cloned on every open — an
  // <input type="file"> holding the user's chosen file must not be thrown
  // away underneath the upload it started.
  const uploadForm = document.getElementById('route-upload-form');
  const uploadInput = /** @type {HTMLInputElement|null} */ (
    document.getElementById('route-upload-input')
  );

  // Server-translated copy, read back from the template
  // _routes_surface.html renders. makemessages never scans static/js, so a
  // literal here would ship as English to every locale (`tox -e i18n-lint`
  // fails the build on one). The literals below are the English fallback —
  // see static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('routes-strings-template', {
    'signin-prompt': 'Sign in to save a route.',
    'signin-cta': 'Sign in',
    'list-failed': "Your routes couldn't be loaded — check your connection.",
    'rename-failed': "That name couldn't be saved. Try again.",
    'upload-offline': 'You need to be online to upload a route.',
    'upload-invalid':
      "That file couldn't be read as GPX. It must be a .gpx file containing a track or a route.",
    'upload-too-large': 'That file is too large to upload.',
    'upload-limit': "You've reached your saved-route limit. Remove one to upload another.",
    'upload-failed': "That route couldn't be uploaded. Try again.",
  });

  const CREATE_URL = btn.dataset.routeCreateUrl;
  const LIST_URL = btn.dataset.routeListUrl;
  const RENAME_URL_TEMPLATE = btn.dataset.routeRenameUrlTemplate;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.routesEligible === 'true';

  // ---------------------------------------------------------------------------
  // Sheet controller + toast — SNOW-608's shared static/js/map_sheet.js, which
  // owns the open/focus cycle, the three dismissal routes (Escape,
  // click-outside, overlays.js's [data-action="dismiss"]), their common
  // teardown, and the overlay registration described in the header above.
  // ---------------------------------------------------------------------------

  const controller = window.MapSheet.attach(sheet, {
    triggerSelector: '#route-add-btn',
  });
  const closeSheet = controller.close;
  const showToast = window.MapSheet.toast;

  /** Read the CSRF token from the (inert) upload form.
   *
   * Django issues one CSRF token per session, valid across every form on
   * the page, so both this module's plain-fetch calls — the upload POST and
   * the rename commit — read the same one. Mirrors favourites.js, which
   * reads its own out of the create-form template.
   *
   * @returns {string}
   */
  function getCsrfToken() {
    const input = uploadForm
      ? uploadForm.querySelector('input[name="csrfmiddlewaretoken"]')
      : null;
    return input ? input.value : '';
  }

  /** Build the anonymous sign-in CTA — a prompt plus a link to the sign-in
   * page. Built with createElement rather than an innerHTML string: the
   * same DOM-not-markup discipline favourites.js uses for anything
   * URL-bearing.
   * @returns {HTMLDivElement}
   */
  function buildSigninCta() {
    return window.snowdeskSigninCta.build(
      SIGNIN_URL,
      STRINGS['signin-prompt'],
      STRINGS['signin-cta']
    );
  }

  // ---------------------------------------------------------------------------
  // The panel — the sheet's only body: the user's own routes and a CTA to
  // add another.
  //
  // Re-cloned from the template on every open rather than updated in place,
  // mirroring favourites.js's showListPanel() and
  // map_downloads_manager.js's render(): a stale row can then never survive
  // a re-open. That is exactly why every listener below is delegated on the
  // SHEET — a per-element listener would have to be rebound each time.
  // ---------------------------------------------------------------------------

  /** Clone #route-list-template into the sheet and populate it.
   *
   * Eligible: the rows load over HTMX from routes:list. Anonymous: the rows
   * and the add CTA are removed and a sign-in CTA takes their place — but
   * the overlay switch stays, exactly as it does on the favourites panel:
   * it is a view control for the map behind the sheet, not a row in a list
   * the visitor hasn't got.
   *
   * @returns {void}
   */
  function showListPanel() {
    sheet.replaceChildren();
    sheet.appendChild(listTemplate.content.cloneNode(true));

    // SNOW-687: reflect the overlay's REAL state rather than a flag of this
    // module's own, the way favourites.js and map_downloads_manager.js do.
    //
    // isEnabled(), the persisted preference — NOT isVisible(), which
    // answers from the layers MapLibre is drawing. See this file's header:
    // the switch states what the user asked for, the roundel's ring states
    // whether it reached the map, and the case where they disagree is the
    // case the distinction exists for.
    const toggle = sheet.querySelector('#map-routes-overlay-toggle');
    if (toggle) toggle.checked = !!window.pwaRoutesOverlay?.isEnabled?.();

    if (!IS_ELIGIBLE) {
      const addButton = sheet.querySelector('[data-panel-add]');
      if (addButton) addButton.remove();
      const rows = sheet.querySelector('[data-routes-rows]');
      if (rows) rows.replaceChildren(buildSigninCta());
      return;
    }
    loadRows();
  }

  /** (Re)load the panel's rows from routes:list over HTMX.
   *
   * The URL is used VERBATIM — the server puts ``?variant=map`` on it so
   * the sheet gets the lean row template (routes/partials/
   * _route_list_map.html), and rebuilding the path here would silently drop
   * it and render the default variant's markup instead.
   *
   * Called on open, and again after an upload or a rename lands — the same
   * re-read-the-truth move favourites.js makes, rather than patching a row
   * in place from what we think we just wrote.
   *
   * @returns {void}
   */
  function loadRows() {
    const rows = sheet.querySelector('[data-routes-rows]');
    if (!rows || !LIST_URL || typeof htmx === 'undefined') return;
    htmx.ajax('GET', LIST_URL, { target: rows, swap: 'innerHTML' });
  }

  // The roundel TOGGLES, matching the layers pill and the other three UGC
  // roundels — a second tap on the control that opened the panel closes it.
  // Bound on the button, so it runs before MapSheet's own document-level
  // click-outside handler, which then sees a closed sheet and does nothing.
  btn.addEventListener('click', function () {
    if (controller.isOpen()) {
      closeSheet();
      return;
    }
    controller.open();
    showListPanel();
  });

  // ---------------------------------------------------------------------------
  // Delegated sheet clicks — the rename pencil and the add CTA.
  //
  // NOTHING here calls stopPropagation, unlike favourites.js's own version.
  // That module needs it because its add CTA REPLACES the sheet's body with
  // the create form, which detaches the clicked button before MapSheet's
  // document-level click-outside handler asks whether the click was inside
  // the sheet — so the sheet would dismiss itself the instant the form
  // appeared. Neither handler here replaces the body: the pencil reveals an
  // editor already in the row, and the add CTA opens a native file picker.
  // The clicked element is still inside the sheet when that handler runs,
  // so the default behaviour is already correct and hiding the click from
  // every other document-level listener would buy nothing.
  // ---------------------------------------------------------------------------

  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (handleRenameClick(event)) return;
    if (!target.closest('[data-panel-add]')) return;
    if (!IS_ELIGIBLE || !uploadInput) return;
    // Refuse before the picker rather than after the choice: making
    // somebody find a file, then telling them it cannot be sent, wastes
    // the one action they took. See the header's note on online-only.
    if (!navigator.onLine) {
      showToast(STRINGS['upload-offline']);
      return;
    }
    uploadInput.click();
  });

  // SNOW-687: the overlay switch drives window.pwaRoutesOverlay directly —
  // show()/hide() are the only writers of that overlay's visibility, and
  // showListPanel() reads isEnabled() back, so the two can never drift. No
  // re-render: nothing else in the panel depends on this state. Delegated
  // on the sheet because the body is re-cloned on every open.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target || !target.matches) return;
    if (!target.matches('#map-routes-overlay-toggle')) return;
    if (target.checked) window.pwaRoutesOverlay?.show();
    else window.pwaRoutesOverlay?.hide();
  });

  /** Handle a click that starts a row's inline rename.
   *
   * The row's pencil opens the editor already sitting hidden in the row's
   * own markup; window.pwaInlineRename owns the focus/keyboard/blur state
   * machine — shared unchanged with the favourites and downloads panels —
   * and calls back exactly once, with a name worth writing. Escape, an
   * unchanged name and an emptied field never reach here.
   *
   * Online-only, matching every other panel's rename.
   *
   * SNOW-711: the POST itself is window.pwaRowRenameCommit's, shared with
   * the favourites panel and the account page. What is left here is this
   * panel's own share — which hook carries the uuid, where the CSRF token
   * lives, and what to do once the write lands.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a rename, so the caller stops
   *   rather than also testing the add CTA.
   */
  function handleRenameClick(event) {
    if (!window.pwaRowRenameCommit) return false;
    return window.pwaRowRenameCommit.handleClick(event, {
      uuidAttribute: 'data-route-rename',
      urlTemplate: RENAME_URL_TEMPLATE,
      csrfToken: getCsrfToken,
      onCommitted: function () {
        // Re-read the list rather than swap the response in: route_rename
        // answers with routes/partials/_route.html — the row the DEFAULT
        // variant renders, and after SNOW-711 the shared UGC row too, but
        // rendered for a surface that swaps it into itself. This panel
        // clones its own body on every open, so re-reading is both the
        // cheaper move and the one that cannot leave two sources of truth
        // about what the list holds. That endpoint is left exactly as
        // SNOW-685 shipped it: teaching it a ``?variant=`` parameter would
        // buy one avoided refetch and put a second surface's concern into
        // a create/rename endpoint.
        loadRows();
      },
      onFailed: function () {
        showToast(STRINGS['rename-failed']);
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Upload — a multipart POST straight to routes:create. Online-only; see
  // the header for why this is not routed through window.pwaMutationQueue.
  // ---------------------------------------------------------------------------

  /** The message for an upload the server refused.
   *
   * One line per outcome route_create distinguishes, because "it didn't
   * work" is not actionable and each of these has a different next step:
   * pick a different file, delete a route, or wait and retry. The statuses
   * are that view's own contract — 400 unparseable, 409 at the per-user
   * cap, 413 over ROUTE_UPLOAD_MAX_BYTES — and 403/429/5xx all fall to the
   * generic "try again", which is the correct advice for each.
   *
   * @param {number} status An HTTP status code.
   * @returns {string} A user-facing, translated line.
   */
  function uploadFailureMessage(status) {
    if (status === 400) return STRINGS['upload-invalid'];
    if (status === 409) return STRINGS['upload-limit'];
    if (status === 413) return STRINGS['upload-too-large'];
    return STRINGS['upload-failed'];
  }

  /** POST one chosen file to routes:create and re-read the list.
   *
   * The response body is deliberately DISCARDED. route_create returns the
   * default variant's row (_route.html), which is the wrong shape for this
   * panel — the same reason rename re-reads rather than swaps — so the
   * authoritative list is refetched instead of patching in what we think
   * was just created.
   *
   * No Content-Type header: passing FormData to fetch lets the browser set
   * `multipart/form-data` WITH its generated boundary. Setting the header
   * by hand omits the boundary and the server cannot parse the body.
   *
   * @param {File} file The user's chosen .gpx file.
   * @returns {void}
   */
  function upload(file) {
    if (!IS_ELIGIBLE || !CREATE_URL) return;
    if (!navigator.onLine) {
      showToast(STRINGS['upload-offline']);
      return;
    }

    const body = new FormData();
    body.append('file', file);

    fetch(CREATE_URL, {
      method: 'POST',
      headers: {
        // route_create is @require_htmx; this is a plain fetch. CSRF rides
        // in the header plus the same-origin session cookie (fetch defaults
        // to credentials: 'same-origin').
        'HX-Request': 'true',
        'X-CSRFToken': getCsrfToken(),
      },
      body: body,
    })
      .then(function (resp) {
        if (!resp.ok) {
          showToast(uploadFailureMessage(resp.status));
          return;
        }
        window.pwaTelemetry?.emit('map.route.created', {});
        loadRows();
      })
      .catch(function () {
        showToast(STRINGS['upload-failed']);
      });
  }

  if (uploadInput) {
    uploadInput.addEventListener('change', function () {
      const file = uploadInput.files && uploadInput.files[0];
      // Clear the input BEFORE uploading, not after: it is a persistent
      // element, and a `change` event only fires when the value actually
      // changes — so re-picking the SAME file after a failed upload would
      // be a no-op with a stale value still sitting there. `file` is
      // already captured, so clearing first costs nothing.
      uploadInput.value = '';
      if (file) upload(file);
    });
  }

  // The list is fetched, and this panel opens offline while its list does
  // not load offline. Say so, rather than leaving the loading line up
  // forever — and never fall through to routes:list's own empty state,
  // which would tell the user they have no routes when the request merely
  // failed. Both htmx failure events are covered: responseError is a
  // non-2xx reply, sendError is no reply at all (the offline case).
  //
  // Unlike the favourites equivalent this is the ONLY writer of that
  // element: there is no routes_offline.js repainting cached rows over the
  // line. SNOW-687 caches the map LAYER's geometry offline
  // (map_overlay_offline_cache.js), not this list's rows.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, function (event) {
      const rows = sheet.querySelector('[data-routes-rows]');
      if (!rows || !event.detail || event.detail.target !== rows) return;
      const p = document.createElement('p');
      p.className = 'text-sm text-text-2';
      p.textContent = STRINGS['list-failed'];
      rows.replaceChildren(p);
    });
  }

  // ---------------------------------------------------------------------------
  // htmx response handling — a row removed from the panel.
  //
  // The panel row's own Remove form posts and swaps itself out, and it is
  // the only htmx request this module has to notice. Two-step: mark on
  // htmx:beforeRequest whether the element making the request lives inside
  // the panel's rows (checked *before* any swap runs, so elt.closest() sees
  // a properly attached tree — an outerHTML swap like that row removal
  // detaches the element by the time a later event fires), then act on
  // htmx:afterRequest, the last event in htmx's lifecycle.
  //
  // The mark is stamped on the request's own XMLHttpRequest, not on a
  // module-level variable: both listeners are document-level, so every HTMX
  // request on the page runs through them — this surface shares the map
  // homepage with the favourites, report and downloads panels — and a
  // shared variable is rewritten by whichever request fires last, with no
  // way to correlate an afterRequest back to the beforeRequest that set it.
  // htmx carries the same xhr object on both events, so the mark rides the
  // request it describes. Lifted from favourites.js, which needs it for the
  // same reason.
  // ---------------------------------------------------------------------------

  const PANEL_ROW_REQUEST_MARK = 'snowdeskRoutePanelRowRequest';

  document.addEventListener('htmx:beforeRequest', function (event) {
    const detail = event.detail || {};
    if (!detail.xhr) return;
    const elt = detail.elt;
    detail.xhr[PANEL_ROW_REQUEST_MARK] = !!(
      elt && elt.closest && elt.closest('[data-routes-rows]')
    );
  });

  document.addEventListener('htmx:afterRequest', function (event) {
    const xhr = event.detail && event.detail.xhr;
    if (!xhr || !xhr[PANEL_ROW_REQUEST_MARK]) return;
    if (!event.detail.successful) return;
    window.pwaTelemetry?.emit('map.route.deleted', {});
  });
}());
