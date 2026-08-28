/*
 * tests/js/test_pwa_offline.js — Vitest unit tests for
 * static/js/pwa_offline.js's banner reveal/hide rules (C2, 2026-08-03 JS
 * review).
 *
 * Two behaviours are pinned here:
 *
 *   1. A caller-initiated abort is not a connectivity failure. The sign-in
 *      page starts the WebAuthn conditional ceremony on email-input focus
 *      and aborts it on the first keystroke, so a fully-online user typing
 *      their address used to pin the banner open for the life of the page.
 *   2. A successful same-origin response while ``navigator.onLine`` is true
 *      re-hides a revealed banner. Without it the only hide path is the
 *      ``online`` event, which never fires when connectivity never changed.
 *
 * ``pwa_offline.js`` is an IIFE that reads ``#pwa-offline-banner`` and wraps
 * ``window.fetch`` at import time, so every test builds its fixture and
 * installs its own ``window.fetch`` mock BEFORE importing the module, then
 * re-imports it fresh via ``vi.resetModules()`` (same pattern as
 * test_home_intro.js). Re-importing per test also stops each run's wrapper
 * stacking on the previous one's.
 *
 * ``window.pwaDb`` is left undefined for the banner tests: the persistence
 * and sync-log helpers all guard on it and return early, which keeps those
 * tests on the banner behaviour alone. Two blocks stub it, each because
 * what it asserts is what crosses that boundary — the sync-log block,
 * which is about what gets written, and SNOW-748's boot re-assert, whose
 * whole subject is what comes back out of ``meta:app``.
 *
 * SNOW-748 added a third mode and the two nav surfaces that carry it: a header
 * SYMBOL shown only while the app is not using the network, and an "Offline
 * mode" row in the subscriber menu that switches it — a phone's aeroplane
 * mode, status bar and settings. Their tests are at the foot of the file, and
 * the distinction they turn on is that ``'offline'`` is the worker's guess
 * that there is no route while ``'offline-forced'`` is the user's instruction
 * — so an ``online`` event, a probe and a page reload all treat the two
 * differently.
 *
 * The last block covers what that mode publishes. The control shipped without
 * it: ``snowdesk:connectivity-changed`` carried ``navigator.onLine`` alone and
 * fired only on an interface transition, so forcing offline mode changed
 * nothing any consumer could see — the map's layers menu kept its green sync
 * dots and the basemap download controls went on offering downloads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const BANNER_ID = 'pwa-offline-banner';
const TOGGLE_SELECTOR = '[data-network-toggle]';
const INDICATOR_SELECTOR = '[data-network-indicator]';

/**
 * The banner markup from templates/includes/_offline_banner.html, plus the
 * header offline SYMBOL from templates/includes/nav.html.
 *
 * SNOW-742 added the latched variants and the mode controls; SNOW-748 added
 * the forced-mode explainer, the second label on the way-back button, and the
 * nav surfaces. They are mirrored here rather than only in the templates
 * because the module toggles them by ``data-role``, so a fixture missing one
 * would silently make those assertions vacuous — the role helper skips a role
 * it cannot find, and ``renderNetworkUi`` skips an element it cannot find.
 *
 * The symbol is what an ANONYMOUS page renders: it is shown to every viewer,
 * while the "Offline mode" row below is signed-in only. Kept as its own
 * constant so ``buildAnonymousFixture`` can render exactly this and no row.
 */
const BANNER_AND_SYMBOL = `
  <details id="${BANNER_ID}" data-role="offline-freshness" role="status" class="hidden">
    <summary>
      <span data-role="offline-message">Offline — last synced</span>
      <span data-role="latched-message" class="hidden">Offline mode — last synced</span>
      <span data-role="synced-at">—</span>
    </summary>
    <div>
      <p data-role="offline-explainer">Lost contact.</p>
      <p data-role="latched-explainer" class="hidden">Stopped trying.</p>
      <p data-role="forced-explainer" class="hidden">You asked it to stay offline.</p>
      <button type="button" data-role="reconnect" class="hidden">
        <span data-role="reconnect-label">Try reconnecting</span>
        <span data-role="resume-label" class="hidden">Use the network again</span>
      </button>
    </div>
  </details>
  <span
    data-network-indicator
    class="hidden items-center justify-center w-7 h-7 rounded-full text-status-warning-text"
  >
    <svg></svg>
    <span class="sr-only">Offline mode</span>
  </span>
  <button type="button" data-network-required>Sync now</button>
`;

/**
 * The "Offline mode" row from the subscriber menu — the control half.
 *
 * Rendered only inside nav.html's ``{% if request.user.is_authenticated %}``
 * branch, hence its own constant: an anonymous page genuinely does not have
 * this element, and the module has to cope with that rather than skip the
 * symbol alongside it.
 */
const MENU_TOGGLE_ROW = `
  <button
    type="button"
    role="menuitemcheckbox"
    aria-checked="false"
    data-network-toggle
    class="group hidden w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-text-2"
  >
    <span class="shrink-0 text-text-3"><svg></svg></span>
    <span class="grow">Offline mode</span>
  </button>
`;

/**
 * A signed-in page: banner, header symbol, and the menu row that switches the
 * mode.
 *
 * The ``data-network-required`` button inside ``BANNER_AND_SYMBOL`` is any
 * page's stand-in for a control that cannot work without the network — that
 * attribute is the generic mechanism the whole site gates on, and the last
 * block asserts a forced mode reaches it.
 */
function buildFixture() {
  document.body.innerHTML = BANNER_AND_SYMBOL + MENU_TOGGLE_ROW;
}

/** An anonymous page: the symbol, and no way to switch the mode. */
function buildAnonymousFixture() {
  document.body.innerHTML = BANNER_AND_SYMBOL;
}

/** Whether the element carrying ``data-role`` is currently visible. */
function roleShown(role) {
  const el = document.querySelector(`[data-role="${role}"]`);
  return !!el && !el.classList.contains('hidden');
}

/** The menu's "Offline mode" row, for readability at the assertion site. */
function toggleButton() {
  return document.querySelector(TOGGLE_SELECTOR);
}

/** The header offline symbol. */
function indicator() {
  return document.querySelector(INDICATOR_SELECTOR);
}

/** Whether the header offline symbol is currently shown. */
function indicatorShown() {
  return !indicator().classList.contains('hidden');
}

/**
 * Record every ``snowdesk:connectivity-changed`` the module dispatches from
 * now on, and stop recording when ``stop()`` is called.
 *
 * Removed per test rather than left bound: every previous test's module
 * instance is still attached to the shared document (``vi.resetModules()``
 * gives a fresh module, it does not unbind the old one's listeners), so a
 * listener left in place would collect another test's broadcasts too.
 *
 * @returns {{online: boolean[], stop: () => void}}
 */
function recordConnectivity() {
  const online = [];
  const listener = (event) => online.push(event.detail.online);
  document.addEventListener('snowdesk:connectivity-changed', listener);
  return {
    online,
    stop: () => document.removeEventListener('snowdesk:connectivity-changed', listener),
  };
}

/** The fixture's ``data-network-required`` control. */
function networkRequiredButton() {
  return document.querySelector('[data-network-required]');
}

/** Whether the menu row is painted in an offline state. */
function toggleChecked() {
  return toggleButton().getAttribute('aria-checked') === 'true';
}

/**
 * Install a service-worker stub that records what the page posts to the
 * worker and lets a test push a worker-originated message back.
 *
 * @returns {{posted: object[], emit: (data: object) => void}}
 */
function stubServiceWorker() {
  const posted = [];
  const listeners = [];
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: {
      controller: { postMessage: (data) => posted.push(data) },
      addEventListener: (type, handler) => {
        if (type === 'message') listeners.push(handler);
      },
    },
  });
  return {
    posted,
    emit: (data) => listeners.forEach((handler) => handler({ data })),
  };
}

/** The banner element, for readability at the assertion site. */
function banner() {
  return document.getElementById(BANNER_ID);
}

/** Whether the banner is currently revealed. */
function bannerShown() {
  return !banner().classList.contains('hidden');
}

// The ``online`` handler the most recent ``loadModule()`` registered — see
// ``fireOnline`` for why the test calls it rather than dispatching the event.
let lastOnlineHandler = null;

/**
 * Re-import pwa_offline.js fresh against the current fixture and fetch
 * mock, awaiting the microtasks its async ``init()`` defers past.
 *
 * ``window.addEventListener`` is wrapped for the duration so the ``online``
 * handler this instance registers can be called directly later. Every previous
 * test's module instance is STILL bound to ``window`` — ``vi.resetModules()``
 * gives a fresh module, it does not unbind the old one's listeners — and each
 * carries its own ``networkMode`` closure over the shared DOM. Dispatching a
 * real ``online`` event therefore runs a dozen handlers, several of them in
 * ``'offline'`` and all of them writing to the one fixture.
 */
async function loadModule() {
  const realAdd = window.addEventListener.bind(window);
  window.addEventListener = (type, handler, options) => {
    if (type === 'online') lastOnlineHandler = handler;
    return realAdd(type, handler, options);
  };
  try {
    vi.resetModules();
    await import('../../static/js/pwa_offline.js');
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  } finally {
    window.addEventListener = realAdd;
  }
}

/** Run this instance's ``online`` handler, and nobody else's. */
function fireOnline() {
  lastOnlineHandler(new Event('online'));
}

/**
 * An Error shaped like the DOMException a real ``AbortController`` raises
 * when it cancels an in-flight fetch.
 *
 * @returns {Error}
 */
function abortError() {
  const err = new Error('The operation was aborted.');
  err.name = 'AbortError';
  return err;
}

/**
 * A minimal same-origin 200 stand-in carrying the three fields
 * ``absorbFreshness`` reads: headers, resolved URL, status.
 *
 * @param {string} path
 * @returns {object}
 */
function okResponse(path = '/api/ratings/') {
  return {
    headers: { get: () => null },
    url: `${window.location.origin}${path}`,
    status: 200,
  };
}

beforeEach(() => {
  buildFixture();
});

describe('offline banner reveal', () => {
  it('does not reveal the banner when a fetch is aborted', async () => {
    const err = abortError();
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/account/passkey/auth/request/')).rejects.toBe(err);

    expect(bannerShown()).toBe(false);
  });

  it('still reveals the banner on a genuine network failure', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);

    expect(bannerShown()).toBe(true);
  });
});

describe('offline banner recovery', () => {
  it('re-hides a revealed banner on a successful same-origin response while online', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValueOnce(okResponse());
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);
    expect(bannerShown()).toBe(true);

    await window.fetch('/api/ratings/');

    expect(bannerShown()).toBe(false);
  });
});


// ---------------------------------------------------------------------------
// SNOW-742 — the offline latch, as the page sees it
// ---------------------------------------------------------------------------
//
// The banner used to key off ``navigator.onLine`` alone. That is exactly the
// signal which stays TRUE on the Underground — the radio is attached, there is
// simply no route — so the state these tests cover is one the old banner could
// not represent at all: latched, and online as far as the platform knows.

describe('offline latch banner (SNOW-742)', () => {
  it('shows the latched variant when the worker announces a latch, even while onLine', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(bannerShown()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Revealed despite navigator.onLine being true throughout — the case the
    // old onLine-only banner was blind to.
    expect(window.navigator.onLine).toBe(true);
    expect(bannerShown()).toBe(true);
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
    expect(roleShown('latched-explainer')).toBe(true);
  });

  it('offers the way back only once the app has actually stopped trying', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    // Struggling but not latched: the app is still trying on its own, so
    // "try reconnecting" would do nothing it is not already doing.
    await expect(window.fetch('/api/ratings/')).rejects.toThrow();
    expect(roleShown('reconnect')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Latched: the app has stopped, so the useful action is the way back.
    expect(roleShown('reconnect')).toBe(true);
    expect(roleShown('reconnect-label')).toBe(true);
  });

  it('asks the worker to unlatch when the user taps Try reconnecting', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    banner().querySelector('[data-role="reconnect"]').click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(roleShown('offline-message')).toBe(true);
  });

  it('hides the banner again once the worker reports it has unlatched', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(bannerShown()).toBe(true);

    // A probe found a route again.
    sw.emit({ type: 'network-mode', mode: 'auto' });

    expect(bannerShown()).toBe(false);
  });
});


// ---------------------------------------------------------------------------
// The sync log's write side
// ---------------------------------------------------------------------------
//
// The panel on /account/settings/ answers "has this device actually reached
// the server", so a row only earns its place if the reader would recognise it
// as the app fetching something. telemetry.js flushes its own buffer every 30
// seconds and on every lifecycle event, which filled the panel with rows
// describing nothing the reader did.

describe('sync-log write filter', () => {
  /**
   * Stub the three ``window.pwaDb`` methods pwa_offline.js touches and
   * record every path handed to ``appendSyncLog``.
   *
   * @returns {string[]} live array of logged paths
   */
  function stubDb() {
    const logged = [];
    window.pwaDb = {
      get: async () => undefined,
      put: async () => {},
      appendSyncLog: async (entry) => {
        logged.push(entry.path);
      },
    };
    return logged;
  }

  afterEach(() => {
    delete window.pwaDb;
  });

  it('does not log a telemetry flush', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/telemetry'));
    await loadModule();

    await window.fetch('/api/telemetry', { method: 'POST' });

    expect(logged).toEqual([]);
  });

  it('still logs a request the reader would recognise', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/ratings/'));
    await loadModule();

    await window.fetch('/api/ratings/');

    expect(logged).toEqual(['/api/ratings/']);
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the nav's symbol and switch, and a forced mode that stays forced
// ---------------------------------------------------------------------------
//
// The control SNOW-742 built lived in the banner, which only reveals once the
// connection has already failed — so the user it was for, "I have signal now
// and am about to lose it", could never reach it. It is now in the nav, split
// the way a phone splits aeroplane mode: a symbol in the header that appears
// only while the mode is on, and a switch in the settings menu. That move
// exposed the defect these tests pin: a user's request used to be the worker's
// auto-latch, and an auto-latch is probed back to 'auto' within thirty
// seconds. Every assertion below is about the difference between the two
// offline modes; ``'offline'`` is the worker's guess, ``'offline-forced'`` is
// the user's.

describe('the header offline symbol (SNOW-748)', () => {
  it('shows nothing while the app is using the network', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // A phone shows the aeroplane glyph only while aeroplane mode is on. In
    // 'auto' there is nothing to say, so the header says nothing.
    expect(indicatorShown()).toBe(false);
  });

  it('appears under either offline mode, and goes again in auto', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The worker latched on its own.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(indicatorShown()).toBe(true);

    // The user's own mode shows the same mark — the header answers one
    // question, and the banner carries which offline it is.
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(indicatorShown()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(indicatorShown()).toBe(false);
  });

  it('is painted on an anonymous page, which has no menu row at all', async () => {
    // The row lives inside nav.html's authenticated branch; the symbol does
    // not. The module looks each up separately for exactly this case — a
    // shared early return on the missing row would leave an anonymous user
    // with no indication that the app had stopped using the network.
    buildAnonymousFixture();
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(toggleButton()).toBeNull();
    expect(indicatorShown()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    expect(indicatorShown()).toBe(true);
  });
});

describe('the menu offline-mode toggle (SNOW-748)', () => {
  it('is revealed by this module, not by the template', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    // The nav renders it `hidden` so a page whose script never runs does not
    // offer a control nothing will honour.
    expect(toggleButton().classList.contains('hidden')).toBe(true);

    await loadModule();

    expect(toggleButton().classList.contains('hidden')).toBe(false);
    expect(toggleButton().classList.contains('flex')).toBe(true);
  });

  it('asks for a forced mode — never the auto-latch — when pressed while online', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    toggleButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
    expect(sw.posted).not.toContainEqual({ type: 'network-mode', mode: 'offline' });
  });

  it('round-trips: a second press asks for auto again', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    toggleButton().click();
    expect(toggleChecked()).toBe(true);
    expect(indicatorShown()).toBe(true);

    toggleButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(toggleChecked()).toBe(false);
    expect(indicatorShown()).toBe(false);
  });

  it('reports checked under either offline mode, including one it did not start', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(toggleChecked()).toBe(false);

    // aria-checked is the row's whole state: a menuitemcheckbox says on/off
    // and nothing finer, and the worker's latch is as much "not using the
    // network" as the user's own choice is.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(toggleChecked()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(toggleChecked()).toBe(true);
  });
});

describe('the forced offline mode, as the page renders it (SNOW-748)', () => {
  it('reveals the banner while forced even though navigator.onLine is true', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    // The normal case for this mode: a working connection the user has asked
    // the app not to use. Keying the banner off onLine would hide it.
    expect(window.navigator.onLine).toBe(true);
    expect(bannerShown()).toBe(true);
  });

  it('explains a forced mode differently from a latch, and shares the summary line', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(roleShown('latched-explainer')).toBe(true);
    expect(roleShown('forced-explainer')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    // "There is no usable connection" is false here, so that copy must go.
    expect(roleShown('forced-explainer')).toBe(true);
    expect(roleShown('latched-explainer')).toBe(false);
    // The summary line is shared: both modes mean "not contacting the server".
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
  });

  it('offers the way back with the verb that fits, in both modes', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(roleShown('reconnect')).toBe(true);
    // "Try reconnecting" reads as a repair, and nothing is broken.
    expect(roleShown('resume-label')).toBe(true);
    expect(roleShown('reconnect-label')).toBe(false);

    banner().querySelector('[data-role="reconnect"]').click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('survives an online event, where an auto-latch does not', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    fireOnline();

    // The user is very often ONLINE when they choose this mode — a metered
    // roam, a battery to nurse, a tunnel ahead — so an interface event must
    // not overrule them.
    expect(sw.posted).not.toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(bannerShown()).toBe(true);
    expect(toggleChecked()).toBe(true);
    expect(indicatorShown()).toBe(true);

    // The contrast: an auto-latch is the worker guessing there is no route,
    // and an online event is better evidence, so that one does lift.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    fireOnline();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('re-asserts the persisted mode as itself, not as a latch', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    // A worker terminated while idle comes back in 'auto'. Re-asserting is
    // what restores the mode — and re-asserting the WRONG one hands it a
    // latch, which schedules the probe that ends the user's choice.
    window.pwaDb = {
      get: vi.fn().mockResolvedValue({ key: 'network.mode', value: 'offline-forced' }),
      put: vi.fn().mockResolvedValue(undefined),
      add: vi.fn().mockResolvedValue(undefined),
      getAll: vi.fn().mockResolvedValue([]),
      isResetRequired: () => false,
    };

    await loadModule();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
    expect(bannerShown()).toBe(true);
    expect(toggleChecked()).toBe(true);
    expect(indicatorShown()).toBe(true);
    delete window.pwaDb;
  });
});

describe('what a forced mode publishes to the rest of the app (SNOW-748)', () => {
  /*
   * The toggle shipped without this half. `broadcastConnectivity` carried
   * `navigator.onLine` alone, and fired only from the `online`/`offline`
   * listeners and at boot — so pressing the toggle dispatched nothing, and
   * every consumer (the layers menu's sync dots, both basemap download
   * controls, the downloads sheet) went on believing the network was
   * available. Hugo reproduced it live: zero events, and a Download button
   * still `disabled: false`.
   */

  it('reports offline the moment the mode goes forced, while onLine is true', async () => {
    // Installed but not read back: this test drives the mode from the page's
    // own toggle, and the stub is only here so the post to the worker lands
    // somewhere fresh rather than on a previous test's stub.
    stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    const seen = recordConnectivity();

    try {
      toggleButton().click();

      // The premise of this whole mode: the interface is up throughout.
      expect(window.navigator.onLine).toBe(true);
      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('reports online again when the user returns to auto', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    const seen = recordConnectivity();

    try {
      toggleButton().click();

      expect(seen.online).toEqual([true]);
    } finally {
      seen.stop();
    }
  });

  it('reports a latch the worker announced on its own, too', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    const seen = recordConnectivity();

    try {
      // Not only the user's mode: a latch also stops the app calling the
      // server, and the dots and download controls must see that as well.
      sw.emit({ type: 'network-mode', mode: 'offline' });

      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('does not re-report online on an interface event while forced', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    const seen = recordConnectivity();

    try {
      fireOnline();

      // The `online` listener used to broadcast a hardcoded `true`, which
      // handed every consumer the network back the first time the radio
      // blinked — under a mode the user had chosen and the worker was still
      // enforcing.
      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('disables data-network-required controls under a forced mode', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(networkRequiredButton().disabled).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(networkRequiredButton().disabled).toBe(true);

    // And an interface event must not undo it — same trap as the broadcast
    // above, one mechanism across.
    fireOnline();
    expect(networkRequiredButton().disabled).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(networkRequiredButton().disabled).toBe(false);
  });

  it('answers the same question through window.pwaConnectivity', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The read the map's controls make when they repaint — they re-render on
    // the event, so the two must agree or the paint contradicts the banner.
    expect(window.pwaConnectivity.isOnline()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(window.pwaConnectivity.isOnline()).toBe(true);
  });
});
