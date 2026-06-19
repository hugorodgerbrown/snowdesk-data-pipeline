/*
 * static/js/report.js — SNOW-324 GPS-gated field-report affordance.
 *
 * Loaded only when the map page is rendered with ``report_mode=True``
 * (i.e. the ``field_observations`` waffle flag is active for the current
 * user AND the user has a Subscriber profile).
 *
 * Flow:
 *   1. User taps the floating #report-btn.
 *   2. We call navigator.geolocation.getCurrentPosition.
 *   3a. On success: fire an HTMX GET to report_form_url with lat/lon/accuracy
 *       as query parameters; open #report-sheet to show the form.
 *   3b. On denial/error: show a toast explaining that location is required
 *       and do NOT open the form (GPS gate — no fix, no form).
 *   4. The HTMX form POSTs to report_submit_url; on success HTMX swaps
 *       #report-sheet with the confirmation fragment.
 *   5. "Cancel" / "Close" buttons in the partials carry
 *      data-action="close-report-sheet" — we delegate to that from here.
 *
 * Secure-context note: navigator.geolocation is available on localhost
 * (treated as a secure context by all modern browsers) and on production
 * HTTPS. It is never available on plain HTTP origins, but Snowdesk is
 * HTTPS-only in production.
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
  // Geolocation → HTMX
  // ---------------------------------------------------------------------------

  btn.addEventListener('click', function () {
    if (!navigator.geolocation) {
      showToast('Location is not supported by your browser.');
      return;
    }

    navigator.geolocation.getCurrentPosition(
      // Success callback.
      function (position) {
        const lat = position.coords.latitude;
        const lon = position.coords.longitude;
        const accuracy = position.coords.accuracy; // metres

        const params = new URLSearchParams({ lat: lat, lon: lon });
        if (accuracy != null) params.set('accuracy', accuracy);

        const url = FORM_URL + '?' + params.toString();

        // Load the report form into the sheet via HTMX.
        // htmx.ajax fires a GET and swaps the response into the target element.
        openSheet();
        // Show a loading indicator inside the sheet.
        sheet.innerHTML = '<p class="py-4 text-center text-sm text-text-2">Loading…</p>';

        htmx.ajax('GET', url, {
          target: '#report-sheet',
          swap: 'innerHTML',
        });
      },

      // Error callback — GPS denied or unavailable.
      function (error) {
        let message;
        if (error.code === error.PERMISSION_DENIED) {
          message = 'Location access was denied. We need your location to record where you observed — it is only used while you are reporting.';
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          message = 'Your location is currently unavailable. Please try again outdoors.';
        } else {
          message = 'Could not get your location. Please try again.';
        }
        showToast(message);
      },

      // Options — accept a cached fix up to 60 s old; time out after 10 s.
      { maximumAge: 60000, timeout: 10000 }
    );
  });
}());
