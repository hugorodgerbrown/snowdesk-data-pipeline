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
 *   data-routes-eligible="true|false"         — Is there a list to load at
 *                                               all: authenticated, OR an
 *                                               anonymous visitor holding a
 *                                               followed share link.
 *   data-routes-upload-eligible="true|false"  — May this request upload:
 *                                               authentication, which is what
 *                                               data-routes-eligible meant on
 *                                               its own before SNOW-764.
 *   data-signin-url="<url>"                   — Sign-in page for the anon CTA.
 *   data-route-create-url="<url>"             — Multipart upload endpoint.
 *   data-route-list-url="<url>"               — The list endpoint.
 *   data-route-rename-url-template="<url>"    — __UUID__-templated rename.
 *   data-route-share-url-template="<url>"     — __UUID__-templated share mint.
 *
 * SHARING (SNOW-764). A row's Share button mints a link and hands it to the
 * platform, both through window.pwaShare (static/js/share.js) — the same
 * helper the bulletin page's own share button uses, so the three outcomes
 * that matter (the sheet took it, the user cancelled, the clipboard caught
 * it) behave identically on both surfaces. Nothing is swapped: the endpoint
 * answers JSON and its body goes to the share sheet, not into the page.
 *
 * The RECEIVING half is a pending row, rendered server-side by
 * routes:list for a session holding a followed share, with a Save that is a
 * plain HTMX form. Nothing here handles that POST; what this module does is
 * notice it landing (window.pwaRowRemoved, keyed on the claim's own hook)
 * so the list can be re-read and the map told, exactly as it does for a
 * Remove. Two smaller pieces of that wiring sit further down and are
 * easy to miss: an `htmx:beforeSwap` override that lets the claim
 * endpoint's REFUSALS reach the page (htmx discards a 4xx body by
 * default), and a `snowdesk:routes-changed` listener, because the map
 * popup's own Save claims by plain fetch and no HTMX event ever fires
 * for it.
 *
 * The anonymous branch is therefore no longer "sign-in CTA INSTEAD of the
 * list". A visitor who followed a share link has rows to see — that is the
 * whole point of the link — so they get the rows AND the sign-in prompt,
 * because saving one still needs an account. A visitor with nothing pending
 * is unchanged: the CTA replaces the rows.
 *
 * There is no delete URL template, unlike favourites.js: a route row's
 * Remove is a plain HTMX form rendered into the row server-side
 * (routes/partials/_route_row_actions.html), so nothing here ever
 * builds that URL. Only rename needs one, because its commit is a fetch
 * from an inline editor rather than a form submit.
 *
 * Flow when eligible (authenticated):
 *   1. Tap #route-add-btn — opens the sheet on the list panel, whose rows
 *      load over HTMX from routes:list.
 *   2. Tap [data-panel-add] inside it — opens the native file picker by
 *      clicking the hidden #route-upload-input the surface partial renders.
 *   3. Choosing a file POSTs it straight to routes:create as multipart,
 *      then re-reads the list.
 *
 * Flow when not eligible (anonymous, nothing pending):
 *   Tap opens the panel with a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url) in place of the list and the add CTA.
 *
 * Flow for an anonymous visitor holding a followed share link (SNOW-764):
 *   Tap opens the panel, the rows load as normal — they are the pending
 *   shares and nothing else — and the sign-in CTA sits UNDER them rather
 *   than in place of them. The add CTA is still removed: uploading needs
 *   an account.
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
    'share-copied': 'Link copied.',
    'share-failed': "That link couldn't be created. Try again.",
  });

  const CREATE_URL = btn.dataset.routeCreateUrl;
  const LIST_URL = btn.dataset.routeListUrl;
  const RENAME_URL_TEMPLATE = btn.dataset.routeRenameUrlTemplate;
  const SHARE_URL_TEMPLATE = btn.dataset.routeShareUrlTemplate;
  const SIGNIN_URL = btn.dataset.signinUrl;
  // Two questions since SNOW-764, and keeping them apart is what stops a
  // signed-out recipient being offered an upload control they cannot use.
  // IS_ELIGIBLE: there is a list to load. UPLOAD_ELIGIBLE: this visitor may
  // add to it.
  const IS_ELIGIBLE = btn.dataset.routesEligible === 'true';
  const UPLOAD_ELIGIBLE = btn.dataset.routesUploadEligible === 'true';
  // The only way an anonymous request is eligible at all is a followed
  // share link, so the pair says "there is something pending" without a
  // third attribute repeating a fact these two already carry.
  const SHARE_PENDING = IS_ELIGIBLE && !UPLOAD_ELIGIBLE;

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

  /**
   * SNOW-748: true when the app is actually using the network — the interface
   * is up AND no offline mode is in force.
   *
   * A GPX upload is online-only (see the header's note), and the question it
   * has to ask is whether the app is using the network at all, not whether
   * the interface happens to be up: a mode the user forced from the header
   * toggle leaves the interface flag true, so the picker opened and the POST
   * went out under a connection they had asked the app not to spend.
   * ``pwa_offline.js`` owns the answer, and the interface flag stays the
   * fallback for a page where that module has not run.
   *
   * @returns {boolean}
   */
  function networkInUse() {
    const connectivity = window.pwaConnectivity;
    return connectivity ? connectivity.isOnline() : navigator.onLine !== false;
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

    if (!UPLOAD_ELIGIBLE) {
      // Uploading needs an account either way, so the add CTA goes.
      const addButton = sheet.querySelector('[data-panel-add]');
      if (addButton) addButton.remove();

      if (!SHARE_PENDING) {
        const rows = sheet.querySelector('[data-routes-rows]');
        if (rows) rows.replaceChildren(buildSigninCta());
        return;
      }

      // SNOW-764: they followed a share link, so there ARE rows — the
      // pending ones routes:list renders for this session. Load them, and
      // put the sign-in prompt UNDER the list rather than over it: the
      // route they were sent is the thing they came to see, and replacing
      // it with "sign in to save a route" would hide the very thing the
      // link was for. Saving still needs an account, which is what the
      // prompt beneath says.
      //
      // The CTA is appended to the panel's own foot (where the add CTA
      // was) rather than into [data-routes-rows], because that container
      // is replaced wholesale by every list load and by the failed-load
      // handler below — a CTA inside it would vanish on the first
      // re-read.
      loadRows();
      appendSigninCta();
      return;
    }
    loadRows();
  }

  /** Put the sign-in prompt at the foot of the panel, under the rows.
   *
   * Only reached for an anonymous visitor holding a pending share. The
   * panel's foot is where the add CTA lives for a signed-in user, so the
   * prompt lands in the place the surface already reserves for "the thing
   * you do next" — and, unlike the rows container, it survives a list
   * re-read.
   *
   * @returns {void}
   */
  function appendSigninCta() {
    const foot = sheet.querySelector('[data-panel-foot]');
    const host = foot || sheet.querySelector('[data-routes-rows]');
    if (!host) return;
    host.appendChild(buildSigninCta());
  }

  /** (Re)load the panel's rows from routes:list over HTMX.
   *
   * The URL is used VERBATIM — the server writes it once, and the sheet's
   * row template (routes/partials/_route_list_map.html) has been the
   * endpoint's only shape since SNOW-803.
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

  /** Announce that the set of routes has changed, whoever changed it.
   *
   * `snowdesk:routes-changed` is the one signal for "the server's idea of
   * this user's routes has moved": map.js refetches the layer off it, and
   * the listener below re-reads this panel's rows. Every writer in this
   * module goes through here rather than dispatching by hand, so the two
   * halves of the response can never drift apart.
   *
   * @returns {void}
   */
  function announceRoutesChanged() {
    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));
  }

  // The panel LISTENS to that signal as well as raising it, because it is
  // not the only thing that can change a route. The map popup's own Save
  // (static/js/map.js's appendRouteClaimCta) claims through a plain fetch —
  // no HTMX request, so none of the mark-pair watchers below ever see it —
  // and a panel left open behind the popup went on listing the route as
  // pending, with a Save that would 404, until it was closed and reopened.
  //
  // Only while the panel is OPEN. Closing the sheet hides it without
  // emptying it (static/js/map_sheet.js's close), so the rows container is
  // still there to be found — and a shut panel re-clones its body from the
  // template on the next open anyway, which re-reads the list. Fetching for
  // a surface nobody is looking at would be a request per change for
  // nothing.
  document.addEventListener('snowdesk:routes-changed', function () {
    if (!controller.isOpen()) return;
    loadRows();
  });

  // The roundel TOGGLES, matching the layers pill and the other three UGC
  // roundels — a second tap on the control that opened the panel closes it.
  // Bound on the button, so it runs before MapSheet's own document-level
  // click-outside handler, which then sees a closed sheet and does nothing.
  /** Open the sheet on its list — what a roundel tap does when it is shut.
   * @returns {void}
   */
  function openSheet() {
    controller.open();
    showListPanel();
  }

  btn.addEventListener('click', function () {
    if (controller.isOpen()) {
      closeSheet();
      return;
    }
    openSheet();
  });

  // SNOW-803: the sheet-level bridge, read by static/js/map.js to honour a
  // ``/?panel=routes`` arrival — what /account/routes/ redirects to now.
  // ``open`` is the roundel's own open path, so it goes through
  // MapSheet.attach's registration with window.pwaMapOverlays. Frozen,
  // like every other window.pwa* bridge.
  window.pwaRoutesSheet = Object.freeze({
    open: openSheet,
    close: closeSheet,
    isOpen: controller.isOpen,
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
    if (handleFocusClick(event)) return;
    if (handleShareClick(event)) return;
    if (handleRenameClick(event)) return;
    if (!target.closest('[data-panel-add]')) return;
    if (!UPLOAD_ELIGIBLE || !uploadInput) return;
    // Refuse before the picker rather than after the choice: making
    // somebody find a file, then telling them it cannot be sent, wastes
    // the one action they took. See the header's note on online-only.
    if (!networkInUse()) {
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

  /** Handle a click on a row's name, which frames that route on the map.
   *
   * Hugo: "clicking on the name of an item should zoom in to it." The row
   * carries its own bbox (routes/partials/_route.html), so the whole
   * behaviour — switch the overlay on, close this panel, fit the viewport —
   * is window.pwaRowFocus's, shared unchanged with the favourites and
   * field-observation panels. What is left here is the two things only this
   * module holds: which overlay bridge is ours, and how this sheet closes.
   *
   * Tested first in the delegated handler above, before the rename test:
   * the two are different elements (the name, the pencil) and cannot both
   * match, so the order is only about reading — the name is the row's
   * primary control, so it is the first thing the handler asks about.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a focus, so the caller stops
   *   rather than also testing rename and the add CTA.
   */
  function handleFocusClick(event) {
    if (!window.pwaRowFocus) return false;
    return window.pwaRowFocus.handleClick(event, {
      overlay: window.pwaRoutesOverlay,
      close: closeSheet,
    });
  }

  /** Handle a click on a row's Share control (SNOW-764).
   *
   * Two steps, both window.pwaShare's: mint the link, then hand it to the
   * platform. Neither is written here, because the bulletin page's share
   * button does exactly the same two things and one of them (the native
   * sheet's three outcomes) is fiddly enough that a second copy would
   * drift — see static/js/share.js.
   *
   * The uuid rides on the button (`data-route-share`), so this delegated
   * handler needs no closure over the rendered row — the same contract the
   * rename pencil's `data-route-rename` carries, and the reason both
   * survive the sheet body being re-cloned on every open.
   *
   * ONLINE-ONLY, like every other action on this panel: minting a link is
   * a POST, and there is nothing to hand out until it lands. It is refused
   * with the generic share failure rather than the offline upload line —
   * "you need to be online to upload a route" would be a wrong description
   * of what just failed.
   *
   * Only the CLIPBOARD outcome says anything. A share sheet that opened is
   * on screen and needs no announcement; a user who cancelled it declined,
   * and telling them a link was copied would contradict them.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a share, so the caller stops
   *   rather than also testing rename and the add CTA.
   */
  function handleShareClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const control = target.closest('[data-route-share]');
    if (!control) return false;
    if (!SHARE_URL_TEMPLATE || !window.pwaShare) return true;

    const uuid = control.getAttribute('data-route-share');
    if (!uuid) return true;

    window.pwaShare
      .createShare(SHARE_URL_TEMPLATE.replace('__UUID__', uuid), getCsrfToken())
      .then(function (url) {
        window.pwaTelemetry?.emit('map.route.shared', {});
        return window.pwaShare.shareOrCopy(url);
      })
      .then(function (outcome) {
        if (outcome === 'copied') showToast(STRINGS['share-copied']);
        else if (outcome === 'failed') showToast(STRINGS['share-failed']);
      })
      .catch(function () {
        showToast(STRINGS['share-failed']);
      });

    return true;
  }

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
    if (!UPLOAD_ELIGIBLE || !CREATE_URL) return;
    if (!networkInUse()) {
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
        // The map draws the same routes this panel lists, and an upload
        // that only reached the list is the defect this announcement
        // exists for: the overlay's source is already installed for
        // anybody who has the layer switched on, so nothing else would
        // ever put the new line on the map. map.js owns what it costs, and
        // this module's own listener re-reads the rows.
        announceRoutesChanged();
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
  // A row removed from the panel.
  //
  // The row's Remove is a plain HTMX form (nothing here handles the POST),
  // but a route that no longer exists has to leave two more places:
  //
  //   the LIST — the row's own swap empties one <li>, so a panel that has
  //     just lost its last route keeps rendering as a list of none.
  //     routes:list's empty state is a server-side ``{% if not routes %}``
  //     clause and only a fresh response can carry it.
  //
  //   the MAP — the line and its start/finish markers, which nothing else
  //     takes off.
  //
  // Both are the same move rename and upload already make.
  //
  // SNOW-752: the beforeRequest/afterRequest mark-pair that recognises the
  // removal is window.pwaRowRemoved's, shared with the other five UGC lists
  // — see static/js/row_removed.js for why it has to be two events and why
  // the mark rides the xhr.
  // ---------------------------------------------------------------------------

  window.pwaRowRemoved?.watch('[data-routes-rows]', function () {
    window.pwaTelemetry?.emit('map.route.deleted', {});
    announceRoutesChanged();
  });

  // ---------------------------------------------------------------------------
  // A claim the server refused, shown rather than swallowed.
  //
  // routes:share_claim answers every failure with a RENDERED BODY —
  // _route_limit.html on a 409, a line of text on 403/404/429 — and HTMX
  // throws all four away: its default `responseHandling` marks the whole
  // 4xx/5xx range `swap: false`, so the pending row sat there unchanged and
  // a user at their saved-route limit pressed Save and saw absolutely
  // nothing happen.
  //
  // Forced back on for THIS form's swaps only. Making the whole range
  // swappable globally would push every other surface's error body into
  // whatever target it happened to name — the list-load failure below is
  // handled by its own handler, and the favourites and report panels share
  // this document.
  //
  // `isError` is deliberately LEFT TRUE. It is what htmx derives
  // `detail.successful` from, and window.pwaRowRemoved's claim watcher
  // fires on a successful request: clearing it would re-read the list on
  // top of the message that has just been swapped in, wiping the only
  // explanation the user gets. A failed claim is still a failure; the
  // change here is that its body reaches the page.
  // ---------------------------------------------------------------------------

  // The statuses route_share_claim answers with something worth reading.
  // Anything else (a 5xx, a proxy's own error page) keeps htmx's default
  // and is left unswapped — an opaque body in a route row explains less
  // than the row the user was already looking at.
  const CLAIM_SWAP_STATUSES = [403, 404, 409, 429];

  // The DOM id _route.html gives a pending row, and therefore the target
  // the claim form posts to (`hx-target="#route-share-<token>"`).
  const PENDING_ROW_ID_PREFIX = 'route-share-';

  document.addEventListener('htmx:beforeSwap', function (event) {
    const detail = event.detail;
    if (!detail || !detail.xhr) return;
    if (CLAIM_SWAP_STATUSES.indexOf(detail.xhr.status) === -1) return;
    const target = detail.target;
    if (!target || !target.id || !sheet.contains(target)) return;
    if (target.id.indexOf(PENDING_ROW_ID_PREFIX) !== 0) return;
    detail.shouldSwap = true;
  });

  // ---------------------------------------------------------------------------
  // A shared route claimed from the panel (SNOW-764).
  //
  // The Save is a plain HTMX form and its own swap replaces the pending row
  // with the claimed one, so the LIST is momentarily correct without any
  // help. Both re-reads below are still needed:
  //
  //   the LIST — the claimed route is an owned route now, and its position
  //     in the list is the server's to decide (owned rows sort under
  //     pending ones, newest first). The in-place swap leaves it sitting
  //     where the pending row was, at the top, which is not where a
  //     re-read would put it.
  //
  //   the MAP — the claim both adds a line (a new owned route the layer
  //     has never seen) and removes one (the dashed pending line, which
  //     is no longer pending). Nothing else takes either.
  //
  // Same mark-pair mechanism the removal above uses, keyed on the claim
  // form's own hook — see static/js/row_removed.js for why recognising
  // this needs two events.
  // ---------------------------------------------------------------------------

  window.pwaRowRemoved?.watch(
    '[data-routes-rows]',
    function () {
      window.pwaTelemetry?.emit('map.route.claimed', {});
      announceRoutesChanged();
    },
    '[data-row-claimed]'
  );
}());
