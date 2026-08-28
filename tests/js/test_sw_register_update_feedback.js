/*
 * tests/js/test_sw_register_update_feedback.js — what the update banner
 * shows when it is revealed, and what it does when Reload is pressed.
 *
 * The defect: the click changed nothing on screen. Both reload paths take
 * a moment — the SW path posts SKIP_WAITING and waits for activation,
 * backstopped by a three-second timer — and for that whole stretch the
 * only feedback was a button that had stopped responding, which reads as
 * a dead control rather than a busy one.
 *
 * Harness notes
 * -------------
 * `sw_register.js` is a load-time IIFE that resolves `#sw-update-banner`
 * ONCE and defines non-configurable globals, so it can be imported only
 * once per jsdom window — the full public markup and both <meta> tags go
 * in before the import, and the tests drive it through the DOM and
 * `window.pwaUpdateBanner` afterwards.
 *
 * The click test deliberately leaves a WAITING worker in place: that path
 * ends in a timer rather than in `location.reload()`, which jsdom does not
 * implement. The busy state is set before any of it, which is the point.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** Payloads posted to the waiting worker by the reload handler. */
const posted = [];

const waitingWorker = {
  state: 'installed',
  postMessage: (msg) => posted.push(msg),
};

/**
 * The public banner as `_sw_update_banner.html` renders it — the ids the
 * module binds to and the icon hook it spins.
 *
 * @returns {string}
 */
function bannerMarkup() {
  return `
    <div id="sw-update-banner" class="hidden" role="status" aria-live="polite">
      <span data-overlay-icon aria-hidden="true"></span>
      <p id="sw-update-banner-title">Update available</p>
      <p id="sw-update-banner-body">A newer version of Snowdesk is ready.</p>
      <button type="button" id="sw-update-banner-reload">Reload</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
}

/** @returns {HTMLButtonElement} the Reload CTA. */
const cta = () => document.getElementById('sw-update-banner-reload');

beforeAll(async () => {
  document.body.innerHTML = bannerMarkup();

  Object.defineProperty(navigator, 'serviceWorker', {
    value: {
      controller: null,
      register: () =>
        Promise.resolve({ waiting: null, installing: null, addEventListener: () => {} }),
      getRegistration: () => Promise.resolve({ waiting: waitingWorker }),
      getRegistrations: () => Promise.resolve([]),
      addEventListener: () => {},
      ready: Promise.resolve({ active: null }),
    },
    configurable: true,
  });
  // The IIFE fetches /api/sw-config before registering.
  vi.stubGlobal('fetch', () =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ enabled: true, sw_url: '/sw.js', kill: false }),
    }),
  );

  await import('../../static/js/sw_register.js');
});

describe('pressing Reload', () => {
  it('acknowledges the click before the update starts', async () => {
    cta().click();
    // The busy state is synchronous; the awaits in the handler come after.
    expect(cta().getAttribute('aria-busy')).toBe('true');
    expect(cta().hasAttribute('disabled')).toBe(true);
    expect(cta().textContent).toBe('Updating…');
    expect(document.getElementById('sw-update-banner-title').textContent).toBe(
      'Updating Snowdesk',
    );
    expect(document.getElementById('sw-update-banner-body').textContent).toContain(
      'reload',
    );
    expect(
      document.querySelector('[data-overlay-icon]').classList.contains(
        'motion-safe:animate-spin',
      ),
    ).toBe(true);

    // And it does go on to ask the waiting worker to take over.
    await vi.waitFor(() => expect(posted).toEqual([{ type: 'SKIP_WAITING' }]));
  });

  it('ignores a second press rather than re-posting SKIP_WAITING', () => {
    cta().click();

    expect(posted).toHaveLength(1);
  });
});
