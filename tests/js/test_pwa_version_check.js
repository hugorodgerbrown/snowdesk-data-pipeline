/*
 * tests/js/test_pwa_version_check.js — Vitest unit tests for
 * static/js/pwa_version_check.js (SNOW-618).
 *
 * The forced-update gate was the largest untested decision in the
 * codebase: it can open an undismissable modal and wipe every cache,
 * service worker and IndexedDB database on the device. Finding M12 ranked
 * it first by risk among the seventeen untested modules, and SNOW-609 is
 * about to change how its floor is evaluated — so this net lands first.
 *
 * What the module actually decides
 * --------------------------------
 * The two version headers ride on every response, INCLUDING ones replayed
 * from the browser HTTP cache or the SW's stale-while-revalidate cache
 * with pre-deploy values. So a header drift is only a hint: it schedules
 * one authoritative ``fetch('/api/version', {cache: 'no-store'})`` and the
 * BODY decides. Most of the cases below are about that distinction, which
 * is the part a reader would most easily get wrong.
 *
 * Load-order constraint, and why every test re-imports
 * ----------------------------------------------------
 * The module is an IIFE holding one-way latches in module scope —
 * ``forcedUpdateTriggered``, ``modalTelemetryEmitted``, and the
 * ``staleConfirmed`` / ``driftConfirmed`` memos. None of them is
 * resettable from outside, so a single import shared across the file would
 * make every test after the first depend on the ones before it: open the
 * modal once and the gate is latched for the rest of the run.
 *
 * ``loadModule()`` therefore calls ``vi.resetModules()`` and re-imports,
 * giving each test its own latches. That also re-runs the two things the
 * module does at import time — capturing ``pristineFetch`` before wrapping
 * ``window.fetch``, and the cold-launch escalation check — so the fetch
 * stub and any ``localStorage`` stamp must be in place BEFORE the call.
 * ``CURRENT_BUILD`` comes from ``<meta name="pwa-app-version">``, read at
 * import time; the module returns early without it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const CURRENT_BUILD = '2026.08.01';
const NEWER_BUILD = '2026.08.02';
const FIRST_SHOWN_KEY = 'pwa.update.first_shown_at';

document.head.innerHTML = `<meta name="pwa-app-version" content="${CURRENT_BUILD}">`;
document.body.innerHTML = `
  <div id="sw-update-banner" class="hidden"></div>
  <div id="pwa-update-modal" class="hidden">
    <button id="pwa-update-modal-reload" type="button">Reload now</button>
  </div>
`;

/** Every ``/api/version`` body the stub has been told to answer with. */
let versionBody = null;
/** Set when the stub should fail the verification round trip outright. */
let versionUnreachable = false;
/** One entry per ``/api/version`` request the module issued. */
let versionCalls = [];

/**
 * The fetch every test drives.
 *
 * Installed BEFORE the import so it is captured as ``pristineFetch``, and
 * so the module's own wrapper delegates to it. ``/api/version`` is
 * answered from the mutable state above; anything else gets an ordinary
 * response carrying whatever version headers the test asked for.
 */
const baseFetch = vi.fn(async (url, _init) => {
  if (String(url).startsWith('/api/version')) {
    versionCalls.push(String(url));
    if (versionUnreachable) throw new TypeError('Failed to fetch');
    return new Response(JSON.stringify(versionBody), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  return new Response('{}', { status: 200, headers: responseHeaders });
});

/** Headers the non-version stub response carries. Rewritten per test. */
let responseHeaders = {};

/**
 * Stands in for ``pwa_reset.js``'s wipe, which ``resetAndReload``
 * delegates to (SNOW-615).
 *
 * Stubbed rather than left absent for two reasons: the real fallback path
 * calls ``location.reload()``, which jsdom cannot do and reports as an
 * unhandled "navigation not implemented" error on every forced-update
 * case; and the wipe firing is itself worth asserting, since it is the
 * destructive half of this gate.
 */
let resetLocalData = null;

/**
 * Stands in for `sw_register.js`'s `window.pwaUpdateBanner` (SNOW-623).
 *
 * The soft banner has one owner now, and this module reaches it through
 * that export rather than toggling the element itself. The stub does the
 * real class toggle so the `bannerShowing()` assertions below still test
 * the user-visible outcome, and records the call so the delegation itself
 * can be asserted.
 */
let revealBanner = null;

/** The module's wrapped fetch, replaced on every ``loadModule()``. */
let wrappedFetch = null;

/**
 * Load a fresh copy of the module, with fresh latches.
 *
 * Restores the pristine stub first so the module captures IT as
 * ``pristineFetch`` (the path every ``/api/version`` verification takes)
 * and wraps it as ``window.fetch``.
 *
 * @returns {Promise<void>}
 */
async function loadModule() {
  vi.resetModules();
  window.fetch = baseFetch;
  await import('../../static/js/pwa_version_check.js');
  wrappedFetch = window.fetch;
}

/**
 * Drive one response through the version check.
 *
 * @param {{version?: string, min?: string}} headers Values for
 *   ``X-App-Version`` / ``X-App-Min-Version``.
 * @returns {Promise<Response>} The response the caller receives.
 */
async function respondWith({ version, min } = {}) {
  responseHeaders = {};
  if (version) responseHeaders['X-App-Version'] = version;
  if (min) responseHeaders['X-App-Min-Version'] = min;
  return wrappedFetch('/api/anything');
}

/** Let the fire-and-forget verification promise settle. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** @returns {boolean} */
function bannerShowing() {
  return !document.getElementById('sw-update-banner').classList.contains('hidden');
}

/** @returns {boolean} */
function modalShowing() {
  return !document.getElementById('pwa-update-modal').classList.contains('hidden');
}

beforeEach(async () => {
  versionCalls = [];
  versionUnreachable = false;
  versionBody = { current: CURRENT_BUILD, min_supported: '' };
  document.getElementById('sw-update-banner').classList.add('hidden');
  document.getElementById('pwa-update-modal').classList.add('hidden');
  document.documentElement.style.overflow = '';
  const reload = document.getElementById('pwa-update-modal-reload');
  delete reload.dataset.bound;
  localStorage.removeItem(FIRST_SHOWN_KEY);
  baseFetch.mockClear();
  resetLocalData = vi.fn(async () => {});
  window.pwaResetLocalData = resetLocalData;
  revealBanner = vi.fn(() => {
    document.getElementById('sw-update-banner').classList.remove('hidden');
  });
  window.pwaUpdateBanner = { reveal: revealBanner, hide: vi.fn() };
  await loadModule();
  // The import's own cold-launch escalation check runs with no stamp in
  // place, so it is a no-op here — but clear the counter anyway, since a
  // test asserting on round-trip counts must only see its own.
  versionCalls = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaResetLocalData;
  delete window.pwaUpdateBanner;
});

describe('the fetch wrapper', () => {
  it('returns the original response unchanged', async () => {
    const response = await respondWith({ version: CURRENT_BUILD });

    // A passthrough: the version check is a side effect and must never
    // alter or swallow what the caller asked for.
    expect(response.ok).toBe(true);
    expect(await response.text()).toBe('{}');
  });

  it('ignores a response whose version matches the shell', async () => {
    await respondWith({ version: CURRENT_BUILD });
    await settle();

    // No drift, so nothing to verify — the round trip is the expensive
    // part and must not fire on the common case.
    expect(versionCalls).toHaveLength(0);
    expect(bannerShowing()).toBe(false);
  });

  it('ignores a response carrying no version headers at all', async () => {
    await respondWith({});
    await settle();

    expect(versionCalls).toHaveLength(0);
  });
});

describe('a drifting header is a hint, not a verdict', () => {
  it('verifies against /api/version rather than acting on the header', async () => {
    versionBody = { current: CURRENT_BUILD, min_supported: '' };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // This is the staging stuck-banner bug: the header was replayed from a
    // pre-deploy cache entry, the server says we are current, and no
    // banner should appear.
    expect(versionCalls).toHaveLength(1);
    expect(bannerShowing()).toBe(false);
    expect(modalShowing()).toBe(false);
  });

  it('does not re-verify a header the server has already disowned', async () => {
    await respondWith({ version: NEWER_BUILD });
    await settle();
    expect(versionCalls).toHaveLength(1);

    // The same cached response replays. Without the memo this is a round
    // trip per replay, on feeds cached for up to an hour.
    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(versionCalls).toHaveLength(1);
  });

  it('clears a stale first-shown stamp when the server says we are current', async () => {
    localStorage.setItem(FIRST_SHOWN_KEY, String(Date.now() - 1000));

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // A stamp planted by a phantom banner would otherwise escalate to the
    // blocking modal 24h later, on evidence the server has just denied.
    expect(localStorage.getItem(FIRST_SHOWN_KEY)).toBeNull();
  });

  it('shows the soft banner and stamps it when the drift is confirmed', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: '' };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(bannerShowing()).toBe(true);
    expect(modalShowing()).toBe(false);
    expect(Number(localStorage.getItem(FIRST_SHOWN_KEY))).toBeGreaterThan(0);
    // SNOW-623: through sw_register.js's export, not a second copy of the
    // reveal in this file.
    expect(revealBanner).toHaveBeenCalled();
  });

  it('does nothing when the banner owner has not loaded', async () => {
    delete window.pwaUpdateBanner;
    versionBody = { current: NEWER_BUILD, min_supported: '' };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // No banner and, importantly, no stamp — a first-shown timestamp with
    // no banner behind it would escalate to the blocking modal 24h later
    // for something the user was never shown.
    expect(bannerShowing()).toBe(false);
    expect(localStorage.getItem(FIRST_SHOWN_KEY)).toBeNull();
  });

  it('keeps a confirmed drift sticky without another round trip', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: '' };
    await respondWith({ version: NEWER_BUILD });
    await settle();
    expect(versionCalls).toHaveLength(1);

    document.getElementById('sw-update-banner').classList.add('hidden');
    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(versionCalls).toHaveLength(1);
    expect(bannerShowing()).toBe(true);
  });

  it('does nothing at all when /api/version is unreachable', async () => {
    versionUnreachable = true;

    await respondWith({ version: NEWER_BUILD, min: NEWER_BUILD });
    await settle();

    // "Cannot confirm" must never read as "confirmed" — a blocking modal
    // on unverifiable evidence would strand every offline user.
    expect(bannerShowing()).toBe(false);
    expect(modalShowing()).toBe(false);
    expect(localStorage.getItem(FIRST_SHOWN_KEY)).toBeNull();
  });

  it('treats an empty /api/version body as "cannot confirm"', async () => {
    versionBody = {};

    await respondWith({ version: NEWER_BUILD, min: NEWER_BUILD });
    await settle();

    // Neither field present, so nothing drifts and nothing is claimed —
    // the same fail-safe direction as an unreachable endpoint.
    expect(modalShowing()).toBe(false);
    expect(bannerShowing()).toBe(false);
  });

  it('shares one round trip between concurrent observations', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: '' };

    await Promise.all([
      respondWith({ version: NEWER_BUILD }),
      respondWith({ version: NEWER_BUILD }),
      respondWith({ version: NEWER_BUILD }),
    ]);
    await settle();

    expect(versionCalls).toHaveLength(1);
  });
});

describe('the forced-update gate', () => {
  it('opens the blocking modal on a confirmed min-version floor', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: NEWER_BUILD };

    await respondWith({ min: NEWER_BUILD });
    await settle();

    expect(modalShowing()).toBe(true);
    // Spec §3.4: the modal locks the page. Scrolling underneath it would
    // suggest the app is still usable.
    expect(document.documentElement.style.overflow).toBe('hidden');
    // The destructive half — SW, Cache Storage, IndexedDB and web storage
    // all go, before the user has clicked anything.
    expect(resetLocalData).toHaveBeenCalledTimes(1);
  });

  it('does not wipe anything when no floor is confirmed', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: '' };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // A soft update is an invitation, not a reset. Wiping here would
    // destroy queued mutations and cached basemaps for a banner.
    expect(resetLocalData).not.toHaveBeenCalled();
  });

  it('emits pwa.forced_update.triggered exactly once', async () => {
    const emit = vi.fn();
    window.pwaTelemetry = { emit };
    versionBody = { current: NEWER_BUILD, min_supported: NEWER_BUILD };

    await respondWith({ min: NEWER_BUILD });
    await settle();
    await respondWith({ min: NEWER_BUILD });
    await settle();

    const forced = emit.mock.calls.filter((c) => c[0] === 'pwa.forced_update.triggered');
    expect(forced).toHaveLength(1);
    expect(forced[0][1]).toEqual({ trigger: 'min_version' });
    delete window.pwaTelemetry;
  });

  it('latches, so later responses cannot re-run the flow', async () => {
    versionBody = { current: NEWER_BUILD, min_supported: NEWER_BUILD };
    await respondWith({ min: NEWER_BUILD });
    await settle();
    const callsAfterFirst = versionCalls.length;

    await respondWith({ version: 'something-else-entirely' });
    await settle();

    expect(versionCalls).toHaveLength(callsAfterFirst);
  });

  it('does not force an update on a min-version the server does not confirm', async () => {
    // The header claims a floor; the body does not. The body wins — this
    // is the whole point of verifying, and getting it backwards would
    // wipe local data on a cache artefact.
    versionBody = { current: CURRENT_BUILD, min_supported: '' };

    await respondWith({ min: NEWER_BUILD });
    await settle();

    expect(modalShowing()).toBe(false);
  });
});
