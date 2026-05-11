/*
 * static/js/passkey.js — WebAuthn / passkey browser-side logic.
 *
 * Exposes window.Passkey with three entry points:
 *
 *   startConditionalSignIn(authRequestUrl, authResponseUrl)
 *     Feature-detects conditional mediation, fires navigator.credentials.get()
 *     with mediation:"conditional" so passkeys appear inline in the email
 *     field autofill dropdown.  Silently no-ops if the browser does not
 *     support conditional UI.
 *
 *   abortConditionalSignIn()
 *     Cancels a pending conditional sign-in (e.g. when the user starts typing
 *     an email address to use the magic-link flow instead).
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

  /** @type {AbortController|null} */
  let _conditionalController = null;

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
   * On success the user is redirected to /subscribe/manage/.
   *
   * @param {string} authRequestUrl  URL to GET authentication options from.
   * @param {string} authResponseUrl URL to POST the credential to.
   */
  async function startConditionalSignIn(authRequestUrl, authResponseUrl) {
    if (!_supportsConditionalUI()) return;

    const available = await PublicKeyCredential.isConditionalMediationAvailable().catch(
      () => false
    );
    if (!available) return;

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

    _conditionalController = new AbortController();

    let credential;
    try {
      credential = await navigator.credentials.get({
        publicKey: parsed,
        mediation: 'conditional',
        signal: _conditionalController.signal,
      });
    } catch (err) {
      if (err.name === 'AbortError' || err.name === 'NotAllowedError') return;
      console.error('[passkey] conditional sign-in error:', err);
      return;
    }

    await _sendAuthResponse(authResponseUrl, credential);
  }

  /**
   * Abort a pending conditional sign-in ceremony.
   *
   * Call this when the user switches to the magic-link flow (e.g. starts
   * typing in the email field) so the conditional prompt is dismissed.
   */
  function abortConditionalSignIn() {
    if (_conditionalController) {
      _conditionalController.abort();
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
   * challenge — only one can be active at a time).
   *
   * @param {string} authRequestUrl  URL to GET authentication options from.
   * @param {string} authResponseUrl URL to POST the credential to.
   */
  async function signInWithPasskey(authRequestUrl, authResponseUrl) {
    if (!window.PublicKeyCredential) return;

    abortConditionalSignIn();

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
   * POST an authentication credential to the server and redirect on success.
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
    try {
      resp = await fetch(authResponseUrl, {
        method: 'POST',
        headers: { ..._csrfHeaders(), 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(credential.toJSON()),
      });
      data = await resp.json();
    } catch (err) {
      console.error('[passkey] auth response error:', err);
      _dispatch('passkey:auth-error', { message: err.message });
      return;
    }

    if (resp.ok && data.ok) {
      window.location.href = '/subscribe/manage/';
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
