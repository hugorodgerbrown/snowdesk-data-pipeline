/*
 * static/js/report.js — SNOW-324 GPS-gated field-report affordance.
 *
 * Loaded only when the map page is rendered with ``report_mode=True``
 * (i.e. the ``field_observations`` waffle flag is active for the current
 * user AND the user has a Subscriber profile).
 *
 * Flow:
 *   1. User taps the floating #report-btn.
 *   2. We reuse the page's single geolocation source — SNOW-328's MapLibre
 *      GeolocateControl, owned by map.js — by dispatching
 *      ``snowdesk:locate-request`` and awaiting the position it broadcasts on
 *      ``snowdesk:geolocate`` (or ``snowdesk:geolocate-error``).
 *   3a. On a fix: fire an HTMX GET to report_form_url with lat/lon/accuracy
 *       as query parameters; the sheet (opened on tap) swaps to the form.
 *   3b. On denial/error/timeout: show a toast explaining that location is
 *       required and close the sheet (GPS gate — no fix, no form).
 *   4. The HTMX form POSTs to report_submit_url; on success HTMX swaps
 *       #report-sheet with the confirmation fragment.
 *   5. "Cancel" / "Close" buttons in the partials carry
 *      data-action="close-report-sheet" — we delegate to that from here.
 *
 * Geolocation lives entirely in map.js's GeolocateControl (one permission
 * grant, one accuracy circle); report.js never calls navigator.geolocation
 * directly. The control needs a secure context — fine on localhost and on
 * production HTTPS.
 */

(function () {
  'use strict';

  const btn = document.getElementById('report-btn');
  const sheet = document.getElementById('report-sheet');
  if (!btn || !sheet) return;

  const FORM_URL = btn.dataset.reportFormUrl;

  // ---------------------------------------------------------------------------
  // Toast helper — reuses the project-wide toast markup conventions.
  // We create a transient toast element rather than include the partial
  // server-side because the GPS denial message is JS-only (no server round-trip).
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
  // Geolocation (via SNOW-328's GeolocateControl) → HTMX
  //
  // We don't call navigator.geolocation here. On tap we ask map.js's control
  // for a fix (snowdesk:locate-request) and wait for the position it
  // broadcasts (snowdesk:geolocate) or the error (snowdesk:geolocate-error).
  // ---------------------------------------------------------------------------

  function loadForm(lat, lon, accuracy) {
    const params = new URLSearchParams({ lat: lat, lon: lon });
    if (accuracy != null) params.set('accuracy', accuracy);
    htmx.ajax('GET', FORM_URL + '?' + params.toString(), {
      target: '#report-sheet',
      swap: 'innerHTML',
    });
  }

  function errorMessage(code) {
    if (code === 1) {
      return 'Location access was denied. We need your location to record where you observed — it is only used while you are reporting.';
    }
    if (code === 2) {
      return 'Your location is currently unavailable. Please try again outdoors.';
    }
    return 'Could not get your location. Please try again.';
  }

  // Guard against re-entrant taps while a fix is pending.
  let locating = false;

  btn.addEventListener('click', function () {
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
      loadForm(d.lat, d.lon, d.accuracy);
    }

    function onError(event) {
      cleanup();
      closeSheet();
      showToast(errorMessage(event.detail && event.detail.code));
    }

    document.addEventListener('snowdesk:geolocate', onLocate);
    document.addEventListener('snowdesk:geolocate-error', onError);

    // Safety net: if the control never answers (failed to initialise, or the
    // device hangs without a fix), don't leave the sheet stuck on "Finding…".
    const timer = setTimeout(function () {
      onError({ detail: { code: 3 } });
    }, 20000);

    // Ask SNOW-328's GeolocateControl (owned by map.js) for a fresh fix.
    document.dispatchEvent(new CustomEvent('snowdesk:locate-request'));
  });
}());
