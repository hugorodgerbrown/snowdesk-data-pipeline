/*
 * tests/js/test_pwa_version_check.js — Vitest unit tests for
 * static/js/pwa_version_check.js (SNOW-618, rewritten by SNOW-609).
 *
 * The forced-update gate was the largest untested decision in the
 * codebase: it can open an undismissable modal and, before SNOW-609, wiped
 * every cache, service worker and IndexedDB database on the device.
 * Finding M12 ranked it first by risk among the seventeen untested
 * modules; SNOW-618 landed the net, and SNOW-609 then changed what it
 * catches.
 *
 * What the module actually decides
 * --------------------------------
 * The version header rides on every response, INCLUDING ones replayed
 * from the browser HTTP cache or the SW's stale-while-revalidate cache
 * with pre-deploy values. So a header drift is only a hint: it schedules
 * one authoritative ``fetch('/api/version', {cache: 'no-store'})`` and the
 * BODY decides. Most of the cases below are about that distinction, which
 * is the part a reader would most easily get wrong.
 *
 * What SNOW-609 changed, and what is pinned here because of it
 * -----------------------------------------------------------
 *   1. The verdict is the server's. ``update_required: true`` opens the
 *      modal; ``false``, an absent field (a server predating the change)
 *      and an unreachable endpoint all open nothing. There is no version
 *      comparison left on the client to test — the ``min_supported`` floor
 *      and the ``X-App-Min-Version`` header are both gone.
 *   2. Nothing happens until the click. The reveal alone must not clear a
 *      cache or reload; the wipe used to fire under the modal, so the copy
 *      explaining what it cost was torn down before it could be read.
 *   3. The click clears the shell caches, not everything. It calls
 *      ``window.pwaClearShellCachesAndReload``, never
 *      ``window.pwaResetLocalData`` — and that function (last describe,
 *      the other half of the same contract) deletes only
 *      ``snowdesk-shell-*`` / ``map-shell-*``, so a user's pinned basemap
 *      buckets survive a code update.
 *   4. The 24h escalation is gone, along with the
 *      ``pwa.update.first_shown_at`` stamp that fed it.
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
 * giving each test its own latches. That also re-runs what the module does
 * at import time — capturing ``pristineFetch`` before wrapping
 * ``window.fetch`` — so the fetch stub must be in place BEFORE the call.
 * ``CURRENT_BUILD`` comes from ``<meta name="pwa-app-version">``, read at
 * import time; the module returns early without it.
 *
 * The last describe uses a different harness — a ``new Function`` sandbox,
 * as in tests/js/test_sw.js — because ``sw_register.js`` publishes its
 * exports with ``Object.defineProperty(window, …, {configurable: false})``
 * and a second import would throw "Cannot redefine property". Handing it a
 * plain object as ``window`` also makes ``location.reload`` observable,
 * which jsdom's own unforgeable one is not.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// SNOW-620: sw_register.js reads its banner copy through
// self.pwaStrings at IIFE time. The sandbox below injects window /
// document / navigator / caches / fetch as parameters, but `self`
// still resolves to the real global — so the helper has to be loaded
// onto it here or the source throws before it exports anything.
import '../../static/js/i18n_strings.js';

const CURRENT_BUILD = '2026.08.01';
const NEWER_BUILD = '2026.08.02';

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
 * response carrying whatever version header the test asked for.
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
 * Stands in for ``sw_register.js``'s ``window.pwaClearShellCachesAndReload``
 * — the shell-scoped wipe the modal's Reload button runs (SNOW-609).
 *
 * Stubbed rather than left absent because the real fallback path calls
 * ``location.reload()``, which jsdom cannot do and reports as an unhandled
 * "navigation not implemented" error; and because the wipe firing (or not
 * firing) is the half of this gate worth asserting.
 */
let clearShellCaches = null;

/**
 * Stands in for ``pwa_reset.js``'s everything-goes wipe.
 *
 * Present only so the tests can assert it is NEVER called. SNOW-615 routed
 * the forced update through it; SNOW-609 routed it back out, because a
 * blocked build is a code problem and the mutation queue, the favourites
 * roster and the pinned basemaps are not code.
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
 * @param {{version?: string}} headers Value for ``X-App-Version``. There is
 *   no second header any more — SNOW-609 removed ``X-App-Min-Version``.
 * @returns {Promise<Response>} The response the caller receives.
 */
async function respondWith({ version } = {}) {
  responseHeaders = {};
  if (version) responseHeaders['X-App-Version'] = version;
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
  versionBody = { current: CURRENT_BUILD, update_required: false };
  document.getElementById('sw-update-banner').classList.add('hidden');
  document.getElementById('pwa-update-modal').classList.add('hidden');
  document.documentElement.style.overflow = '';
  // Replace the Reload button rather than clearing ``bindModal``'s
  // ``data-bound`` flag: each ``loadModule()`` re-import binds a fresh
  // click listener, and clearing the flag alone lets them accumulate on
  // one node, so the click test would see every earlier test's handler
  // fire too. A new node carries no listeners.
  document.getElementById('pwa-update-modal').innerHTML =
    '<button id="pwa-update-modal-reload" type="button">Reload now</button>';
  baseFetch.mockClear();
  clearShellCaches = vi.fn(async () => {});
  window.pwaClearShellCachesAndReload = clearShellCaches;
  resetLocalData = vi.fn(async () => {});
  window.pwaResetLocalData = resetLocalData;
  revealBanner = vi.fn(() => {
    document.getElementById('sw-update-banner').classList.remove('hidden');
  });
  window.pwaUpdateBanner = { reveal: revealBanner, hide: vi.fn() };
  await loadModule();
  // A test asserting on round-trip counts must only see its own.
  versionCalls = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaClearShellCachesAndReload;
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

  it('ignores a response carrying no version header at all', async () => {
    await respondWith({});
    await settle();

    expect(versionCalls).toHaveLength(0);
  });
});

describe('a drifting header is a hint, not a verdict', () => {
  it('verifies against /api/version rather than acting on the header', async () => {
    versionBody = { current: CURRENT_BUILD, update_required: false };

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

  it('shows the soft banner when the drift is confirmed', async () => {
    versionBody = { current: NEWER_BUILD, update_required: false };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(bannerShowing()).toBe(true);
    expect(modalShowing()).toBe(false);
    // SNOW-623: through sw_register.js's export, not a second copy of the
    // reveal in this file.
    expect(revealBanner).toHaveBeenCalled();
  });

  it('hands the verdict to the banner so it can name the build on offer', async () => {
    // The banner's build line needs the server's own account of what it is
    // serving, and this path has just fetched it. Passing it through is
    // what saves the banner a second identical round trip.
    versionBody = {
      current: NEWER_BUILD,
      released_at: '2026-08-02T09:00:00+00:00',
      update_required: false,
    };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(revealBanner).toHaveBeenCalledWith(
      expect.objectContaining({
        current: NEWER_BUILD,
        released_at: '2026-08-02T09:00:00+00:00',
      }),
    );
  });

  it('re-reveals a sticky drift with the build line it already resolved', async () => {
    versionBody = {
      current: NEWER_BUILD,
      released_at: '2026-08-02T09:00:00+00:00',
      update_required: false,
    };
    await respondWith({ version: NEWER_BUILD });
    await settle();
    revealBanner.mockClear();

    // A replayed cached response re-confirms the same drift from memory.
    // It must not re-reveal with an empty verdict — that would clear a
    // build line the user is mid-way through reading.
    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(revealBanner).toHaveBeenCalledWith(
      expect.objectContaining({ current: NEWER_BUILD }),
    );
  });

  it('does nothing when the banner owner has not loaded', async () => {
    delete window.pwaUpdateBanner;
    versionBody = { current: NEWER_BUILD, update_required: false };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect(bannerShowing()).toBe(false);
  });

  it('keeps a confirmed drift sticky without another round trip', async () => {
    versionBody = { current: NEWER_BUILD, update_required: false };
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

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // "Cannot confirm" must never read as "confirmed" — a blocking modal
    // on unverifiable evidence would strand every offline user.
    expect(bannerShowing()).toBe(false);
    expect(modalShowing()).toBe(false);
  });

  it('treats an empty /api/version body as "cannot confirm"', async () => {
    versionBody = {};

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // Neither field present, so nothing drifts and nothing is claimed —
    // the same fail-safe direction as an unreachable endpoint.
    expect(modalShowing()).toBe(false);
    expect(bannerShowing()).toBe(false);
  });

  it('shares one round trip between concurrent observations', async () => {
    versionBody = { current: NEWER_BUILD, update_required: false };

    await Promise.all([
      respondWith({ version: NEWER_BUILD }),
      respondWith({ version: NEWER_BUILD }),
      respondWith({ version: NEWER_BUILD }),
    ]);
    await settle();

    expect(versionCalls).toHaveLength(1);
  });
});

describe('the forced-update gate is the server verdict (SNOW-609)', () => {
  // The whole verdict table. Note there is no row for "the shell's build
  // is older/newer than the server's": that comparison no longer exists.
  const cases = [
    {
      name: 'update_required true opens the blocking modal',
      body: { current: NEWER_BUILD, update_required: true },
      expected: { modal: true, banner: false },
    },
    {
      name: 'update_required false with a drifted current shows the soft banner',
      body: { current: NEWER_BUILD, update_required: false },
      expected: { modal: false, banner: true },
    },
    {
      name: 'an absent update_required field reads as not blocked',
      body: { current: NEWER_BUILD },
      expected: { modal: false, banner: true },
    },
    {
      name: 'a body matching the shell reveals nothing (stale cached header)',
      body: { current: CURRENT_BUILD, update_required: false },
      expected: { modal: false, banner: false },
    },
  ];

  it.each(cases)('$name', async ({ body, expected }) => {
    versionBody = body;

    await respondWith({ version: NEWER_BUILD });
    await settle();

    expect({ modal: modalShowing(), banner: bannerShowing() }).toEqual(expected);
  });

  it('locks the page scroll behind the modal', async () => {
    versionBody = { current: NEWER_BUILD, update_required: true };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // Spec §3.4: scrolling underneath it would suggest the app is still
    // usable.
    expect(document.documentElement.style.overflow).toBe('hidden');
  });

  it('leaves the modal open and touches nothing until the click', async () => {
    versionBody = { current: NEWER_BUILD, update_required: true };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // The reveal on its own is inert. Before SNOW-609 the wipe fired here
    // and the reload tore the page down before the copy could be read.
    expect(modalShowing()).toBe(true);
    expect(clearShellCaches).not.toHaveBeenCalled();

    document.getElementById('pwa-update-modal-reload').click();
    await settle();

    expect(clearShellCaches).toHaveBeenCalledTimes(1);
    // The full six-step wipe is NOT this path's job — a blocked build is a
    // code problem, and the mutation queue, the favourites roster and the
    // pinned basemaps are not code.
    expect(resetLocalData).not.toHaveBeenCalled();
  });

  it('does not clear anything on a soft update', async () => {
    versionBody = { current: NEWER_BUILD, update_required: false };

    await respondWith({ version: NEWER_BUILD });
    await settle();

    // A soft update is an invitation, not a reset. The banner's own Reload
    // owns that decision (sw_register.js), not this path.
    expect(clearShellCaches).not.toHaveBeenCalled();
    expect(resetLocalData).not.toHaveBeenCalled();
  });

  it('emits pwa.forced_update.triggered exactly once, as blocked_build', async () => {
    const emit = vi.fn();
    window.pwaTelemetry = { emit };
    versionBody = { current: NEWER_BUILD, update_required: true };

    await respondWith({ version: NEWER_BUILD });
    await settle();
    await respondWith({ version: NEWER_BUILD });
    await settle();

    const forced = emit.mock.calls.filter((c) => c[0] === 'pwa.forced_update.triggered');
    expect(forced).toHaveLength(1);
    expect(forced[0][1]).toEqual({ trigger: 'blocked_build' });
    delete window.pwaTelemetry;
  });

  it('latches, so later responses cannot re-run the flow', async () => {
    versionBody = { current: NEWER_BUILD, update_required: true };
    await respondWith({ version: NEWER_BUILD });
    await settle();
    const callsAfterFirst = versionCalls.length;

    await respondWith({ version: 'something-else-entirely' });
    await settle();

    expect(versionCalls).toHaveLength(callsAfterFirst);
  });
});

describe('window.pwaClearShellCachesAndReload (sw_register.js)', () => {
  // Resolved off ``process.cwd()`` rather than ``import.meta.url`` — under
  // jsdom that URL is an ``http:`` one Vitest serves the module from, which
  // ``fileURLToPath`` rejects. Vitest's root is the repo root.
  const SW_REGISTER_SOURCE = readFileSync(
    join(process.cwd(), 'static', 'js', 'sw_register.js'),
    'utf8',
  );

  /**
   * Evaluate ``sw_register.js`` in a sandbox and hand back its window stub
   * plus the record of deleted cache buckets.
   *
   * The module needs a service worker to get past its own guard, so the
   * navigator stub answers the registration handshake with the minimum
   * shape its ``.then`` chain dereferences.
   *
   * @param {string[]} cacheNames - what ``caches.keys()`` reports.
   * @param {object} [options]
   * @param {boolean} [options.cacheApi] - false models a browser without
   *   Cache Storage (the function's own ``'caches' in window`` guard).
   * @returns {{win: object, deleted: string[]}}
   */
  function loadSwRegister(cacheNames, { cacheApi = true } = {}) {
    const deleted = [];
    const cachesStub = {
      keys: async () => cacheNames.slice(),
      delete: async (name) => {
        deleted.push(name);
        return true;
      },
    };
    const registration = {
      waiting: null,
      installing: null,
      addEventListener: () => {},
      update: async () => {},
    };
    const navigatorStub = {
      serviceWorker: {
        controller: null,
        addEventListener: () => {},
        register: async () => registration,
        getRegistration: async () => registration,
        getRegistrations: async () => [registration],
      },
    };
    const windowStub = { location: { reload: vi.fn() } };
    // ``clearShellCachesAndReload`` gates on ``'caches' in window`` before
    // reading the CacheStorage it was handed, so the stub has to appear in
    // both places for the delete branch to run at all.
    if (cacheApi) windowStub.caches = cachesStub;
    const fetchStub = async () => ({
      ok: true,
      json: async () => ({ sw_url: '/sw.js', kill: false }),
    });
    new Function(
      'window',
      'document',
      'navigator',
      'caches',
      'fetch',
      SW_REGISTER_SOURCE,
    )(windowStub, document, navigatorStub, cachesStub, fetchStub);
    return { win: windowStub, deleted };
  }

  it('is exported non-writable and non-configurable', () => {
    // Same shape as ``window.pwaResetLocalData`` (pwa_reset.js): a
    // third-party script must not be able to swap the implementation of a
    // function that deletes caches.
    const { win } = loadSwRegister([]);
    const descriptor = Object.getOwnPropertyDescriptor(
      win,
      'pwaClearShellCachesAndReload',
    );
    expect(typeof descriptor.value).toBe('function');
    expect(descriptor.writable).toBe(false);
    expect(descriptor.configurable).toBe(false);
  });

  it('deletes the shell buckets and spares the pinned basemap buckets', async () => {
    // The invariant SNOW-609 turns on: a forced update refreshes the code
    // and leaves the user's downloads alone. ``snowdesk-basemap-*`` is
    // hundreds of megabytes somebody chose to download before a trip.
    const { win, deleted } = loadSwRegister([
      'snowdesk-shell-abc123',
      'map-shell-abc123',
      'snowdesk-basemap-ch-4115',
      'snowdesk-basemap-custom-1',
      'data:favourites',
    ]);

    await win.pwaClearShellCachesAndReload();

    expect(deleted.sort()).toEqual(['map-shell-abc123', 'snowdesk-shell-abc123']);
    expect(win.location.reload).toHaveBeenCalledTimes(1);
  });

  it('still reloads when the Cache API is unavailable', async () => {
    const { win, deleted } = loadSwRegister(['snowdesk-shell-abc123'], {
      cacheApi: false,
    });

    await win.pwaClearShellCachesAndReload();

    expect(deleted).toEqual([]);
    expect(win.location.reload).toHaveBeenCalledTimes(1);
  });
});
