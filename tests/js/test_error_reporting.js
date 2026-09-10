/*
 * tests/js/test_error_reporting.js — uncaught errors reach telemetry, and
 * an opted-out visitor's report carries nothing about the fault (SNOW-894).
 *
 * Three things this file exists to hold:
 *
 *   1. Both fault kinds are reported at all. Before SNOW-894 nothing in
 *      static/js registered either listener.
 *   2. The opt-out payload is REDUCED, not absent. `js.error` is a critical
 *      event, so it fires either way; the reduced payload is the entire
 *      reason firing-either-way is acceptable. A regression that widened
 *      it back would be silent and would collect diagnostics from people
 *      who declined — so it is asserted field by field, not by a shape
 *      match that a new key would slip through.
 *   3. No query string, ever. The map's URLs carry `?route_share=<token>`
 *      and `?trip_share=<token>`; a share token is a capability, and this
 *      is the assertion standing between one and a third-party analytics
 *      system.
 *
 * The dedupe is tested because it is load-bearing rather than tidy: a throw
 * inside a MapLibre `moveend` handler fires on every frame of a pan.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Point the module at a fresh fake `pwaTelemetry` and clear its dedupe state.
 *
 * The module is imported ONCE for the whole file, deliberately. Its
 * listeners are attached to `window`, and jsdom shares one `window` across
 * every test here — so re-importing per test would leave the previous
 * evaluation's listeners attached and each fault would be reported once per
 * import. (The module now refuses to register twice for the production
 * version of that same problem, so a re-import would be a no-op regardless.)
 *
 * Swapping `window.pwaTelemetry` works because the handlers read it at call
 * time rather than capturing it at load.
 */
let imported = false;
async function load({ optIn = true, isOptInThrows = false } = {}) {
  const emit = vi.fn();
  window.pwaTelemetry = {
    emit,
    isOptIn: isOptInThrows
      ? () => { throw new Error('IndexedDB is gone'); }
      : () => Promise.resolve(optIn),
  };
  if (!imported) {
    await import('../../static/js/error_reporting.js');
    imported = true;
  }
  window.pwaErrorReporting._reset();
  return emit;
}

/** Dispatch a window 'error' event shaped the way the browser shapes one. */
function throwAt(message, filename = '/static/js/map.js', lineno = 718) {
  const event = new Event('error');
  Object.assign(event, {
    message,
    filename,
    lineno,
    colno: 12,
    error: Object.assign(new Error(message), { stack: `Error: ${message}\n  at boot` }),
  });
  window.dispatchEvent(event);
}

/** Dispatch an unhandledrejection with an arbitrary reason. */
function rejectWith(reason) {
  const event = new Event('unhandledrejection');
  Object.assign(event, { reason });
  window.dispatchEvent(event);
}

/** Let the isOptIn() promise and the .then() chain settle. */
const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  window.history.replaceState({}, '', '/');
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
});

describe('an opted-in visitor', () => {
  it('reports an uncaught exception with the detail needed to find it', async () => {
    const emit = await load({ optIn: true });

    throwAt('Failed to initialize WebGL');
    await settle();

    expect(emit).toHaveBeenCalledTimes(1);
    const [name, props] = emit.mock.calls[0];
    expect(name).toBe('js.error');
    expect(props).toMatchObject({
      kind: 'error',
      pathname: '/',
      message: 'Failed to initialize WebGL',
      filename: '/static/js/map.js',
      lineno: 718,
      colno: 12,
    });
    expect(props.stack).toContain('at boot');
  });

  it('reports an unhandled rejection, including one rejected with a non-Error', async () => {
    const emit = await load({ optIn: true });

    rejectWith(new Error('fetch failed'));
    rejectWith('a bare string, which carries no stack');
    await settle();

    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit.mock.calls[0][1]).toMatchObject({
      kind: 'unhandledrejection', message: 'fetch failed',
    });
    // A promise can be rejected with anything. The string one still
    // reports; it simply has no stack to send.
    expect(emit.mock.calls[1][1]).toMatchObject({
      kind: 'unhandledrejection',
      message: 'a bare string, which carries no stack',
      stack: '',
    });
  });
});

describe('an opted-out visitor', () => {
  it('reports that a fault happened and nothing whatever about it', async () => {
    const emit = await load({ optIn: false });

    throwAt('ReferenceError: MAP is not defined');
    await settle();

    // Fired at all — js.error is critical, so opting out does not silence
    // it. That is the trade this reduced payload pays for.
    expect(emit).toHaveBeenCalledTimes(1);
    const [, props] = emit.mock.calls[0];

    // Asserted as an exact key set, not a subset: a future field added to
    // the opted-in branch and forgotten here would otherwise ship
    // diagnostics from someone who declined, and no test would notice.
    expect(Object.keys(props).sort()).toEqual(['kind', 'pathname']);
    expect(props).toEqual({ kind: 'error', pathname: '/' });
  });

  it('is the branch taken when the opt-in lookup itself fails', async () => {
    // A broken or reset IndexedDB must not be read as consent.
    const emit = await load({ isOptInThrows: true });

    throwAt('boom');
    await settle();

    expect(emit.mock.calls[0][1]).toEqual({ kind: 'error', pathname: '/' });
  });
});

describe('the payload', () => {
  it('never carries the query string, on either branch', async () => {
    // The live case: a visitor arriving on a shared route or trip link.
    window.history.replaceState({}, '', '/?route_share=SECRET-TOKEN&trip_share=OTHER');

    for (const optIn of [true, false]) {
      const emit = await load({ optIn });
      throwAt(`crash ${optIn}`);
      await settle();

      const serialised = JSON.stringify(emit.mock.calls[0][1]);
      expect(serialised).not.toContain('SECRET-TOKEN');
      expect(serialised).not.toContain('OTHER');
      expect(serialised).not.toContain('route_share');
      expect(emit.mock.calls[0][1].pathname).toBe('/');
    }
  });

  it('clamps a runaway stack so one fault cannot fill the beacon', async () => {
    const emit = await load({ optIn: true });

    const event = new Event('error');
    Object.assign(event, {
      message: 'recursion',
      filename: '/static/js/map.js',
      lineno: 1,
      colno: 1,
      error: { stack: 'x'.repeat(50000) },
    });
    window.dispatchEvent(event);
    await settle();

    expect(emit.mock.calls[0][1].stack.length).toBe(2000);
  });
});

describe('the dedupe', () => {
  it('caps repeats of one fault, because a pan fires hundreds', async () => {
    const emit = await load({ optIn: true });

    for (let i = 0; i < 200; i += 1) throwAt('moveend handler threw');
    await settle();

    expect(emit).toHaveBeenCalledTimes(window.pwaErrorReporting.MAX_PER_KEY);
  });

  it('counts distinct faults separately', async () => {
    const emit = await load({ optIn: true });

    // Same message, different line: a different fault, and reported.
    throwAt('same message', '/static/js/map.js', 100);
    throwAt('same message', '/static/js/map.js', 200);
    await settle();

    expect(emit).toHaveBeenCalledTimes(2);
  });

  it('stops tracking new faults past the key cap, rather than growing forever', async () => {
    const emit = await load({ optIn: true });
    const { MAX_KEYS } = window.pwaErrorReporting;

    for (let i = 0; i < MAX_KEYS; i += 1) throwAt('fault', '/static/js/map.js', i);
    throwAt('one too many', '/static/js/map.js', 9999);
    await settle();

    expect(emit).toHaveBeenCalledTimes(MAX_KEYS);
  });
});

describe('the reporter itself', () => {
  it('does nothing, and throws nothing, when telemetry is absent', async () => {
    await load({ optIn: true });
    delete window.pwaTelemetry;

    // A reporter that throws inside an error handler turns one fault into
    // a loop.
    expect(() => throwAt('no telemetry on this page')).not.toThrow();
  });

  it('ignores a failed resource load, which is a different signal', async () => {
    const emit = await load({ optIn: true });

    // A broken <img>/<script> fires 'error' on window during capture but
    // sets no `message`, and is not an exception.
    window.dispatchEvent(new Event('error'));
    await settle();

    expect(emit).not.toHaveBeenCalled();
  });
});
