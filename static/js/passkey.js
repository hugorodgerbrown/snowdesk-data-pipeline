/*
 * static/js/passkey.js — WebAuthn / passkey browser-side logic.
 *
 * Exposes window.Passkey with three entry points:
 *
 *   startConditionalSignIn(authRequestUrl, authResponseUrl)
 *     Feature-detects conditional mediation, fires navigator.credentials.get()
 *     with mediation:"conditional" so passkeys appear inline in the email
 *     field autofill dropdown.  Silently no-ops if the browser does not
 *     support conditional UI, if a conditional ceremony is already pending,
 *     or if an explicit ceremony (see signInWithPasskey) is in flight — the
 *     two ceremonies share a session challenge and must never overlap.
 *
 *   abortConditionalSignIn()
 *     Cancels a pending conditional sign-in (e.g. when the user starts typing
 *     an email address to use the magic-link flow instead, or submits the
 *     form).
 *
 *   registerPasskey(regRequestUrl, regResponseUrl)
 *     Runs the full passkey registration ceremony and POSTs the credential
 *     to the server.  Dispatches custom events on document:
 *       passkey:registered  — {passkey: {uuid, name, device_type}}
 *       passkey:cancelled   — user dismissed the browser prompt
 *       passkey:unsupported — browser does not support WebAuthn
 *       passkey:error       — {message}
 *
 * CSRF: all POST requests include the X-CSRFToken header, read from the
 * standard Django csrftoken cookie.
 *
 * Signal API: if the server returns 404 for an auth response, and the browser
 * supports signalUnknownCredential, this script notifies the passkey provider
 * so it can remove the stale credential from autofill.
 */

(function () {
  'use strict';

  /** @type {AbortController|null} Non-null while a conditional ceremony is pending/active. */
  let _conditionalController = null;

  /** @type {boolean} True from an explicit ceremony's options-fetch through settle. */
  let _explicitInFlight = false;

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /**
   * Start WebAuthn conditional UI (passkey autofill) on the sign-in page.
   *
   * Fetches authentication options, then calls navigator.credentials.get()
   * with mediation:"conditional".  The browser surfaces matching passkeys
   * inline inside the email input's autofill dropdown without a modal.
   *
   * On success the user is redirected to the destination the server returned
   * (the sign-in page's `next`, SNOW-825), falling back to /account/.
   *
   * No-ops if the browser does not support conditional UI, if a conditional
   * ceremony is already pending, or if an explicit ceremony is in flight —
   * conditional and explicit ceremonies share a session challenge and must
   * never overlap.
   *
   * @param {string} authRequestUrl  URL to GET authentication options from.
   * @param {string} authResponseUrl URL to POST the credential to.
   */
  async function startConditionalSignIn(authRequestUrl, authResponseUrl) {
    if (!_supportsConditionalUI()) return;
    if (_conditionalController || _explicitInFlight) return;

    // Created eagerly, before any await, so abortConditionalSignIn() can
    // cancel this ceremony even mid-setup (e.g. while awaiting availability
    // or the options fetch).
    const controller = new AbortController();
    _conditionalController = controller;

    const available = await PublicKeyCredential.isConditionalMediationAvailable().catch(
      () => false
    );
    if (!available || controller.signal.aborted) {
      _finishConditional(controller);
      return;
    }

    let options;
    try {
      const resp = await fetch(authRequestUrl, { signal: controller.signal });
      if (!resp.ok) {
        _finishConditional(controller);
        return;
      }
      options = await resp.json();
    } catch {
      _finishConditional(controller);
      return;
    }

    // The abort may have landed while the fetch was in flight, or this
    // ceremony may have been superseded by a newer one — either way the
    // session challenge may have moved on, so bail without proceeding.
    if (controller.signal.aborted || _conditionalController !== controller) {
      _finishConditional(controller);
      return;
    }

    let parsed;
    try {
      parsed = PublicKeyCredential.parseRequestOptionsFromJSON(options);
    } catch {
      _finishConditional(controller);
      return;
    }

    let credential;
    try {
      credential = await navigator.credentials.get({
        publicKey: parsed,
        mediation: 'conditional',
        signal: controller.signal,
      });
    } catch (err) {
      if (err.name === 'AbortError' || err.name === 'NotAllowedError') {
        _finishConditional(controller);
        return;
      }
      console.error('[passkey] conditional sign-in error:', err);
      _finishConditional(controller);
      return;
    }

    _finishConditional(controller);
    await _sendAuthResponse(authResponseUrl, credential);
  }

  /**
   * Abort a pending conditional sign-in ceremony.
   *
   * Call this when the user switches to the magic-link flow (e.g. starts
   * typing in the email field), submits the form, or starts an explicit
   * passkey ceremony, so the conditional prompt is dismissed.
   */
  function abortConditionalSignIn() {
    if (_conditionalController) {
      _conditionalController.abort();
      _conditionalController = null;
    }
  }

  /**
   * Clear the module-level conditional controller if it still refers to
   * the ceremony that is finishing — never clobbers a newer controller
   * that may have been installed since (e.g. by a fresh
   * startConditionalSignIn() call).
   *
   * @param {AbortController} controller
   */
  function _finishConditional(controller) {
    if (_conditionalController === controller) {
      _conditionalController = null;
    }
  }

  /**
   * Explicitly sign in with a passkey by showing the browser's passkey picker.
   *
   * Unlike startConditionalSignIn, this triggers an immediate modal prompt so
   * the user can pick a passkey to use.  Intended for an explicit "Sign in
   * with a passkey" button rather than autofill.
   *
   * Aborts any pending conditional sign-in first (the two flows share a session
   * challenge — only one can be active at a time), and no-ops if another
   * explicit ceremony is already in flight.
   *
   * @param {string} authRequestUrl  URL to GET authentication options from.
   * @param {string} authResponseUrl URL to POST the credential to.
   */
  async function signInWithPasskey(authRequestUrl, authResponseUrl) {
    if (!window.PublicKeyCredential) return;
    if (_explicitInFlight) return;

    abortConditionalSignIn();

    _explicitInFlight = true;
    try {
      let options;
      try {
        const resp = await fetch(authRequestUrl);
        if (!resp.ok) return;
        options = await resp.json();
      } catch {
        return;
      }

      let parsed;
      try {
        parsed = PublicKeyCredential.parseRequestOptionsFromJSON(options);
      } catch {
        return;
      }

      let credential;
      try {
        credential = await navigator.credentials.get({
          publicKey: parsed,
          mediation: 'required',
        });
      } catch (err) {
        if (err.name === 'NotAllowedError' || err.name === 'AbortError') return;
        console.error('[passkey] sign-in error:', err);
        return;
      }

      if (credential) {
        await _sendAuthResponse(authResponseUrl, credential);
      }
    } finally {
      _explicitInFlight = false;
    }
  }

  /**
   * Register a new passkey for the currently signed-in subscriber.
   *
   * Fetches creation options from the server, calls navigator.credentials.create(),
   * and POSTs the result back.  Dispatches document-level custom events to
   * allow the page to react (see module-level JSDoc for event names).
   *
   * @param {string} regRequestUrl  URL to GET registration options from.
   * @param {string} regResponseUrl URL to POST the new credential to.
   */
  async function registerPasskey(regRequestUrl, regResponseUrl) {
    if (!window.PublicKeyCredential) {
      _dispatch('passkey:unsupported');
      return;
    }

    let options;
    try {
      const resp = await fetch(regRequestUrl);
      if (!resp.ok) {
        _dispatch('passkey:error', { message: 'Failed to fetch registration options.' });
        return;
      }
      options = await resp.json();
    } catch (err) {
      _dispatch('passkey:error', { message: err.message });
      return;
    }

    let parsed;
    try {
      parsed = PublicKeyCredential.parseCreationOptionsFromJSON(options);
    } catch (err) {
      _dispatch('passkey:error', { message: 'Could not parse registration options.' });
      return;
    }

    let credential;
    try {
      credential = await navigator.credentials.create({ publicKey: parsed });
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        _dispatch('passkey:cancelled');
        return;
      }
      _dispatch('passkey:error', { message: err.message });
      return;
    }

    let result;
    try {
      const resp = await fetch(regResponseUrl, {
        method: 'POST',
        headers: { ..._csrfHeaders(), 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(credential.toJSON()),
      });
      result = await resp.json();
      if (!resp.ok) {
        _dispatch('passkey:error', { message: result.error || 'Registration failed.' });
        return;
      }
    } catch (err) {
      _dispatch('passkey:error', { message: err.message });
      return;
    }

    _dispatch('passkey:registered', result);
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  /**
   * Read the sign-in page's post-sign-in destination, if it has one.
   *
   * The sign-in form carries `data-next` when the page was reached with a
   * `?next=` the server accepted (SNOW-825). It is read from the DOM rather
   * than passed through the ceremony's arguments so both entry points — the
   * conditional autofill ceremony and the explicit button — get it without
   * either caller having to know about it.
   *
   * @returns {string} The destination, or '' when there is none.
   */
  function _nextDestination() {
    const form = document.querySelector('form[data-next]');
    return (form && form.getAttribute('data-next')) || '';
  }

  /**
   * POST an authentication credential to the server and redirect on success.
   *
   * The body is the credential plus, when the page has one, the `next`
   * destination. The server re-validates `next` and echoes back either a
   * same-site URL or null, so the navigation below can never be pointed off
   * this origin by the value this function sent.
   *
   * When the server returns 404 (unknown credential), calls
   * PublicKeyCredential.signalUnknownCredential() if available so the passkey
   * provider can remove the stale entry.
   *
   * @param {string}             authResponseUrl
   * @param {PublicKeyCredential} credential
   */
  async function _sendAuthResponse(authResponseUrl, credential) {
    let resp, data;
    const payload = credential.toJSON();
    const next = _nextDestination();
    if (next) payload.next = next;
    try {
      resp = await fetch(authResponseUrl, {
        method: 'POST',
        headers: { ..._csrfHeaders(), 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      data = await resp.json();
    } catch (err) {
      console.error('[passkey] auth response error:', err);
      _dispatch('passkey:auth-error', { message: err.message });
      return;
    }

    if (resp.ok && data.ok) {
      window.location.href = data.next || '/account/';
      return;
    }

    if (resp.status === 404 && data.credentialId) {
      if (typeof PublicKeyCredential.signalUnknownCredential === 'function') {
        try {
          await PublicKeyCredential.signalUnknownCredential({
            rpId: window.location.hostname,
            credentialId: data.credentialId,
          });
        } catch {
          /* progressive enhancement — ignore failures */
        }
      }
      _dispatch('passkey:auth-unknown-credential', { credentialId: data.credentialId });
      return;
    }

    _dispatch('passkey:auth-error', { message: data.error || 'Authentication failed.' });
  }

  /**
   * Return true if the browser supports conditional mediation.
   *
   * @returns {boolean}
   */
  function _supportsConditionalUI() {
    return (
      typeof window.PublicKeyCredential !== 'undefined' &&
      typeof PublicKeyCredential.isConditionalMediationAvailable === 'function'
    );
  }

  /**
   * Return an object with the X-CSRFToken header if a CSRF token is present.
   *
   * @returns {Record<string, string>}
   */
  function _csrfHeaders() {
    const token = _getCsrfToken();
    return token ? { 'X-CSRFToken': token } : {};
  }

  /**
   * Read the Django CSRF token from the csrftoken cookie.
   *
   * @returns {string}
   */
  function _getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  /**
   * Dispatch a CustomEvent on document.
   *
   * @param {string} name
   * @param {object} [detail]
   */
  function _dispatch(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail: detail || {}, bubbles: true }));
  }

  // ---------------------------------------------------------------------------
  // Exports
  // ---------------------------------------------------------------------------

  window.Passkey = {
    startConditionalSignIn,
    abortConditionalSignIn,
    signInWithPasskey,
    registerPasskey,
  };
})();
