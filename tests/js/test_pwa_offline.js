/*
 * tests/js/test_pwa_offline.js — Vitest unit tests for
 * static/js/pwa_offline.js's connection-state UI (C2, 2026-08-03 JS review).
 *
 * Two behaviours are pinned first:
 *
 *   1. A caller-initiated abort is not a connectivity failure. The sign-in
 *      page starts the WebAuthn conditional ceremony on email-input focus
 *      and aborts it on the first keystroke, so a fully-online user typing
 *      their address used to have the whole app declaring itself offline
 *      for the life of the page.
 *   2. A successful same-origin response while ``navigator.onLine`` is true
 *      repaints as online. Without it the only recovery path is the
 *      ``online`` event, which never fires when connectivity never changed.
 *
 * ``pwa_offline.js`` is an IIFE that reads its DOM and wraps ``window.fetch``
 * at import time, so every test builds its fixture and installs its own
 * ``window.fetch`` mock BEFORE importing the module, then re-imports it fresh
 * via ``vi.resetModules()`` (same pattern as test_home_intro.js). Re-importing
 * per test also stops each run's wrapper stacking on the previous one's.
 *
 * ``window.pwaDb`` is left undefined for most tests: the persistence and
 * sync-log helpers all guard on it and return early, which keeps those tests
 * on the UI behaviour alone. Two blocks stub it, each because what it asserts
 * is what crosses that boundary — the sync-log block, which is about what gets
 * written, and SNOW-748's boot re-assert, whose whole subject is what comes
 * back out of ``meta:app``.
 *
 * SNOW-742 added a third mode; SNOW-748 rebuilt the surfaces that carry it. The
 * offline BANNER is gone. In its place: a PERMANENT header symbol, painted on
 * every page for every viewer and never hidden, and a connection-status TOAST
 * the symbol opens. The distinction the mode tests turn on is that
 * ``'offline'`` is the worker's guess that there is no route while
 * ``'offline-forced'`` is the user's instruction — so an ``online`` event, a
 * probe and a page reload all treat the two differently.
 *
 * The symbol and the menu switch paint DIFFERENT predicates, and several tests
 * exist only to hold that apart: the symbol reports whether the app is
 * reaching the server (so a dead interface strikes it through in ``'auto'``),
 * while the switch reports whether the user asked for offline mode (so a
 * merely-struggling connection must leave it off).
 *
 * The last block covers what that mode publishes. The control shipped without
 * it: ``snowdesk:connectivity-changed`` carried ``navigator.onLine`` alone and
 * fired only on an interface transition, so forcing offline mode changed
 * nothing any consumer could see — the map's layers menu kept its green sync
 * dots and the basemap download controls went on offering downloads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const TOAST_ID = 'pwa-offline-toast';
const SWITCH_ID = 'nav-offline-mode';
const INDICATOR_SELECTOR = '[data-network-indicator]';
const TOGGLE_ROW_SELECTOR = '[data-network-toggle]';
const CTA_SELECTOR = `#${TOAST_ID} [data-action="reload"]`;

/**
 * The header symbol from templates/includes/nav.html plus the
 * connection-status toast from templates/includes/_offline_toast.html (which
 * renders through _toast.html's body_template / cta_label_template slots).
 *
 * Mirrored here rather than only in the templates because the module toggles
 * them by ``data-role``, so a fixture missing one would silently make those
 * assertions vacuous — the role helper skips a role it cannot find.
 *
 * This is what an ANONYMOUS page renders: the symbol is shown to every viewer,
 * while the "Offline mode" switch below is signed-in only. Kept as its own
 * constant so ``buildAnonymousFixture`` can render exactly this and no row.
 *
 * The toast carries ``hidden flex`` at rest exactly as the real partial does:
 * ``hidden`` is emitted last in Tailwind's display group and so wins while
 * both are present, and revealing is removing ``hidden`` alone.
 */
const SYMBOL_AND_TOAST = `
  <button
    type="button"
    data-network-indicator
    data-network-state="online"
    aria-expanded="false"
    aria-controls="${TOAST_ID}"
  >
    <span data-role="network-online-icon"><svg></svg></span>
    <span data-role="network-offline-icon" class="hidden"><svg></svg></span>
    <span data-role="network-name-online" class="sr-only">Connection status: using the network</span>
    <span data-role="network-name-offline" class="sr-only hidden">Connection status: offline</span>
  </button>
  <div id="${TOAST_ID}" role="status" data-overlay data-overlay-hide="class" class="hidden flex">
    <span data-toast-body>
      <span>
        <span data-role="online-message">Online — last synced</span>
        <span data-role="offline-message" class="hidden">Offline — last synced</span>
        <span data-role="latched-message" class="hidden">Offline mode — last synced</span>
        <span data-role="synced-at">—</span>
      </span>
      <span>
        <span data-role="online-explainer">Using the network.</span>
        <span data-role="offline-explainer" class="hidden">Lost contact.</span>
        <span data-role="latched-explainer" class="hidden">Stopped trying.</span>
        <span data-role="forced-explainer" class="hidden">You asked it to stay offline.</span>
      </span>
    </span>
    <button type="button" data-action="reload">
      <span data-role="reconnect-label">Try reconnecting</span>
      <span data-role="resume-label" class="hidden">Use the network again</span>
    </button>
    <button type="button" data-action="dismiss">×</button>
  </div>
  <button type="button" data-network-required>Sync now</button>
`;

/**
 * The "Offline mode" row from the subscriber menu — the control half.
 *
 * Rendered only inside nav.html's ``{% if request.user.is_authenticated %}``
 * branch, hence its own constant: an anonymous page genuinely does not have
 * this element, and the module has to cope with that rather than skip the
 * symbol alongside it.
 *
 * A real ``includes/_switch.html`` checkbox, not the role="menuitemcheckbox"
 * button this row shipped as, so the state the module writes is ``checked``
 * on an input rather than an attribute on a button.
 */
const MENU_SWITCH_ROW = `
  <div role="none" data-network-toggle class="hidden items-center gap-3">
    <label for="${SWITCH_ID}">Offline mode</label>
    <label for="${SWITCH_ID}">
      <input id="${SWITCH_ID}" type="checkbox" role="switch" class="peer sr-only">
    </label>
  </div>
`;

/**
 * A signed-in page: symbol, toast, and the menu switch that changes the mode.
 *
 * The ``data-network-required`` button inside ``SYMBOL_AND_TOAST`` is any
 * page's stand-in for a control that cannot work without the network — that
 * attribute is the generic mechanism the whole site gates on, and the last
 * block asserts a forced mode reaches it.
 */
function buildFixture() {
  document.body.innerHTML = SYMBOL_AND_TOAST + MENU_SWITCH_ROW;
}

/** An anonymous page: the symbol and toast, and no way to switch the mode. */
function buildAnonymousFixture() {
  document.body.innerHTML = SYMBOL_AND_TOAST;
}

/**
 * The shared "×" dismiss handler from static/js/overlays.js, in the one form
 * this module depends on: it adds ``hidden`` and dispatches
 * ``overlay:dismissed``, and pwa_offline.js listens for the second so a close
 * it did not perform still lands on ``aria-expanded`` and the ticker.
 *
 * Imported rather than reimplemented would drag the whole module's other
 * listeners into every test in this file; this is the contract, stated once.
 *
 * @returns {() => void} a teardown that unbinds it again
 */
function installDismissHandler() {
  const listener = (event) => {
    const trigger = event.target.closest?.('[data-action="dismiss"]');
    if (!trigger) return;
    const overlay = trigger.closest('[data-overlay]');
    if (!overlay) return;
    overlay.classList.add('hidden');
    overlay.dispatchEvent(new CustomEvent('overlay:dismissed', { bubbles: true }));
  };
  document.addEventListener('click', listener);
  return () => document.removeEventListener('click', listener);
}

/** Whether the element carrying ``data-role`` is currently visible. */
function roleShown(role) {
  const el = document.querySelector(`[data-role="${role}"]`);
  return !!el && !el.classList.contains('hidden');
}

/** The menu's "Offline mode" switch input, for readability at the call site. */
function switchInput() {
  return document.getElementById(SWITCH_ID);
}

/** The row the switch sits in — the element the module reveals. */
function toggleRow() {
  return document.querySelector(TOGGLE_ROW_SELECTOR);
}

/** The header symbol. */
function indicator() {
  return document.querySelector(INDICATOR_SELECTOR);
}

/** What the header symbol is currently reporting: 'online' or 'offline'. */
function indicatorState() {
  return indicator().getAttribute('data-network-state');
}

/** The connection-status toast. */
function toast() {
  return document.getElementById(TOAST_ID);
}

/** Whether the toast is currently open. */
function toastShown() {
  return !toast().classList.contains('hidden');
}

/** The toast's one CTA — the way back to the network. */
function reconnectButton() {
  return document.querySelector(CTA_SELECTOR);
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

/** Whether the menu switch is painted in an offline state. */
function switchChecked() {
  return switchInput().checked;
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

describe('the header symbol tracks the connection', () => {
  it('does not report offline when a fetch is aborted', async () => {
    const err = abortError();
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/account/passkey/auth/request/')).rejects.toBe(err);

    expect(indicatorState()).toBe('online');
    expect(roleShown('network-online-icon')).toBe(true);
    expect(roleShown('network-offline-icon')).toBe(false);
  });

  it('reports offline on a genuine network failure', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);

    expect(indicatorState()).toBe('offline');
    expect(roleShown('network-offline-icon')).toBe(true);
    expect(roleShown('network-online-icon')).toBe(false);
  });

  it('recovers on a successful same-origin response while online', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValueOnce(okResponse());
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);
    expect(indicatorState()).toBe('offline');

    await window.fetch('/api/ratings/');

    expect(indicatorState()).toBe('online');
  });

  it('names itself for the state, in text the template rendered', async () => {
    // Both glyph partials set aria-hidden on their own <svg>, so the sr-only
    // spans ARE the accessible name. Toggled here, never assigned: a name set
    // from a JS literal ships English to every locale, because makemessages
    // never scans static/js.
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(roleShown('network-name-online')).toBe(true);
    expect(roleShown('network-name-offline')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    expect(roleShown('network-name-offline')).toBe(true);
    expect(roleShown('network-name-online')).toBe(false);
  });

  it('is painted on an anonymous page, which has no menu switch at all', async () => {
    // The switch lives inside nav.html's authenticated branch; the symbol does
    // not. The module looks each up separately for exactly this case — a
    // shared early return on the missing row would leave an anonymous user
    // with no indication that the app had stopped using the network.
    buildAnonymousFixture();
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(switchInput()).toBeNull();
    expect(indicatorState()).toBe('online');

    sw.emit({ type: 'network-mode', mode: 'offline' });

    expect(indicatorState()).toBe('offline');
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the toast the symbol opens
// ---------------------------------------------------------------------------
//
// The banner this replaces revealed itself whenever the app stopped reaching
// the server, and hid itself again when it recovered. The toast does neither:
// it opens and closes on the user's press alone, and the module only ever
// repaints its copy. These tests are what stops the old reveal-on-failure
// behaviour creeping back in under a different element id.

describe('the connection-status toast (SNOW-748)', () => {
  it('stays closed until the symbol is pressed', async () => {
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toThrow();

    // Offline, and still nothing over the page. The header says so; the detail
    // is one press away.
    expect(indicatorState()).toBe('offline');
    expect(toastShown()).toBe(false);
  });

  it('opens on a press and closes on the next one', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    indicator().click();
    expect(toastShown()).toBe(true);
    expect(indicator().getAttribute('aria-expanded')).toBe('true');

    indicator().click();
    expect(toastShown()).toBe(false);
    expect(indicator().getAttribute('aria-expanded')).toBe('false');
  });

  it('never changes the network mode', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    indicator().click();

    // A disclosure, not a switch. An earlier pass shipped this element as a
    // toggle for the mode itself, which is what aria-expanded (rather than
    // aria-pressed) now promises a screen-reader user it is not.
    expect(sw.posted).toEqual([]);
    expect(switchChecked()).toBe(false);
  });

  it('follows a "×" dismiss it did not perform', async () => {
    const teardown = installDismissHandler();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      indicator().click();
      expect(indicator().getAttribute('aria-expanded')).toBe('true');

      document.querySelector(`#${TOAST_ID} [data-action="dismiss"]`).click();

      // overlays.js hides it; the module learns via overlay:dismissed. Without
      // that binding the symbol would claim the toast was still open, and the
      // next press would close an already-closed panel.
      expect(toastShown()).toBe(false);
      expect(indicator().getAttribute('aria-expanded')).toBe('false');
    } finally {
      teardown();
    }
  });

  it('fills the freshness cell when it opens', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    await window.fetch('/api/ratings/');

    // Em dash until a sync is known; a relative phrase once one is.
    indicator().click();

    expect(document.querySelector('[data-role="synced-at"]').textContent).not.toBe('—');
  });

  it('explains a healthy connection, which the banner never had to', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The banner only existed in the failure case, so it had no copy for this
    // one. The symbol is pressable at any moment, so the toast needs it.
    expect(roleShown('online-message')).toBe(true);
    expect(roleShown('online-explainer')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
    expect(roleShown('latched-message')).toBe(false);
  });

  it('offers no way back while the network is already in use', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(reconnectButton().classList.contains('hidden')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// SNOW-742 — the offline latch, as the page sees it
// ---------------------------------------------------------------------------
//
// The UI used to key off ``navigator.onLine`` alone. That is exactly the
// signal which stays TRUE on the Underground — the radio is attached, there is
// simply no route — so the state these tests cover is one the old banner could
// not represent at all: latched, and online as far as the platform knows.

describe('offline latch (SNOW-742)', () => {
  it('shows the latched copy when the worker announces a latch, even while onLine', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(roleShown('online-message')).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Painted despite navigator.onLine being true throughout — the case the
    // old onLine-only banner was blind to.
    expect(window.navigator.onLine).toBe(true);
    expect(indicatorState()).toBe('offline');
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('online-message')).toBe(false);
    expect(roleShown('latched-explainer')).toBe(true);
  });

  it('offers the way back only once the app has actually stopped trying', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    // Struggling but not latched: the app is still trying on its own, so
    // "try reconnecting" would do nothing it is not already doing.
    await expect(window.fetch('/api/ratings/')).rejects.toThrow();
    expect(roleShown('offline-message')).toBe(true);
    expect(reconnectButton().classList.contains('hidden')).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Latched: the app has stopped, so the useful action is the way back.
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    expect(roleShown('reconnect-label')).toBe(true);
  });

  it('asks the worker to unlatch when the user taps Try reconnecting', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    reconnectButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(roleShown('online-message')).toBe(true);
  });

  it('clears the symbol once the worker reports it has unlatched', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(indicatorState()).toBe('offline');

    // A probe found a route again.
    sw.emit({ type: 'network-mode', mode: 'auto' });

    expect(indicatorState()).toBe('online');
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
// SNOW-748 — the menu switch, and a forced mode that stays forced
// ---------------------------------------------------------------------------
//
// The control SNOW-742 built lived in the banner, which only revealed once the
// connection had already failed — so the user it was for, "I have signal now
// and am about to lose it", could never reach it. It is now at the top of the
// account menu, the settings half of the aeroplane-mode model whose status-bar
// half is the header symbol. That move exposed the defect these tests pin: a
// user's request used to be the worker's auto-latch, and an auto-latch is
// probed back to 'auto' within thirty seconds. ``'offline'`` is the worker's
// guess; ``'offline-forced'`` is the user's.

describe('the menu offline-mode switch (SNOW-748)', () => {
  it('is revealed by this module, not by the template', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    // The nav renders it `hidden` so a page whose script never runs does not
    // offer a control nothing will honour.
    expect(toggleRow().classList.contains('hidden')).toBe(true);

    await loadModule();

    expect(toggleRow().classList.contains('hidden')).toBe(false);
    expect(toggleRow().classList.contains('flex')).toBe(true);
  });

  it('asks for a forced mode — never the auto-latch — when switched on while online', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    switchInput().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
    expect(sw.posted).not.toContainEqual({ type: 'network-mode', mode: 'offline' });
  });

  it('round-trips: switching it off asks for auto again', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    switchInput().click();
    expect(switchChecked()).toBe(true);
    expect(indicatorState()).toBe('offline');

    switchInput().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(switchChecked()).toBe(false);
    expect(indicatorState()).toBe('online');
  });

  it('reports on under either offline mode, including one it did not start', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(switchChecked()).toBe(false);

    // ``checked`` is the row's whole state: a switch says on/off and nothing
    // finer, and the worker's latch is as much "not using the network" as the
    // user's own choice is.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(switchChecked()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(switchChecked()).toBe(true);
  });

  it('stays off while the app is merely struggling, though the symbol does not', async () => {
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toThrow();

    // The two surfaces answer different questions, and this is the case that
    // separates them. The symbol reports whether the app is reaching the
    // server, so it goes struck-through. The switch reports whether the USER
    // asked for offline mode, and nobody did — a switch that flicks itself on
    // when the lift crosses a ridge reports the worker's decision as theirs.
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(false);
  });
});

describe('the forced offline mode, as the page renders it (SNOW-748)', () => {
  it('paints the symbol offline while navigator.onLine is true', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    // The normal case for this mode: a working connection the user has asked
    // the app not to use. Keying the symbol off onLine would say "online".
    expect(window.navigator.onLine).toBe(true);
    expect(indicatorState()).toBe('offline');
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
    expect(roleShown('online-message')).toBe(false);
  });

  it('offers the way back with the verb that fits, in both modes', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    // "Try reconnecting" reads as a repair, and nothing is broken.
    expect(roleShown('resume-label')).toBe(true);
    expect(roleShown('reconnect-label')).toBe(false);

    reconnectButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('gives an anonymous reader the only exit they have', async () => {
    // The menu switch is signed-in only, so for an anonymous user latched by
    // the worker this button is the whole way back. It is the reason the
    // banner's reconnect control had to survive the move into the toast.
    buildAnonymousFixture();
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(toggleRow()).toBeNull();

    reconnectButton().click();

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
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(true);

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
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(true);
    expect(roleShown('forced-explainer')).toBe(true);
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
    // own switch, and the stub is only here so the post to the worker lands
    // somewhere fresh rather than on a previous test's stub.
    stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    const seen = recordConnectivity();

    try {
      switchInput().click();

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
      switchInput().click();

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
    // the event, so the two must agree or the paint contradicts the symbol.
    expect(window.pwaConnectivity.isOnline()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(window.pwaConnectivity.isOnline()).toBe(true);
  });
});
