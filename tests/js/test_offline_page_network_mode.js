/*
 * tests/js/test_offline_page_network_mode.js — the Offline mode switch on
 * the offline fallback page (SNOW-922).
 *
 * ## The state this control exists for
 *
 * Under ``'offline-forced'`` ``sw.js`` refuses the network on every read
 * path, HTML navigations included, and answers from cache instead. On a
 * device with no shell page cached for the account signed in, that means
 * every navigation lands on ``static/offline.html`` — and the mode is
 * persisted to ``meta:app`` and re-hydrated by the worker on each boot, so
 * it survives the tab, the worker and the device restarting. A live signal
 * changed nothing, because it is the worker refusing, not the radio.
 *
 * The only control that ended the state lived in the account menu, inside
 * the app that would not open. The exit was behind the door it locks. The
 * two ways out were a full local-data wipe — which also destroys every
 * downloaded region and saved place — and the browser's own site-data
 * settings.
 *
 * So the switch is on this page now, and these tests assert it against the
 * shipped file rather than a fixture copy, exactly as the reset tests next
 * door do. The harness is theirs: the markup is mounted into jsdom and the
 * page's own inline bootstrap is pulled out and run, because `innerHTML`
 * executes nothing.
 *
 * ## Why it is shown in both states
 *
 * A control that appears only while something is broken teaches nobody
 * where it lives, and turning offline mode ON from here is a real thing to
 * want — you are offline, and you would rather the app stopped spending a
 * roaming connection. The lock-out is headed off by the confirmation
 * instead, which is the same guard the nav switch takes.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const OFFLINE_HTML = readFileSync(resolve(process.cwd(), 'static/offline.html'), 'utf-8');

const PANEL_ID = 'network-mode-panel';
const SWITCH_ID = 'offline-mode-switch';

/** Parse the shipped page into an inert document. */
function parseOfflinePage() {
  return new DOMParser().parseFromString(OFFLINE_HTML, 'text/html');
}

/** Mount the shipped page's body and return its inline bootstrap source. */
function mountOfflinePage() {
  const doc = parseOfflinePage();
  document.body.innerHTML = doc.body.innerHTML;
  return Array.from(doc.querySelectorAll('script:not([src])'))
    .map((script) => script.textContent)
    .join('\n');
}

/** Run the page's own bootstrap, then fire the event it waits on. */
function runBootstrap(source) {
  // eslint-disable-next-line no-new-func -- executing the shipped page source is the point.
  new Function(source)();
  document.dispatchEvent(new Event('DOMContentLoaded'));
}

/**
 * Stand in for pwa_network_mode.js, which the page loads as a subresource.
 *
 * A stub rather than the real module: what these tests are about is what
 * the PAGE does with the answers. The module's own reads and writes are
 * pinned in tests/js/test_pwa_network_mode.js.
 *
 * @param {{mode?: string, canOpen?: boolean}} [options]
 */
function stubNetworkMode(options) {
  const opts = options || {};
  const set = vi.fn().mockResolvedValue('auto');
  const canOpenOffline = vi.fn().mockResolvedValue(opts.canOpen === true);
  window.pwaNetworkMode = {
    coerce: (value) => (value === 'offline' || value === 'offline-forced' ? value : 'auto'),
    isForced: (value) => value === 'offline-forced',
    blocksNetwork: (value) => value === 'offline' || value === 'offline-forced',
    read: vi.fn().mockResolvedValue(opts.mode || 'auto'),
    set,
    canOpenOffline,
  };
  return { set, canOpenOffline };
}

/** Let the bootstrap's `read().then(…)` settle. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

const panel = () => document.getElementById(PANEL_ID);
const switchInput = () => document.getElementById(SWITCH_ID);
const hint = () => document.getElementById('network-mode-hint');

beforeEach(() => {
  document.body.innerHTML = '';
  // The page reloads itself after a mode change; jsdom has no navigation,
  // so the call is stubbed rather than letting it log "not implemented".
  delete window.location;
  window.location = { reload: vi.fn() };
});

afterEach(() => {
  delete window.pwaNetworkMode;
  vi.restoreAllMocks();
});

describe('the shipped markup', () => {
  it('carries the switch, hidden until the module that works it has arrived', () => {
    const doc = parseOfflinePage();
    const row = doc.getElementById(PANEL_ID);

    // Same self-guard as the reset and audit panels. A control bound to
    // nothing is worse than no control on a recovery page.
    expect(row).not.toBeNull();
    expect(row.hasAttribute('hidden')).toBe(true);
  });

  it('is a real checkbox with the switch role, not a div with a handler', () => {
    // Matches includes/_switch.html: Tab reaches it, Space toggles it,
    // `:checked` drives the drawing, and none of that has to be written.
    const input = parseOfflinePage().getElementById(SWITCH_ID);

    expect(input.tagName).toBe('INPUT');
    expect(input.getAttribute('type')).toBe('checkbox');
    expect(input.getAttribute('role')).toBe('switch');
  });
});

describe('before the module has loaded', () => {
  it('leaves the switch hidden rather than showing a dead control', () => {
    // The state a device lands in whose precache predates this ticket.
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);

    expect(panel().hidden).toBe(true);
  });
});

describe('painting the mode', () => {
  it('shows the switch ON, and says why the page is showing, under a forced mode', async () => {
    stubNetworkMode({ mode: 'offline-forced' });
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);
    await settle();

    expect(panel().hidden).toBe(false);
    expect(switchInput().checked).toBe(true);
    expect(hint().textContent).toContain('will not load');
  });

  it('rewrites the headline advice, which is otherwise impossible to follow', async () => {
    // "Reconnect and try this page again" is the page's standing copy, and
    // under this mode it is advice for something that cannot work: the
    // worker refuses the network whatever the radio is doing. That
    // sentence is how this ticket got reported.
    stubNetworkMode({ mode: 'offline-forced' });
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);
    await settle();

    const explainer = document.getElementById('offline-explainer').textContent;
    expect(explainer).toContain('Offline mode is switched on');
    expect(explainer).not.toContain('Reconnect and try this page again');
  });

  it('shows the switch OFF, with ordinary copy, when the mode is auto', async () => {
    stubNetworkMode({ mode: 'auto' });
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);
    await settle();

    expect(panel().hidden).toBe(false);
    expect(switchInput().checked).toBe(false);
    expect(document.getElementById('offline-explainer').textContent).toContain(
      'Reconnect and try this page again',
    );
  });

  it('shows the switch ON for the worker’s own latch too', async () => {
    // The switch answers "is the app calling the server", and under either
    // offline mode it is not. The two are told apart in the copy, not in
    // the control's position — a switch that reads OFF while nothing
    // leaves the device would be the report disagreeing with the app.
    stubNetworkMode({ mode: 'offline' });
    const bootstrap = mountOfflinePage();

    runBootstrap(bootstrap);
    await settle();

    expect(switchInput().checked).toBe(true);
  });
});

describe('the way out', () => {
  it('asks for auto, then reloads, when switched off', async () => {
    const mode = stubNetworkMode({ mode: 'offline-forced' });
    const bootstrap = mountOfflinePage();
    runBootstrap(bootstrap);
    await settle();

    switchInput().checked = false;
    switchInput().dispatchEvent(new Event('change'));
    await settle();

    expect(mode.set).toHaveBeenCalledWith('auto');
    // Only after `set` resolves. It persists before it announces, so
    // waiting is what stops the reload racing this device's own write and
    // coming back in the mode the user just left.
    expect(window.location.reload).toHaveBeenCalled();
  });

  it('asks the worker nothing on the way out', async () => {
    // The recovery direction. Nothing belongs between a stranded user and
    // the network — least of all a question that can take three seconds to
    // answer.
    const mode = stubNetworkMode({ mode: 'offline-forced' });
    const bootstrap = mountOfflinePage();
    runBootstrap(bootstrap);
    await settle();

    switchInput().checked = false;
    switchInput().dispatchEvent(new Event('change'));
    await settle();

    expect(mode.canOpenOffline).not.toHaveBeenCalled();
  });
});

describe('the guard on the way in', () => {
  it('warns, and stands down, when the app is not saved and the user declines', async () => {
    const mode = stubNetworkMode({ mode: 'auto', canOpen: false });
    window.confirm = vi.fn().mockReturnValue(false);
    const bootstrap = mountOfflinePage();
    runBootstrap(bootstrap);
    await settle();

    switchInput().checked = true;
    switchInput().dispatchEvent(new Event('change'));
    await settle();

    expect(window.confirm).toHaveBeenCalled();
    expect(mode.set).not.toHaveBeenCalled();
    expect(switchInput().checked).toBe(false);
    expect(switchInput().disabled).toBe(false);
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it('does not warn when the worker says the app is saved', async () => {
    const mode = stubNetworkMode({ mode: 'auto', canOpen: true });
    window.confirm = vi.fn().mockReturnValue(false);
    const bootstrap = mountOfflinePage();
    runBootstrap(bootstrap);
    await settle();

    switchInput().checked = true;
    switchInput().dispatchEvent(new Event('change'));
    await settle();

    expect(window.confirm).not.toHaveBeenCalled();
    expect(mode.set).toHaveBeenCalledWith('offline-forced');
  });

  it('proceeds when the user says yes anyway', async () => {
    // The warning names the consequence; it does not overrule anyone.
    const mode = stubNetworkMode({ mode: 'auto', canOpen: false });
    window.confirm = vi.fn().mockReturnValue(true);
    const bootstrap = mountOfflinePage();
    runBootstrap(bootstrap);
    await settle();

    switchInput().checked = true;
    switchInput().dispatchEvent(new Event('change'));
    await settle();

    expect(mode.set).toHaveBeenCalledWith('offline-forced');
  });
});
