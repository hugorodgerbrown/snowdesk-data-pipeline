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
 *
 * Flow when eligible (authenticated):
 *   1. User taps the floating #report-btn.
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
 *     Route into MANUAL path rather than closing the sheet.
 *
 * Flow when not eligible:
 *   Authenticated but unverified (data-report-unverified) → tap opens the
 *   sheet with a "verify your email" prompt. Anonymous → tap opens the sheet
 *   with a sign-in / sign-up CTA pointing at the sign-in page
 *   (data-signin-url). No geolocation attempt is made in either case.
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

  const FORM_URL = btn.dataset.reportFormUrl;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.reportEligible === 'true';
  // Authenticated but email not yet verified (SNOW-477): not eligible for the
  // report flow, but distinct from anonymous — gets a "verify your email"
  // prompt rather than the sign-in CTA.
  const IS_UNVERIFIED = btn.dataset.reportUnverified === 'true';

  // ---------------------------------------------------------------------------
  // Toast helper — reuses the project-wide toast markup conventions.
  // ---------------------------------------------------------------------------

  function showToast(message) {
    const existing = document.getElementById('report-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'report-toast';
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

  // SNOW-486: #report-sheet is a [data-overlay] element (see
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

  // Esc dismisses the sheet.
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
    if (target.closest('#report-sheet')) return;
    if (target.closest('#report-btn')) return;
    if (target.closest('#map')) return;
    if (target.closest('[data-place-picker]')) return;
    closeSheet();
  });

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
    return htmx.ajax('GET', FORM_URL + '?' + params.toString(), { // eslint-disable-line no-undef
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
      showToast('Could not submit your report — please tap a signal type.');
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

    // enqueue() is defensively non-fatal: when IndexedDB is missing or in the
    // terminal Reset-Required state (SNOW-375/376 contract) it silently drops
    // the operation and still resolves, so awaiting it can't tell us the
    // report was persisted. Detect those states up front and surface the
    // error toast instead of showing a success confirmation for a report that
    // was never actually queued.
    const dbUnavailable =
      typeof window.pwaDb !== 'object' ||
      (typeof window.pwaDb.isResetRequired === 'function' &&
        window.pwaDb.isResetRequired());
    if (!window.pwaMutationQueue || dbUnavailable) {
      showToast('Could not save your report on this device — please try again.');
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
      showToast('Something went wrong — please try again.');
      closeSheet();
      return;
    }

    // Otherwise only handle errors originating from the report form itself.
    if (!elt) return;
    const form = elt.closest && elt.closest('#report-form');
    if (!form && elt.id !== 'report-form') return;
    const message =
      (detail.xhr && detail.xhr.responseText) ||
      'Something went wrong — please try again.';
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

  // SNOW-474: persistent × header for states built as innerHTML strings
  // (the anonymous sign-in CTA below), mirroring templates/includes/
  // _sheet_header.html and favourites.js's buildSheetHeader() exactly.
  // i18n: hardcoded English pre-launch; mirrors _sheet_header.html.
  const SHEET_HEADER_HTML =
    '<div class="flex items-center justify-between px-2 pt-1 pb-3">' +
    '<span class="text-sm font-semibold text-text-1">Report</span>' +
    '<button type="button" data-action="dismiss" aria-label="Close" ' +
    'class="text-text-2 hover:text-text-1 text-lg leading-none px-1">×</button>' +
    '</div>';

  btn.addEventListener('click', function () {
    // Ineligible users: open the sheet with a prompt; no geolocation. Two
    // cases — an authenticated-but-unverified user is told to verify their
    // email (SNOW-477); an anonymous user gets a sign-in CTA.
    if (!IS_ELIGIBLE) {
      openSheet();
      if (IS_UNVERIFIED) {
        sheet.innerHTML =
          SHEET_HEADER_HTML +
          '<p class="px-2 py-4 text-sm text-text-2">Verify your email to submit a field observation. Check your inbox for the verification link.</p>';
      } else if (SIGNIN_URL) {
        sheet.innerHTML =
          SHEET_HEADER_HTML +
          '<div class="px-2 py-4">' +
          '<p class="text-sm text-text-2 mb-3">Sign in to submit a field observation.</p>' +
          '<a href="' + SIGNIN_URL + '" class="block w-full rounded-pill bg-status-info-bg text-status-info-text text-sm font-medium text-center py-2 px-4">Sign in</a>' +
          '</div>';
      } else {
        sheet.innerHTML =
          SHEET_HEADER_HTML +
          '<p class="px-2 py-4 text-sm text-text-2">Sign in to submit a field observation.</p>';
      }
      return;
    }

    if (locating) return;
    locating = true;

    // Open the sheet immediately with a locating state so the tap feels
    // responsive while the control acquires a fix.
    openSheet();
    sheet.innerHTML = '<p class="py-4 text-center text-sm text-text-2">Finding your location…</p>';

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
  });
}());
