/*
 * static/js/report.js — SNOW-333 field-report affordance with auth gate.
 *
 * Loaded when the field_observations flag is active (report_visible=True),
 * regardless of whether the user is authenticated.  The button carries the
 * data attributes that drive the branching:
 *
 *   data-report-eligible="true|false"   — True for authenticated + verified
 *                                         users (matches the server gate).
 *   data-report-unverified="true|false" — True for an authenticated user whose
 *                                         email is not yet verified (SNOW-477).
 *   data-signin-url="<url>"             — Sign-in page URL for the anon CTA.
 *   data-report-form-url="<url>"        — HTMX form endpoint (eligible only).
 *   data-report-list-url="<url>"        — HTMX list endpoint (eligible only).
 *
 * SNOW-658: the roundel opens a PANEL, not the location flow. The panel
 * (#report-list-template, in _report_surface.html) lists the user's own
 * reports — loaded over HTMX from observations:list, so each row arrives
 * with its own delete wiring — offers [data-panel-add] to file another,
 * and carries the "Display on the map" switch that used to
 * be a row in the layers menu (it drives window.pwaCommunityReportsOverlay
 * in map.js). This follows SNOW-634's downloads pattern: user-generated
 * data gets its own roundel, its own panel, and its panel owns the overlay
 * switch. favourites.js took the same treatment in the same ticket.
 *
 * Flow when eligible (authenticated):
 *   1. User taps the floating #report-btn, then [data-panel-add] in the
 *      panel it opens.
 *   2. We reuse the page's single geolocation source — SNOW-328's MapLibre
 *      GeolocateControl, owned by map.js — by dispatching
 *      ``snowdesk:locate-request`` and awaiting the position it broadcasts on
 *      ``snowdesk:geolocate`` (or ``snowdesk:geolocate-error``).
 *
 * Location-source state machine (eligible path). Both branches below use
 * the shared place-picker (SNOW-475 — static/js/place_picker.js) rather
 * than a draggable marker, which on touch screens is occluded by the
 * dragging finger: a pin fixed at the map's viewport centre, with the
 * coordinate read live from MAP.getCenter() as the user pans underneath it.
 *
 *   GPS path (fix obtained):
 *     snowdesk:geolocate → loadForm(lat, lon, accuracy, 'GPS', gpsLat, gpsLon)
 *     Form renders with "Using current GPS location" + Refine button.
 *     Refine click → PlacePicker.activate({ recenterTo: [lon, lat], onChange })
 *       re-centres the map on the current point and writes hidden lat/lon +
 *       location_source=GPS_REFINED on every pan (keep gps_*).
 *
 *   MANUAL path (GPS denied/unavailable):
 *     snowdesk:geolocate-error → loadForm(null, null, null, 'MANUAL')
 *     Form renders with "GPS not available — move the map to set your
 *     location". Problem buttons start disabled; PlacePicker.activate()
 *     reveals the centre pin and its immediate onChange call — the centre
 *     is always a valid coordinate — writes the form's coords and enables
 *     the buttons right away, no separate "tap to place" step needed.
 *
 *   Safety-net timeout (20 s with no GPS answer):
 *     Route into MANUAL path rather than closing the sheet. SNOW-682 put a
 *     five-second budget on the answer itself (map_geolocate.js), so in
 *     practice this net now only catches a control that never replies at
 *     all — a map bundle that failed to boot, say. It is deliberately not
 *     retuned to match: it guards a DIFFERENT failure (no answer arriving)
 *     from the one that budget bounds (an answer taking too long), and the
 *     gap between the two is what makes it a net.
 *
 * Flow when not eligible:
 *   Authenticated but unverified (data-report-unverified) → the panel shows
 *   a "verify your email" prompt in place of the list and the add CTA.
 *   Anonymous → the same slot holds a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url). No geolocation attempt is made in either
 *   case. The overlay switch stays in both states: community reports are
 *   public data with no eligibility gate at all, so its control is useful to
 *   a visitor even when filing one is not.
 *
 * Submission (SNOW-420 — offline-first via the mutation queue):
 *   The form's GET (loadForm, above) still goes through HTMX — only the
 *   POST changed. A document-delegated ``submit`` listener on #report-form
 *   intercepts the tap (preventDefault — the browser never submits the
 *   form directly), stamps the hidden observed_at input with the tap-time
 *   instant, builds the urlencoded body (including observation_type from
 *   event.submitter, which FormData does not capture once the default
 *   submission is prevented), and hands it to
 *   window.pwaMutationQueue.enqueue() (SNOW-376) — captured immediately,
 *   persisted to IndexedDB, and replayed with a stable Idempotency-Key on
 *   reconnect. The sheet renders the confirmation partial optimistically
 *   (cloned from the #report-confirmation-template embedded in the form
 *   partial) right away; offline, it also reveals the "will sync" line.
 *
 *   Error handling:
 *     htmx:responseError → showToast. The POST no longer goes through HTMX, so
 *     this only ever fires for the form-load GET. That GET targets
 *     #report-sheet (not #report-form, which does not exist yet), so the
 *     handler keys off the sheet target to surface a toast and reset the sheet
 *     rather than stalling on "Finding your location…" (SNOW-477).
 *
 *   Cleanup:
 *     Sheet close, or the optimistic confirmation render on submit, calls
 *     PlacePicker.deactivate() to hide the centre pin and stop tracking the
 *     map. The htmx:afterSwap listener below now only ever sees form-load
 *     swaps (the confirmation swap it also handled pre-SNOW-420 no longer
 *     occurs via HTMX) — kept as a harmless no-op rather than removed,
 *     since a future HTMX-driven swap of #report-sheet would still want it.
 *
 * map.js is unchanged — the snowdesk:locate-request listener already does a
 * plain getCurrentPosition; report.js drives the place-picker via the
 * global MAP.
 */

(function () {
  'use strict';

  const btn = document.getElementById('report-btn');
  const sheet = document.getElementById('report-sheet');
  if (!btn || !sheet) return;
  // SNOW-658: the panel body. Optional on purpose — a surface without it
  // still reaches the report flow straight from the roundel.
  const listTemplate = document.getElementById('report-list-template');

  // SNOW-620: server-translated toast copy, read back from the template
  // _report_surface.html renders. The literals are the English fallback —
  // see static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('report-strings-template', {
    'no-signal-type': 'Could not submit your report — please tap a signal type.',
    'cannot-queue': 'Could not save your report on this device — please try again.',
    generic: 'Something went wrong — please try again.',
    // SNOW-658: 'sheet-title' and 'close' went with SHEET_HEADER_HTML — every
    // state this module renders now clones a template that includes the real
    // includes/_sheet_header.html.
    'verify-email':
      'Verify your email to submit a field observation. Check your inbox for the verification link.',
    'signin-prompt': 'Sign in to submit a field observation.',
    'signin-cta': 'Sign in',
    'list-failed': "Your reports couldn't be loaded — check your connection.",
    locating: 'Finding your location…',
  });

  const esc = self.pwaStrings.escapeHtml;

  const FORM_URL = btn.dataset.reportFormUrl;
  const LIST_URL = btn.dataset.reportListUrl;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.reportEligible === 'true';
  // Authenticated but email not yet verified (SNOW-477): not eligible for the
  // report flow, but distinct from anonymous — gets a "verify your email"
  // prompt rather than the sign-in CTA.
  const IS_UNVERIFIED = btn.dataset.reportUnverified === 'true';

  // ---------------------------------------------------------------------------
  // Sheet controller + toast — SNOW-608's shared static/js/map_sheet.js, which
  // owns the open/focus cycle, the three dismissal routes (Escape,
  // click-outside, overlays.js's [data-action="dismiss"]) and their common
  // teardown. favourites.js attaches to its own sheet the same way.
  //
  // SNOW-658: attaching also registers this sheet with
  // window.pwaMapOverlays, so opening it closes the layers menu, the other
  // two panels and any anchored detail popup — and each of those closes
  // this one. This surface had no exclusivity wiring of its own at all
  // before that, which is what made the old hand-wired pairs read as
  // arbitrary: it opened over whatever happened to be up.
  // ---------------------------------------------------------------------------

  const controller = window.MapSheet.attach(sheet, {
    triggerSelector: '#report-btn',
  });
  const closeSheet = controller.close;
  const showToast = window.MapSheet.toast;

  // ---------------------------------------------------------------------------
  // Form-coord helpers — written by the place-picker's onChange (SNOW-475).
  // ---------------------------------------------------------------------------

  /**
   * Write lat/lon into the report form's hidden inputs, mutating the values
   * that will be POSTed when the user taps a problem button.
   * @param {number} lat
   * @param {number} lon
   */
  function writeFormCoords(lat, lon) {
    const form = document.getElementById('report-form');
    if (!form) return;
    const latInput = form.querySelector('input[name="lat"]');
    const lonInput = form.querySelector('input[name="lon"]');
    if (latInput) latInput.value = lat;
    if (lonInput) lonInput.value = lon;
  }

  /**
   * Write location_source into the report form's hidden input.
   * @param {string} source — 'GPS', 'GPS_REFINED', or 'MANUAL'
   */
  function writeFormLocationSource(source) {
    const form = document.getElementById('report-form');
    if (!form) return;
    const input = form.querySelector('input[name="location_source"]');
    if (input) input.value = source;
  }

  /**
   * Enable all problem-type submit buttons in the report form.
   * Called after a MANUAL pin is placed to unblock submission.
   */
  function enableProblemButtons() {
    const form = document.getElementById('report-form');
    if (!form) return;
    form.querySelectorAll('button[name="observation_type"]').forEach(function (btn) {
      btn.removeAttribute('disabled');
    });
  }

  // ---------------------------------------------------------------------------
  // HTMX form load
  // ---------------------------------------------------------------------------

  /**
   * Fire an HTMX GET to FORM_URL with the given params and swap #report-sheet.
   * @param {number|null} lat
   * @param {number|null} lon
   * @param {number|null} accuracy
   * @param {string} locationSource — 'GPS', 'GPS_REFINED', or 'MANUAL'
   * @param {number|null} gpsLat — raw device fix latitude (null on MANUAL path)
   * @param {number|null} gpsLon — raw device fix longitude (null on MANUAL path)
   * @returns {Promise<any>} resolves once the request has completed and the
   *   swap has settled — htmx.ajax()'s own promise. The MANUAL onError
   *   branch chains off this so PlacePicker.activate() only writes into the
   *   form once it actually exists in the DOM.
   */
  function loadForm(lat, lon, accuracy, locationSource, gpsLat, gpsLon) {
    const params = new URLSearchParams({ location_source: locationSource });
    if (lat != null) params.set('lat', lat);
    if (lon != null) params.set('lon', lon);
    if (accuracy != null) params.set('accuracy', accuracy);
    if (gpsLat != null) params.set('gps_lat', gpsLat);
    if (gpsLon != null) params.set('gps_lon', gpsLon);
    return htmx.ajax('GET', FORM_URL + '?' + params.toString(), {
      target: '#report-sheet',
      swap: 'innerHTML',
    });
  }

  // ---------------------------------------------------------------------------
  // Report-form submit (SNOW-420) — routed through window.pwaMutationQueue
  // instead of HTMX, so an offline tap is captured immediately, persisted to
  // IndexedDB, and replayed with a stable Idempotency-Key on reconnect.
  // Delegated from document so it survives HTMX swaps (the form itself is
  // re-rendered by loadForm's HTMX GET).
  // ---------------------------------------------------------------------------

  document.addEventListener('submit', function (event) {
    if (!event.target || event.target.id !== 'report-form') return;
    event.preventDefault();

    const form = /** @type {HTMLFormElement} */ (event.target);
    const submitter = event.submitter;
    // One-tap UX: every submit is triggered by a problem button carrying
    // its own observation_type value. No submitter (or no value) means
    // this wasn't a real problem-button tap — surface a toast rather than
    // silently dropping a bodyless mutation. preventDefault above has
    // already cancelled the native submit, so there is no fallback path.
    if (!submitter || !submitter.value) {
      showToast(STRINGS['no-signal-type']);
      return;
    }

    // Stamp the tap-time instant — this is the moment the user actually
    // observed the problem, which may differ from whenever the queued
    // mutation eventually replays (SNOW-420).
    const observedAtInput = form.querySelector('input[name="observed_at"]');
    if (observedAtInput) observedAtInput.value = new Date().toISOString();

    // FormData does not include submit-button name/value pairs once the
    // default submission is prevented (submitter is not a "successful
    // control" in that case), so observation_type is set explicitly.
    // Every other field (csrfmiddlewaretoken, lat, lon, location_source,
    // gps_*, accuracy_m, observed_at) is a plain hidden input FormData
    // already captures.
    const params = new URLSearchParams(new FormData(form));
    params.set('observation_type', submitter.value);
    const body = params.toString();

    const operation = {
      method: 'POST',
      url: form.getAttribute('action'),
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        // Mandatory — the replay is a plain fetch, and report_submit is
        // @require_htmx. CSRF rides via the body's csrfmiddlewaretoken
        // plus the same-origin session cookie (fetch defaults to
        // credentials: 'same-origin').
        'HX-Request': 'true',
      },
      body: body,
    };

    // enqueue() is defensively non-fatal — see MapSheet.canQueueMutations()
    // for why the check has to come first rather than off the promise.
    if (!window.MapSheet.canQueueMutations()) {
      showToast(STRINGS['cannot-queue']);
      return;
    }
    window.pwaMutationQueue.enqueue(operation).catch(function () {});

    // Optimistic confirmation — clone the template embedded in the form
    // partial rather than waiting for the queued mutation to replay.
    const template = document.getElementById('report-confirmation-template');
    if (template) {
      sheet.innerHTML = '';
      sheet.appendChild(template.content.cloneNode(true));
      if (!navigator.onLine) {
        const pending = sheet.querySelector('[data-report-pending]');
        if (pending) pending.removeAttribute('hidden');
      }
    }

    window.PlacePicker?.deactivate();
  });

  // ---------------------------------------------------------------------------
  // HTMX response-error handler (covers non-2xx responses to the form's GET
  // load). The POST no longer goes through HTMX (see the submit handler
  // above), so this can now only ever fire for the form-load request.
  // Delegated from document so it survives HTMX swaps.
  // ---------------------------------------------------------------------------

  document.addEventListener('htmx:responseError', function (event) {
    const detail = event.detail || {};
    const elt = detail.elt;
    const target = detail.target;

    // The form-load GET (loadForm) targets #report-sheet, and at that point
    // #report-form does not exist yet, so the elt-based check below never
    // matches it. Handle it explicitly (SNOW-477): surface a toast and reset
    // the sheet so it doesn't stall on "Finding your location…". A generic
    // message is friendlier here than the raw 403 body.
    const isFormLoad = target === sheet || (target && target.id === 'report-sheet');
    if (isFormLoad) {
      showToast(STRINGS.generic);
      closeSheet();
      return;
    }

    // Otherwise only handle errors originating from the report form itself.
    if (!elt) return;
    const form = elt.closest && elt.closest('#report-form');
    if (!form && elt.id !== 'report-form') return;
    const message =
      (detail.xhr && detail.xhr.responseText) ||
      STRINGS.generic;
    showToast(message);
  });

  // ---------------------------------------------------------------------------
  // Refine-location handler (delegated so it survives HTMX form swaps).
  // ---------------------------------------------------------------------------

  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    const refineBtn = target.closest('[data-action="refine-location"]');
    if (!refineBtn) return;

    const form = document.getElementById('report-form');
    if (!form) return;
    const latInput = form.querySelector('input[name="lat"]');
    const lonInput = form.querySelector('input[name="lon"]');
    if (!latInput || !lonInput) return;

    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);
    if (isNaN(lat) || isNaN(lon)) return;

    // Re-centre the map on the current GPS fix and arm the place-picker; the
    // pin starts on that point, and every subsequent pan writes updated
    // coords with location_source=GPS_REFINED (the raw gps_lat/gps_lon
    // hidden inputs stay unchanged).
    window.PlacePicker?.activate({
      recenterTo: [lon, lat],
      occludedBy: sheet,
      onChange: function (la, lo) {
        writeFormCoords(la, lo);
        writeFormLocationSource('GPS_REFINED');
      },
    });
  });

  // ---------------------------------------------------------------------------
  // Confirmation swap cleanup (SNOW-420: now effectively dead but harmless).
  // Pre-SNOW-420 this covered the confirmation partial swapping in via HTMX;
  // the confirmation is now rendered by the submit handler above (which
  // deactivates the place-picker directly), so #report-sheet is only ever
  // swapped by loadForm's HTMX GET — a form re-render, not a confirmation —
  // and the branch below never fires in practice. Left in place rather than
  // removed in case a future HTMX-driven swap of #report-sheet needs it.
  // ---------------------------------------------------------------------------

  document.addEventListener('htmx:afterSwap', function (event) {
    const elt = event.detail && event.detail.target;
    if (elt && elt.id === 'report-sheet') {
      // If the swap was a confirmation (not the form re-render), deactivate
      // the place-picker.
      const form = elt.querySelector('#report-form');
      if (!form) {
        window.PlacePicker?.deactivate();
      }
    }
  });

  // ---------------------------------------------------------------------------
  // Geolocation (via SNOW-328's GeolocateControl) → HTMX
  //
  // We don't call navigator.geolocation here. On tap we ask map.js's control
  // for a fix (snowdesk:locate-request) and wait for the position it
  // broadcasts (snowdesk:geolocate) or the error (snowdesk:geolocate-error).
  // ---------------------------------------------------------------------------

  // Guard against re-entrant taps while a fix is pending.
  let locating = false;

  // SNOW-658: ``SHEET_HEADER_HTML`` lived here — an HTML-string
  // reimplementation of templates/includes/_sheet_header.html (SNOW-474) for
  // the two ineligible states, which had no server-rendered markup of their
  // own. Both now render inside #report-list-template, which includes the
  // real partial, so the copy has no callers and the two can no longer drift.

  /** The ineligible states' body, as an HTML string: a "verify your email"
   * prompt (SNOW-477) for an authenticated-but-unverified user, or the
   * anonymous sign-in CTA. SNOW-658 moved both inside the panel's
   * [data-report-gate] slot, so they no longer carry their own sheet header.
   * @returns {string}
   */
  function gateHtml() {
    if (IS_UNVERIFIED) {
      return '<p class="text-sm text-text-2">' + esc(STRINGS['verify-email']) + '</p>';
    }
    if (SIGNIN_URL) {
      // Same treatment as the favourites and routes gates, from the same
      // place (SNOW-672) — this one is a markup string because the caller
      // assigns it with innerHTML, so it takes the classes rather than the
      // built nodes.
      return (
        '<p class="' + window.snowdeskSigninCta.PROMPT_CLASS + '">' +
        esc(STRINGS['signin-prompt']) +
        '</p>' +
        '<a href="' + SIGNIN_URL + '" class="' + window.snowdeskSigninCta.LINK_CLASS + '">' +
        esc(STRINGS['signin-cta']) +
        '</a>'
      );
    }
    return '<p class="text-sm text-text-2">' + esc(STRINGS['signin-prompt']) + '</p>';
  }

  // ---------------------------------------------------------------------------
  // The panel (SNOW-658) — the sheet's default body: the user's own reports, a
  // CTA to file another, and the map-overlay switch that used to be a layers-
  // menu row.
  //
  // Re-cloned from the template on every open rather than updated in place,
  // mirroring map_downloads_manager.js's render(): a stale row can then never
  // survive a re-open. That is exactly why every listener below is delegated
  // on the SHEET — a per-element listener would have to be rebound each time.
  // ---------------------------------------------------------------------------

  /** Clone #report-list-template into the sheet and populate it.
   *
   * Eligible: the rows load over HTMX from observations:list. Otherwise the
   * rows and the add CTA are removed and the gate slot carries the verify /
   * sign-in prompt — but the overlay switch stays, because community reports
   * are public data and its control is useful to a visitor.
   *
   * @returns {boolean} Whether the panel was rendered — false when the
   *   surface carries no list template, so the caller can fall back.
   */
  function showListPanel() {
    if (!listTemplate) return false;
    sheet.replaceChildren();
    sheet.appendChild(listTemplate.content.cloneNode(true));

    // Reflect the overlay's REAL state rather than a flag of this module's
    // own, the way map_downloads_manager.js's render() does.
    //
    // SNOW-658 review: isEnabled(), the persisted preference — NOT
    // isVisible(), which now answers from the layers MapLibre is drawing.
    // See the matching note in static/js/favourites.js: the switch states
    // what the user asked for, the roundel's ring states whether it reached
    // the map, and offline-with-nothing-cached is the case where the user
    // needs to be able to see the two disagree.
    const toggle = sheet.querySelector('#map-community-reports-overlay-toggle');
    if (toggle) toggle.checked = !!window.pwaCommunityReportsOverlay?.isEnabled?.();

    const gate = sheet.querySelector('[data-report-gate]');
    const rows = sheet.querySelector('[data-report-rows]');
    if (!IS_ELIGIBLE) {
      const addButton = sheet.querySelector('[data-panel-add]');
      if (addButton) addButton.remove();
      if (rows) rows.remove();
      // Every value interpolated here is escaped by gateHtml() — the strings
      // are server-translated copy, not user content, but a locale carrying a
      // quote would still break out of the attribute it sits in.
      if (gate) gate.innerHTML = gateHtml();
      return true;
    }
    if (gate) gate.remove();
    if (rows && LIST_URL && typeof htmx !== 'undefined') {
      htmx.ajax('GET', LIST_URL, { target: rows, swap: 'innerHTML' });
    }
    return true;
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
    // No list template on this surface. The flow is still reachable, and an
    // ineligible user still gets told why it is not — an empty sheet would be
    // a dead end with no explanation.
    if (IS_ELIGIBLE) startReportFlow();
    else sheet.innerHTML = gateHtml();
  });

  // SNOW-658: "Report an observation" — what the roundel itself used to do.
  // Delegated on the sheet so it survives the body being re-cloned.
  //
  // stopPropagation is load-bearing, not caution. MapSheet's click-outside
  // dismissal asks ``sheet.contains(event.target)`` from a DOCUMENT listener,
  // which runs after this one — and by then this handler has replaced the
  // sheet's body, so the button that was clicked is detached and no longer
  // "inside" anything. The sheet would close itself the instant the locating
  // state appeared, with nothing on screen to explain it.
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
    if (!target.closest('[data-panel-add]')) return;
    event.stopPropagation();
    if (!IS_ELIGIBLE) return;
    startReportFlow();
  });

  // SNOW-658: the overlay switch drives window.pwaCommunityReportsOverlay
  // directly — show()/hide() are the only writers of that overlay's
  // visibility, and showListPanel() reads isVisible() back, so the two can
  // never drift. No re-render: nothing else in the panel depends on it.
  sheet.addEventListener('change', function (event) {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target || !target.matches) return;
    if (!target.matches('#map-community-reports-overlay-toggle')) return;
    if (target.checked) window.pwaCommunityReportsOverlay?.show();
    else window.pwaCommunityReportsOverlay?.hide();
  });

  // SNOW-658: the list is fetched, and this panel opens offline while its
  // list does not load offline. Say so, rather than leaving the loading line
  // up forever — and never fall through to observations:list's own empty
  // state, which would tell the user they have reported nothing when the
  // request merely failed. Both htmx failure events are covered:
  // responseError is a non-2xx reply, sendError is no reply at all.
  //
  // Registered before the general htmx:responseError handler above would
  // reach it — that one keys off `target === sheet` (the form load), which
  // this target is not, so the two cannot both fire for one request.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, function (event) {
      const rows = sheet.querySelector('[data-report-rows]');
      if (!rows || !event.detail || event.detail.target !== rows) return;
      const p = document.createElement('p');
      p.className = 'text-sm text-text-2';
      p.textContent = STRINGS['list-failed'];
      rows.replaceChildren(p);
    });
  }

  /** Ask the map for a fix and load the report form from whatever comes back.
   *
   * Unchanged from the flow the roundel used to run directly (SNOW-658 only
   * moved its trigger into the panel): GPS path, MANUAL path, and the
   * twenty-second safety net that routes into MANUAL rather than leaving the
   * sheet stuck on "Finding your location…".
   *
   * @returns {void}
   */
  function startReportFlow() {
    if (locating) return;
    locating = true;

    // Replace the panel with a locating state so the tap feels responsive
    // while the control acquires a fix.
    sheet.innerHTML =
      '<p class="py-4 text-center text-sm text-text-2">' + esc(STRINGS.locating) + '</p>';

    function cleanup() {
      locating = false;
      clearTimeout(timer);
      document.removeEventListener('snowdesk:geolocate', onLocate);
      document.removeEventListener('snowdesk:geolocate-error', onError);
    }

    function onLocate(event) {
      cleanup();
      const d = event.detail || {};
      // GPS path: pass the fix as both the report coords and the raw GPS fix.
      loadForm(d.lat, d.lon, d.accuracy, 'GPS', d.lat, d.lon);
    }

    function onError() {
      cleanup();
      // MANUAL path: load the form in "move the map" state — do NOT close
      // the sheet or show an error toast. Once the swap settles, arm the
      // place-picker: its immediate onChange call (the centre is always a
      // valid coordinate) writes the form's coords and location_source
      // straight away, and enableProblemButtons() unblocks submission —
      // no separate "tap to place" step, unlike the old marker flow.
      loadForm(null, null, null, 'MANUAL', null, null).then(function () {
        window.PlacePicker?.activate({
          occludedBy: sheet,
          onChange: function (la, lo) {
            writeFormCoords(la, lo);
            writeFormLocationSource('MANUAL');
          },
        });
        enableProblemButtons();
      });
    }

    document.addEventListener('snowdesk:geolocate', onLocate);
    document.addEventListener('snowdesk:geolocate-error', onError);

    // Safety net: if the control never answers (failed to initialise, or the
    // device hangs without a fix), route into the MANUAL path rather than
    // leaving the sheet stuck on "Finding…".
    const timer = setTimeout(function () {
      onError();
    }, 20000);

    // Ask SNOW-328's GeolocateControl (owned by map.js) for a fresh fix.
    document.dispatchEvent(new CustomEvent('snowdesk:locate-request'));
  }
}());
