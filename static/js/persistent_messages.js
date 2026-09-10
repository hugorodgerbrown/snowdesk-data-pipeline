/*
 * static/js/persistent_messages.js — record a site banner's dismissal.
 *
 * The admin-managed banners (templates/includes/_persistent_banners.html)
 * are hidden by overlays.js's shared "×" handler like every other banner:
 * it adds the `hidden` class and dispatches a bubbling `overlay:dismissed`.
 * That is a one-page-view hide. This module is the other half — it listens
 * for that event and, when the strip carries a `data-dismiss-url`, DELETEs
 * it so django-persistent-messages records the dismissal against the
 * reader's account and stops sending the banner.
 *
 * The attribute is the whole gate. `apps.public.banners.dismiss_url_for`
 * renders it only for a signed-in reader on a dismissable row, because the
 * endpoint behind it is `login_required` — so an anonymous dismissal fires
 * no request at all rather than earning a redirect to the login page.
 *
 * Deliberately fire-and-forget. The strip is already gone by the time this
 * runs, and a failed DELETE means the banner returns on the next load,
 * which is a strictly better failure than blocking the "×" on the network
 * or reinstating a banner the reader has just closed. Errors are swallowed
 * for the same reason: offline is the expected case, not an exception.
 *
 * No user-facing strings, so nothing here needs the i18n_strings.js
 * treatment (docs/i18n.md).
 */

(function persistentMessagesInit() {
  'use strict';

  /**
   * Tell the server this reader has dismissed a banner.
   *
   * `keepalive` so the request survives the page being navigated away from
   * in the same gesture — a reader who dismisses a banner and immediately
   * clicks a link would otherwise have the DELETE cancelled and see the
   * banner again on the next page.
   *
   * The endpoint is `csrf_exempt` in the package (it dismisses a
   * notification; it deletes no data), so there is no token to attach.
   *
   * @param {string} url
   */
  function recordDismissal(url) {
    try {
      fetch(url, {
        method: 'DELETE',
        credentials: 'same-origin',
        keepalive: true,
      }).catch(function () {
        // Offline, or the row has since been deleted. The banner comes
        // back on the next load; nothing here can do better.
      });
    } catch (_err) {
      // No fetch (or a synchronous throw from a locked-down environment).
    }
  }

  document.addEventListener('overlay:dismissed', function (event) {
    const overlay = event.detail && event.detail.overlay;
    if (!overlay || !overlay.dataset) return;
    const url = overlay.dataset.dismissUrl;
    if (!url) return;
    recordDismissal(url);
  });
})();
