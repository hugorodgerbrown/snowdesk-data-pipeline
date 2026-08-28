/*
 * tests/js/test_sw_register_update_feedback.js — what the update banner
 * shows when it is revealed, and what it does when Reload is pressed.
 *
 * Two defects, one surface:
 *
 *   * The click changed nothing on screen. Both reload paths take a
 *     moment — the SW path posts SKIP_WAITING and waits for activation,
 *     backstopped by a three-second timer — and for that whole stretch
 *     the only feedback was a button that had stopped responding, which
 *     reads as a dead control rather than a busy one.
 *   * "Update available" named neither the build you were on nor the one
 *     you were about to get. Neither side can render that alone: the
 *     shell's build is in its own <meta>, the server's only in
 *     /api/version. `fillBuildLine` composes them.
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
 *
 * The probe path (a reveal with no verdict, borrowing
 * `window.pwaVersionProbe`) lives in its own file — this module fills the
 * line once per page by design, so a second scenario needs a second
 * window.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** The build this shell was "delivered on", per its <meta> tag. */
const SHELL_BUILD = '0123456789abcdef0123456789abcdef01234567';

/** Payloads posted to the waiting worker by the reload handler. */
const posted = [];

const waitingWorker = {
  state: 'installed',
  postMessage: (msg) => posted.push(msg),
};

/**
 * The public banner as `_sw_update_banner.html` renders it — the ids the
 * module binds to, the icon hook it spins, and the empty build-line slot.
 *
 * @returns {string}
 */
function bannerMarkup() {
  return `
    <div id="sw-update-banner" class="hidden" role="status" aria-live="polite">
      <span data-overlay-icon aria-hidden="true"></span>
      <p id="sw-update-banner-title">Update available</p>
      <p id="sw-update-banner-body">A newer version of Snowdesk is ready.</p>
      <p id="sw-update-banner-versions" hidden></p>
      <button type="button" id="sw-update-banner-reload">Reload</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
}

/** @returns {HTMLElement} the build-line slot. */
const line = () => document.getElementById('sw-update-banner-versions');
/** @returns {HTMLButtonElement} the Reload CTA. */
const cta = () => document.getElementById('sw-update-banner-reload');

beforeAll(async () => {
  document.head.innerHTML = `
    <meta name="pwa-app-version" content="${SHELL_BUILD}">
    <meta name="pwa-app-released-at" content="2026-08-27T09:12:00+00:00">`;
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

describe('the build line', () => {
  it('stays hidden when the server is serving the build we already have', () => {
    // The SW-driven case: a worker is waiting because `sw.js` itself
    // changed, and the server's APP_VERSION has not moved. "abc → abc"
    // would contradict the banner sitting above it.
    window.pwaUpdateBanner.reveal({
      current: SHELL_BUILD,
      released_at: '2026-08-27T09:12:00+00:00',
    });

    expect(line().hidden).toBe(true);
    expect(line().textContent).toBe('');
  });

  it('names both builds, shortened, once a real drift is confirmed', () => {
    window.pwaUpdateBanner.reveal({
      current: 'fedcba9876543210fedcba9876543210fedcba98',
      released_at: '2026-08-28T14:03:00+00:00',
    });

    expect(line().hidden).toBe(false);
    // Seven characters, the abbreviation GitHub itself uses — forty in a
    // toast is noise.
    expect(line().textContent).toContain('0123456');
    expect(line().textContent).toContain('fedcba9');
    expect(line().textContent).toContain('→');
    // Each half is dated, so the pair reads as a delta rather than as two
    // opaque hashes.
    expect(line().textContent).toMatch(/\(.+\).+→.+\(.+\)/);
  });

  it('is written once — a re-reveal does not rewrite it', () => {
    const written = line().textContent;

    window.pwaUpdateBanner.reveal({ current: 'aaaaaaa', released_at: '' });

    expect(line().textContent).toBe(written);
  });
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
