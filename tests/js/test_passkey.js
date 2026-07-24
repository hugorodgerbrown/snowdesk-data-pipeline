/*
 * tests/js/test_passkey.js — Vitest unit tests for static/js/passkey.js
 * (SNOW-515).
 *
 * Covers the conditional/explicit sign-in ceremony lifecycle added to fix
 * the passkey-provider cascade on sign-in page load: conditional UI must
 * never overlap an explicit ceremony, aborting a conditional ceremony must
 * work even mid-setup (before navigator.credentials.get() is called), and
 * neither ceremony can be started twice concurrently.
 *
 * `PublicKeyCredential` and `navigator.credentials` don't exist in jsdom, so
 * both are stubbed per-test (module-graph-per-file caveat — see
 * test_telemetry.js's header). `fetch` is stubbed per-test too, following
 * the same pattern as test_telemetry.js / test_mutation_queue.js.
 *
 * passkey.js memoises `_conditionalController` / `_explicitInFlight` at
 * module scope for the life of this file's one static import, so every test
 * below either drives its ceremony to completion (resolving/rejecting every
 * mocked `get()` call) or relies on `afterEach`'s
 * `abortConditionalSignIn()` to reset the conditional half of that state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/passkey.js';

const AUTH_REQUEST_URL = '/account/passkey/auth/request/';
const AUTH_RESPONSE_URL = '/account/passkey/auth/response/';

/**
 * Build a promise plus its resolve/reject functions, so a test can control
 * exactly when a mocked async call settles.
 *
 * @returns {{promise: Promise, resolve: Function, reject: Function}}
 */
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Flush a run of microtasks so pending `await` chains inside passkey.js
 * have a chance to advance up to their next unresolved promise.
 *
 * @param {number} [times]
 */
async function flush(times = 10) {
  for (let i = 0; i < times; i += 1) {
    await Promise.resolve();
  }
}

/**
 * Build an Error shaped like the DOMException a real WebAuthn
 * implementation raises when a ceremony is aborted or dismissed.
 *
 * @param {string} name
 * @returns {Error}
 */
function webauthnError(name) {
  const err = new Error(name);
  err.name = name;
  return err;
}

beforeEach(() => {
  vi.stubGlobal('PublicKeyCredential', {
    isConditionalMediationAvailable: vi.fn().mockResolvedValue(true),
    parseRequestOptionsFromJSON: vi.fn((x) => x),
  });
  Object.defineProperty(navigator, 'credentials', {
    value: { get: vi.fn() },
    configurable: true,
  });
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  window.Passkey.abortConditionalSignIn();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// 1. Abort before the options fetch resolves.
// ---------------------------------------------------------------------------

describe('startConditionalSignIn() aborted before the options fetch resolves', () => {
  it('never calls get(), and the fetch it made carries a now-aborted signal', async () => {
    fetch.mockReturnValue(new Promise(() => {})); // never resolves

    window.Passkey.startConditionalSignIn(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush(); // let the ceremony reach the options fetch call

    window.Passkey.abortConditionalSignIn();
    await flush();

    expect(navigator.credentials.get).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [, init] = fetch.mock.calls[0];
    expect(init.signal.aborted).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. Abort while the options fetch is in flight (controller existed eagerly).
// ---------------------------------------------------------------------------

describe('startConditionalSignIn() aborted while the options fetch is in flight', () => {
  it('does not call get() even if the in-flight fetch later settles', async () => {
    const fetchDeferred = deferred();
    fetch.mockReturnValue(fetchDeferred.promise);

    window.Passkey.startConditionalSignIn(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush();

    window.Passkey.abortConditionalSignIn();

    // The abort landed while the fetch was in flight; the mock isn't wired
    // to the AbortSignal, so simulate a fetch that settles anyway — the
    // post-fetch controller check must still catch it.
    fetchDeferred.resolve({ ok: true, json: async () => ({}) });
    await flush();

    expect(navigator.credentials.get).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 3. Concurrent conditional starts are rejected.
// ---------------------------------------------------------------------------

describe('startConditionalSignIn() called twice concurrently', () => {
  it('no-ops on the second call; fetch/get run at most once for the pair', async () => {
    fetch.mockReturnValue(new Promise(() => {})); // keep the first ceremony pending

    window.Passkey.startConditionalSignIn(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    window.Passkey.startConditionalSignIn(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(navigator.credentials.get).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 4. Concurrent explicit sign-ins are rejected.
// ---------------------------------------------------------------------------

describe('signInWithPasskey() called twice concurrently', () => {
  it('no-ops on the second call while the first is mid-ceremony', async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => ({}) });
    const getDeferred = deferred();
    navigator.credentials.get.mockReturnValue(getDeferred.promise);

    const first = window.Passkey.signInWithPasskey(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush();

    const second = window.Passkey.signInWithPasskey(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush();

    expect(navigator.credentials.get).toHaveBeenCalledTimes(1);

    getDeferred.resolve(null); // let the first ceremony settle and clear _explicitInFlight
    await Promise.all([first, second]);
  });
});

// ---------------------------------------------------------------------------
// 5. An explicit sign-in aborts a pending conditional ceremony.
// ---------------------------------------------------------------------------

describe('signInWithPasskey() vs a pending conditional ceremony', () => {
  it('aborts the conditional signal and proceeds with mediation:"required"', async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => ({}) });
    const conditionalGetDeferred = deferred();
    const explicitGetDeferred = deferred();
    navigator.credentials.get.mockImplementation((opts) =>
      opts.mediation === 'conditional' ? conditionalGetDeferred.promise : explicitGetDeferred.promise
    );

    const conditionalPromise = window.Passkey.startConditionalSignIn(
      AUTH_REQUEST_URL,
      AUTH_RESPONSE_URL
    );
    await flush(); // let the conditional ceremony reach navigator.credentials.get()

    expect(navigator.credentials.get).toHaveBeenCalledTimes(1);
    const conditionalCallArgs = navigator.credentials.get.mock.calls[0][0];
    expect(conditionalCallArgs.signal.aborted).toBe(false);

    const explicitPromise = window.Passkey.signInWithPasskey(AUTH_REQUEST_URL, AUTH_RESPONSE_URL);
    await flush();

    expect(conditionalCallArgs.signal.aborted).toBe(true);
    expect(navigator.credentials.get).toHaveBeenCalledTimes(2);
    const explicitCallArgs = navigator.credentials.get.mock.calls[1][0];
    expect(explicitCallArgs.mediation).toBe('required');

    conditionalGetDeferred.reject(webauthnError('AbortError'));
    explicitGetDeferred.resolve(null);
    await Promise.allSettled([conditionalPromise, explicitPromise]);
  });
});
