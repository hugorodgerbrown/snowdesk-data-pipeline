/*
 * static/js/trip_share.js — the organiser's Share and Stop sharing controls
 * (SNOW-821).
 *
 * Two clicks, both delegated off `document` so neither needs a closure over
 * the rendered page. The uuid rides on each button, exactly as the route
 * row's own Share does — which is why SNOW-834 could put the same Share on
 * every organised row of the trips list without this module learning that
 * the list exists. It reads what a control carries, not where it sits.
 *
 * ## What is NOT written here
 *
 * The native share sheet and its three outcomes, and the clipboard fallback
 * behind them, are `window.pwaShare`'s (`static/js/share.js`) — shared
 * unchanged with the bulletin page and the routes panel. A second copy of
 * that logic would drift, and the outcome it would drift on is the fiddly
 * one: a user who CANCELLED the sheet has declined, so telling them a link
 * was copied contradicts them.
 *
 * Only the clipboard outcome says anything, for that reason. A share sheet
 * that opened is on screen and needs no announcement.
 *
 * ## Rotation is not confirmed here
 *
 * Pressing Share on a trip that already has a live link ROTATES it, which
 * stops a link already sent to a group chat working. That is stated on the
 * surface itself — in the sentence under the buttons on the trip's page
 * (`_trip_share_controls.html`), and in the list row's own label and title
 * (`_trip_list_row.html`, which reads "Share again" once a link is live) —
 * rather than in a `confirm()`. A modal on the primary action of the
 * surface would be asked and dismissed every time, which is how a warning
 * stops being read.
 *
 * ## Exports (frozen `self.pwaTripShareCore`)
 *
 *   shareUrlFor(uuid)   → the mint endpoint's path
 *   revokeUrlFor(uuid)  → the revoke endpoint's path
 *
 * Both are pure string builders, so they are unit-tested directly
 * (tests/js/test_trip_share.js) without a browser or a server. The URL
 * TEMPLATES come from the page (`data-trip-share-url`), because a JS module
 * must not know how this project spells its URLs.
 */

(function () {
  'use strict';

  // The English fallbacks are the only copy of these strings a reader of
  // this file can see, so they double as documentation of what each key
  // means. `i18n_strings.js` is loaded first by the page, in document
  // order, so `self.pwaStrings` is here — the same unguarded read
  // `routes.js` makes.
  var STRINGS = self.pwaStrings.read('trip-share-strings-template', {
    'share-copied': 'Link copied.',
    'share-failed': "That link couldn't be created. Try again.",
    'revoke-failed': "The link couldn't be revoked. Try again.",
  });

  /**
   * Build the mint endpoint's URL for one trip.
   *
   * @param {string} template The `__UUID__`-templated path from the page.
   * @param {string} uuid
   * @returns {string}
   */
  function shareUrlFor(template, uuid) {
    if (!template || !uuid) return '';
    return String(template).replace('__UUID__', uuid);
  }

  /**
   * Build the revoke endpoint's URL for one trip.
   *
   * Separate from `shareUrlFor` rather than one function with a suffix
   * argument: the two paths are reversed independently server-side, so a
   * client-side suffix would be this module inventing a URL shape.
   *
   * @param {string} template The `__UUID__`-templated path from the page.
   * @param {string} uuid
   * @returns {string}
   */
  function revokeUrlFor(template, uuid) {
    if (!template || !uuid) return '';
    return String(template).replace('__UUID__', uuid);
  }

  /**
   * Read the page's CSRF token.
   *
   * @returns {string}
   */
  function csrfToken() {
    var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return field ? field.value : '';
  }

  // The toast group this module speaks through, where a page renders one.
  // A GROUP and not two ids: toasts in a group share one screen position
  // (`fixed bottom-4 left-1/2`), so showing one means hiding the rest —
  // see the `group` parameter of templates/includes/_toast.html.
  var TOAST_GROUP = '[data-toast-group="trip"]';

  /**
   * Show one of the page's own toasts, hiding the rest of its group first.
   *
   * `hidden` is declared after `flex` in the compiled stylesheet and so
   * beats it, which is why hiding is one class added and not a pair
   * swapped — the same idiom `overlays.js` hides every other overlay with.
   *
   * @param {HTMLElement} toast
   * @param {string} message
   * @returns {void}
   */
  function showToast(toast, message) {
    document.querySelectorAll(TOAST_GROUP).forEach(function (other) {
      if (other !== toast) other.classList.add('hidden');
    });
    var body = toast.querySelector('[data-toast-body]');
    if (body) body.textContent = message;
    toast.classList.remove('hidden');
    toast.classList.add('flex');
  }

  /**
   * Say what happened, in whichever surface the page gives for saying it.
   *
   * Two surfaces because there are two pages. The trips list (SNOW-834)
   * renders a toast per kind, since a row's Share has no prose beside it to
   * write into. The trip's own page renders none: the paragraph under the
   * buttons is already the thing that describes the link's state, so a
   * failure to change that state belongs there, and adding a toast for two
   * messages would be a second notification system on a page with none.
   *
   * The paragraph ignores `kind` — it is prose, and prose is not coloured.
   *
   * @param {string} message
   * @param {string} kind `'success'` or `'error'`.
   * @returns {void}
   */
  function announce(message, kind) {
    var toast = document.querySelector(
      TOAST_GROUP + '[data-kind="' + kind + '"]'
    );
    if (toast) {
      showToast(/** @type {HTMLElement} */ (toast), message);
      return;
    }
    var host = document.querySelector('[data-testid="trip-share-state"]');
    if (host) host.textContent = message;
  }

  /**
   * Handle a click on Share.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a share.
   */
  function handleShareClick(event) {
    var control = event.target.closest('[data-trip-share]');
    if (!control) return false;
    if (!window.pwaShare) return true;

    var root = document.querySelector('[data-trip-share-url]');
    var url = shareUrlFor(
      root ? root.getAttribute('data-trip-share-url') : '',
      control.getAttribute('data-trip-share')
    );
    if (!url) return true;

    window.pwaShare
      .createShare(url, csrfToken())
      .then(function (shareUrl) {
        return window.pwaShare.shareOrCopy(shareUrl);
      })
      .then(function (outcome) {
        if (outcome === 'copied') announce(STRINGS['share-copied'], 'success');
        else if (outcome === 'failed') announce(STRINGS['share-failed'], 'error');
      })
      .catch(function () {
        announce(STRINGS['share-failed'], 'error');
      });

    return true;
  }

  /**
   * Handle a click on Stop sharing.
   *
   * Reloads on success rather than repainting the controls in place: the
   * page's own copy states when the link expires, and re-deriving that
   * sentence client-side would put a second, translated copy of it in a
   * module `makemessages` never scans.
   *
   * @param {MouseEvent} event
   * @returns {boolean} Whether this click was a revoke.
   */
  function handleRevokeClick(event) {
    var control = event.target.closest('[data-trip-share-revoke]');
    if (!control) return false;

    var root = document.querySelector('[data-trip-share-revoke-url]');
    var url = revokeUrlFor(
      root ? root.getAttribute('data-trip-share-revoke-url') : '',
      control.getAttribute('data-trip-share-revoke')
    );
    if (!url) return true;

    fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
    })
      .then(function (resp) {
        if (!resp.ok) return Promise.reject(resp);
        window.location.reload();
        return null;
      })
      .catch(function () {
        announce(STRINGS['revoke-failed'], 'error');
      });

    return true;
  }

  self.pwaTripShareCore = Object.freeze({
    shareUrlFor: shareUrlFor,
    revokeUrlFor: revokeUrlFor,
  });

  if (typeof document !== 'undefined') {
    document.addEventListener('click', function (event) {
      if (handleShareClick(event)) return;
      handleRevokeClick(event);
    });
  }
})();
