/*
 * static/js/trip_share.js — the organiser's Share and Stop sharing controls
 * on a trip page (SNOW-821).
 *
 * Two clicks, both delegated off `document` so neither needs a closure over
 * the rendered page. The uuid rides on each button, exactly as the route
 * row's own Share does.
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
 * page itself (`_trip_share_controls.html`), in the sentence under the
 * buttons, rather than in a `confirm()` — a modal on the primary action of
 * the surface would be asked and dismissed every time, which is how a
 * warning stops being read.
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

  /**
   * Put one line in the state paragraph under the buttons.
   *
   * The page has no toast host, and adding one for two messages would be a
   * second notification system on a page that has none. The paragraph is
   * already the thing that describes the link's state, so it is where a
   * failure to change that state belongs.
   *
   * @param {string} message
   * @returns {void}
   */
  function announce(message) {
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
        if (outcome === 'copied') announce(STRINGS['share-copied']);
        else if (outcome === 'failed') announce(STRINGS['share-failed']);
      })
      .catch(function () {
        announce(STRINGS['share-failed']);
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
        announce(STRINGS['revoke-failed']);
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
