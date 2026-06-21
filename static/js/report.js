/*
 * static/js/report.js — SNOW-333 field-report affordance with auth gate.
 *
 * Loaded when the field_observations flag is active (report_visible=True),
 * regardless of whether the user is authenticated.  The button carries two
 * data attributes that drive the branching:
 *
 *   data-report-eligible="true|false"  — True for authenticated users.
 *   data-signin-url="<url>"            — Sign-in page URL for the anon CTA.
 *   data-report-form-url="<url>"       — HTMX form endpoint (eligible only).
 *
 * Flow when eligible (authenticated):
 *   1. User taps the floating #report-btn.
 *   2. We reuse the page's single geolocation source — SNOW-328's MapLibre
 *      GeolocateControl, owned by map.js — by dispatching
 *      ``snowdesk:locate-request`` and awaiting the position it broadcasts on
 *      ``snowdesk:geolocate`` (or ``snowdesk:geolocate-error``).
 *
 * Location-source state machine (eligible path):
 *   GPS path (fix obtained):
 *     snowdesk:geolocate → loadForm(lat, lon, accuracy, 'GPS', gpsLat, gpsLon)
 *     Form renders with "Using current GPS location" + Refine button.
 *     Refine click → drop draggable marker at current point; on dragend
 *       write hidden lat/lon + set location_source=GPS_REFINED (keep gps_*).
 *
 *   MANUAL path (GPS denied/unavailable):
 *     snowdesk:geolocate-error → loadForm(null, null, null, 'MANUAL')
 *     Form renders with "GPS not available — choose a location on the map".
 *     Problem buttons are disabled. Wire a one-time MAP.click that drops
 *     a draggable marker; on drop enable buttons + update status text.
 *
 *   Safety-net timeout (20 s with no GPS answer):
 *     Route into MANUAL path rather than closing the sheet.
 *
 * Flow when not eligible (anonymous):
 *   Tap opens the sheet with a sign-in / sign-up CTA pointing at the
 *   sign-in page (data-signin-url). No geolocation attempt is made.
 *
 *   Error handling:
 *     htmx:responseError on the report form → showToast with server message.
 *
 *   Cleanup:
 *     Sheet close or confirmation swap → remove the draggable marker.
 *
 * map.js is unchanged — the snowdesk:locate-request listener already does a
 * plain getCurrentPosition; report.js owns its own marker via the global MAP.
 */

(function () {
  'use strict';

  const btn = document.getElementById('report-btn');
  const sheet = document.getElementById('report-sheet');
  if (!btn || !sheet) return;

  const FORM_URL = btn.dataset.reportFormUrl;
  const SIGNIN_URL = btn.dataset.signinUrl;
  const IS_ELIGIBLE = btn.dataset.reportEligible === 'true';

  // ---------------------------------------------------------------------------
  // Module-level draggable marker state and MANUAL map-click handler.
  // ---------------------------------------------------------------------------

  /** @type {maplibregl.Marker|null} */
  let reportMarker = null;

  /**
   * Module-level reference to the MANUAL map-click handler so that
   * closeSheet() can remove it via MAP.off even after the error-branch
   * closure that registered it has gone out of scope.
   * @type {((e: any) => void)|null}
   */
  let activeMapClickHandler = null;

  /** Remove the draggable pin from the map, if one exists. */
  function removeMarker() {
    if (reportMarker) {
      reportMarker.remove();
      reportMarker = null;
    }
  }

  /** Remove the MANUAL map-click listener from MAP, if one is registered. */
  function removeMapClickHandler() {
    const map = getMap();
    if (map && activeMapClickHandler) {
      map.off('click', activeMapClickHandler);
      activeMapClickHandler = null;
    }
  }

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
    removeMarker();
    removeMapClickHandler();
    sheet.setAttribute('hidden', '');
    sheet.innerHTML = '';
  }

  // Delegate close from Cancel / Close buttons inside the sheet.
  document.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target && target.closest && target.closest('[data-action="close-report-sheet"]')) {
      closeSheet();
    }
  });

  // ---------------------------------------------------------------------------
  // Marker helpers — drop a draggable pin on the global MAP.
  // ---------------------------------------------------------------------------

  /**
   * Return the global MAP object if available, else null.
   * Guards against the case where map.js has not yet initialised.
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null; // eslint-disable-line no-undef
  }

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

  /**
   * Place a draggable MapLibre marker at (lat, lon).
   * On dragend, updates the form's hidden lat/lon and optional location_source.
   * @param {number} lat
   * @param {number} lon
   * @param {string|null} dragLocationSource — value to write on dragend, or null to skip
   * @returns {maplibregl.Marker|null}
   */
  function placeMarker(lat, lon, dragLocationSource) {
    const map = getMap();
    if (!map) return null;

    removeMarker(); // Clean up any previous pin.

    reportMarker = new maplibregl.Marker({ draggable: true }) // eslint-disable-line no-undef
      .setLngLat([lon, lat])
      .addTo(map);

    reportMarker.on('dragend', function () {
      const lngLat = reportMarker.getLngLat();
      writeFormCoords(lngLat.lat, lngLat.lng);
      if (dragLocationSource) {
        writeFormLocationSource(dragLocationSource);
      }
    });

    return reportMarker;
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
   */
  function loadForm(lat, lon, accuracy, locationSource, gpsLat, gpsLon) {
    const params = new URLSearchParams({ location_source: locationSource });
    if (lat != null) params.set('lat', lat);
    if (lon != null) params.set('lon', lon);
    if (accuracy != null) params.set('accuracy', accuracy);
    if (gpsLat != null) params.set('gps_lat', gpsLat);
    if (gpsLon != null) params.set('gps_lon', gpsLon);
    htmx.ajax('GET', FORM_URL + '?' + params.toString(), { // eslint-disable-line no-undef
      target: '#report-sheet',
      swap: 'innerHTML',
    });
  }

  // ---------------------------------------------------------------------------
  // HTMX response-error handler (covers 400/403/429 from report_submit).
  // Delegated from document so it survives HTMX swaps.
  // ---------------------------------------------------------------------------

  document.addEventListener('htmx:responseError', function (event) {
    const elt = event.detail && event.detail.elt;
    if (!elt) return;
    // Only handle errors from the report form.
    const form = elt.closest && elt.closest('#report-form');
    if (!form && elt.id !== 'report-form') return;
    const message =
      (event.detail.xhr && event.detail.xhr.responseText) ||
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

    // Drop a draggable marker; on dragend write updated coords and mark source
    // as GPS_REFINED (the raw gps_lat/gps_lon hidden inputs stay unchanged).
    placeMarker(lat, lon, 'GPS_REFINED');
  });

  // ---------------------------------------------------------------------------
  // Confirmation swap cleanup — remove the marker when the thank-you partial
  // swaps in (HTMX swaps #report-sheet innerHTML).
  // ---------------------------------------------------------------------------

  document.addEventListener('htmx:afterSwap', function (event) {
    const elt = event.detail && event.detail.target;
    if (elt && elt.id === 'report-sheet') {
      // If the swap was a confirmation (not the form re-render), clean up the
      // marker and any outstanding MANUAL map-click listener.
      const form = elt.querySelector('#report-form');
      if (!form) {
        removeMarker();
        removeMapClickHandler();
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

  btn.addEventListener('click', function () {
    // Anonymous users: open the sheet with a sign-in CTA; no geolocation.
    if (!IS_ELIGIBLE) {
      openSheet();
      if (SIGNIN_URL) {
        sheet.innerHTML =
          '<div class="px-2 py-4">' +
          '<p class="text-sm text-text-2 mb-3">Sign in to submit a field observation.</p>' +
          '<a href="' + SIGNIN_URL + '" class="block w-full rounded-pill bg-status-info-bg text-status-info-text text-sm font-medium text-center py-2 px-4">Sign in</a>' +
          '</div>';
      } else {
        sheet.innerHTML =
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
      // MANUAL path: load the form in "choose on map" state — do NOT close the
      // sheet or show an error toast. The form will prompt the user to tap a pin.
      loadForm(null, null, null, 'MANUAL', null, null);

      // Wire a one-time MAP click to drop a draggable marker.
      // Store the handler at module scope so closeSheet() can remove it if the
      // user cancels before placing a pin (BLOCKER 2 fix).
      const map = getMap();
      if (!map) return;

      activeMapClickHandler = function onMapClick(e) {
        // Self-deregister: first click consumes the handler.
        removeMapClickHandler();

        const lat = e.lngLat.lat;
        const lon = e.lngLat.lng;

        // Write coords into the form's hidden inputs.
        writeFormCoords(lat, lon);
        writeFormLocationSource('MANUAL');

        // Drop a draggable marker so the user can fine-tune.
        placeMarker(lat, lon, 'MANUAL');

        // Enable the problem buttons now that a location is set.
        enableProblemButtons();
      };

      map.on('click', activeMapClickHandler);
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
