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
 *
 * SNOW-869 added the banner's other copy state — the one naming both
 * builds — and its tests run FIRST in this file for the same
 * single-import reason: `showBannerBusy` latches, and once the Reload
 * test has fired the labelling is deliberately inert.
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

/** Restore the copy the template renders, before a case that must keep it. */
function resetCopy() {
  document.getElementById('sw-update-banner-title').textContent = 'Update available';
  document.getElementById('sw-update-banner-body').textContent =
    'A newer version of Snowdesk is ready.';
}

/**
 * Stand in for `pwa_version_check.js`'s export.
 *
 * @param {string} release The shell's own release label ('' when unnumbered).
 * @param {string} build The shell's own build id.
 * @param {object | null} verdict The `/api/version` body, or null for
 *   "could not confirm".
 * @returns {void}
 */
function stubVersionInfo(release, build, verdict) {
  window.pwaVersionInfo = Object.freeze({
    build: build,
    release: release,
    verified: () => Promise.resolve(verdict),
  });
}

/**
 * Install a controlling worker that answers `build-identity` with `reply`.
 *
 * @param {object | null} reply The message body to post back down the
 *   transferred port, or null for a worker that never answers at all.
 * @returns {void}
 */
function stubController(reply) {
  navigator.serviceWorker.controller = {
    postMessage: (data, transfer) => {
      if (!reply) return;
      if (!data || data.type !== 'build-identity') return;
      transfer[0].postMessage(reply);
    },
  };
}

/** Put the harness back to "no worker is controlling this page". */
function clearController() {
  navigator.serviceWorker.controller = null;
}

/** @returns {string} */
const titleText = () => document.getElementById('sw-update-banner-title').textContent;
/** @returns {string} */
const bodyText = () => document.getElementById('sw-update-banner-body').textContent;

describe('the copy rule', () => {
  /** The exported pure function — no DOM, no fetch. */
  const describeUpdate = (builds) => window.pwaUpdateBanner.describeUpdate(builds);

  it('prefers the release labels when they differ', () => {
    expect(
      describeUpdate({
        clientRelease: 'v29',
        serverRelease: 'v30',
        clientBuild: 'aaaaaaa1111',
        serverBuild: 'bbbbbbb2222',
      }),
    ).toEqual({ current: 'v29', next: 'v30' });
  });

  it('falls back to short SHAs when the labels are equal', () => {
    // Staging deploys between releases, so two genuinely different
    // builds can carry one label. The SHA is what tells them apart.
    expect(
      describeUpdate({
        clientRelease: 'v30',
        serverRelease: 'v30',
        clientBuild: 'aaaaaaa1111',
        serverBuild: 'bbbbbbb2222',
      }),
    ).toEqual({ current: 'aaaaaaa', next: 'bbbbbbb' });
  });

  it('falls back to short SHAs when there are no labels at all', () => {
    expect(
      describeUpdate({
        clientRelease: '',
        serverRelease: '',
        clientBuild: 'aaaaaaa1111',
        serverBuild: 'bbbbbbb2222',
      }),
    ).toEqual({ current: 'aaaaaaa', next: 'bbbbbbb' });
  });

  it('declines when nothing distinguishes the two builds', () => {
    // "You are on v30. Reload to update to v30." reads as a bug in the
    // update rather than as an update, so the caller keeps the
    // unnumbered copy instead.
    expect(
      describeUpdate({
        clientRelease: 'v30',
        serverRelease: 'v30',
        clientBuild: 'aaaaaaa1111',
        serverBuild: 'aaaaaaa1111',
      }),
    ).toBeNull();
  });

  it('declines when one side has no build at all', () => {
    expect(
      describeUpdate({
        clientRelease: '',
        serverRelease: 'v30',
        clientBuild: '',
        serverBuild: 'bbbbbbb2222',
      }),
    ).toBeNull();
  });
});

describe('the revealed banner', () => {
  it('names both releases when they differ', async () => {
    stubVersionInfo('v29', 'aaaaaaa1111', {
      current: 'bbbbbbb2222',
      release: 'v30',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (v30)'));
    expect(bodyText()).toBe('You are on v29. Reload to update to v30.');
  });

  it('names the short SHAs when the releases are equal', async () => {
    stubVersionInfo('v30', 'aaaaaaa1111', {
      current: 'bbbbbbb2222',
      release: 'v30',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (bbbbbbb)'));
    expect(bodyText()).toContain('You are on aaaaaaa.');
  });

  it('keeps the unnumbered copy when the body cannot be verified', async () => {
    resetCopy();
    stubVersionInfo('v29', 'aaaaaaa1111', null);

    window.pwaUpdateBanner.reveal();
    await Promise.resolve();
    await Promise.resolve();

    // An unreachable endpoint is "cannot confirm", never "confirmed" —
    // the banner does not name a build it could not check.
    expect(titleText()).toBe('Update available');
    expect(bodyText()).toBe('A newer version of Snowdesk is ready.');
  });

  it('keeps the unnumbered copy when nothing distinguishes the builds', async () => {
    resetCopy();
    stubVersionInfo('v30', 'aaaaaaa1111', {
      current: 'aaaaaaa1111',
      release: 'v30',
      update_available: false,
    });

    window.pwaUpdateBanner.reveal();
    await Promise.resolve();
    await Promise.resolve();

    expect(titleText()).toBe('Update available');
  });

  it('keeps the unnumbered copy on a page with no version check', async () => {
    resetCopy();
    delete window.pwaVersionInfo;

    // Admin pages, and any page the version check did not load on.
    window.pwaUpdateBanner.reveal();
    await Promise.resolve();

    expect(titleText()).toBe('Update available');
  });
});

describe('which build the banner calls yours', () => {
  // The staging regression (SNOW-933). Every staging deploy shares one
  // release label, so the copy rule falls through to the SHAs — and
  // navigations are network-first, so the page already carries the NEW
  // build's meta while the OLD worker still controls it. Read from the
  // page, both SHAs match and the banner stays unnumbered forever.
  it('takes the controlling worker\'s build over the page\'s meta', async () => {
    resetCopy();
    stubController({ type: 'build-identity', build: 'aaaaaaa1111', release: 'v34' });
    // The page's own meta is already the server's build.
    stubVersionInfo('v34', 'bbbbbbb2222', {
      current: 'bbbbbbb2222',
      release: 'v34',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (bbbbbbb)'));
    expect(bodyText()).toBe('You are on aaaaaaa. Reload to update to bbbbbbb.');
    clearController();
  });

  it('takes the worker\'s release label too, not the page\'s', async () => {
    // Production's half of the same gap: a fresh v34 page controlled by
    // a v33 worker. The answer is taken whole — the worker's label with
    // the worker's build, never one paired with the other's.
    resetCopy();
    stubController({ type: 'build-identity', build: 'aaaaaaa1111', release: 'v33' });
    stubVersionInfo('v34', 'bbbbbbb2222', {
      current: 'bbbbbbb2222',
      release: 'v34',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (v34)'));
    expect(bodyText()).toBe('You are on v33. Reload to update to v34.');
    clearController();
  });

  it('falls back to the page meta when the worker answers something else', async () => {
    resetCopy();
    stubController({ type: 'not-the-answer' });
    stubVersionInfo('v29', 'aaaaaaa1111', {
      current: 'bbbbbbb2222',
      release: 'v30',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (v30)'));
    expect(bodyText()).toBe('You are on v29. Reload to update to v30.');
    clearController();
  });

  it('falls back to the page meta when the worker never answers', async () => {
    // A worker that predates the build-identity handler — which every
    // worker does on the first deploy carrying it. The read is bounded,
    // so it resolves rather than leaving the copy pending forever.
    resetCopy();
    stubController(null);
    stubVersionInfo('v29', 'aaaaaaa1111', {
      current: 'bbbbbbb2222',
      release: 'v30',
      update_available: true,
    });
    vi.useFakeTimers();

    window.pwaUpdateBanner.reveal();
    await vi.advanceTimersByTimeAsync(2000);
    vi.useRealTimers();

    await vi.waitFor(() => expect(titleText()).toBe('Update available (v30)'));
    expect(bodyText()).toBe('You are on v29. Reload to update to v30.');
    clearController();
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

describe('once the update is applying', () => {
  it('does not let a late version body write the offer back', async () => {
    // The user clicked while the /api/version body was still in flight.
    // The busy copy owns the banner from that moment: putting "Update
    // available" back would claim the update had not started when it had.
    stubVersionInfo('v29', 'aaaaaaa1111', {
      current: 'bbbbbbb2222',
      release: 'v30',
      update_available: true,
    });

    window.pwaUpdateBanner.reveal();
    await Promise.resolve();
    await Promise.resolve();

    expect(titleText()).toBe('Updating Snowdesk');
  });
});
