/*
 * tests/js/test_pwa_install.js — Vitest unit tests for the install-nudge
 * eligibility gate and funnel telemetry in static/js/pwa_install.js
 * (SNOW-649).
 *
 * The gate decides when Snowdesk asks to be installed. Getting it wrong in
 * the permissive direction is the expensive failure: an install prompt on
 * first paint, before the user has seen anything, is the interruption every
 * user hates and the one browsers increasingly penalise. The 30-day
 * cool-off after a dismissal is the same promise in slower form — asking
 * again next week is how an app gets uninstalled rather than installed.
 *
 * None of that was unit-tested. The e2e suite asserted the telemetry
 * emissions (test_pwa_client_signals.py's four install tests) but not the
 * gate, and a browser test cannot practically cover the cool-off at all
 * without either freezing time or waiting a month.
 *
 * Everything is driven through the module's real entry points — a
 * `beforeinstallprompt` event and the banner's own controls — because the
 * module exports nothing. localStorage is the only state, so each test
 * seeds it and re-hides the banner.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const REGIONS_KEY = 'pwa.install.regions_viewed';
const TIME_KEY = 'pwa.install.time_engaged_ms';
const DISMISS_KEY = 'pwa.install.dismissed_at';

const DAY_MS = 24 * 60 * 60 * 1000;

document.body.innerHTML = `
  <div id="pwa-install-banner" class="hidden" data-overlay>
    <button id="pwa-install-accept">Install</button>
    <button data-action="dismiss">×</button>
  </div>
  <div id="pwa-install-ios" class="hidden" data-overlay></div>
`;

await import('../../static/js/pwa_install.js');

const banner = document.getElementById('pwa-install-banner');
const acceptBtn = document.getElementById('pwa-install-accept');

/** A fake beforeinstallprompt event with a controllable user choice. */
function installPromptEvent(outcome = 'accepted') {
  const evt = new Event('beforeinstallprompt');
  evt.prompt = vi.fn();
  evt.userChoice = Promise.resolve({ outcome });
  return evt;
}

/** Fire beforeinstallprompt, which defers the prompt and re-checks the gate. */
function offerInstall(outcome) {
  const evt = installPromptEvent(outcome);
  window.dispatchEvent(evt);
  return evt;
}

/** Every telemetry event name emitted so far. */
function emittedNames() {
  return window.pwaTelemetry.emit.mock.calls.map((c) => c[0]);
}

beforeEach(() => {
  localStorage.clear();
  banner.classList.add('hidden');
  document.getElementById('pwa-install-ios').classList.add('hidden');
  window.pwaTelemetry = { emit: vi.fn() };
});

afterEach(() => {
  delete window.pwaTelemetry;
  delete window.matchMedia;
});

describe('the gate stays shut until the user has actually engaged', () => {
  it('does not reveal the banner on a fresh visit', () => {
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });

  it('emits nothing when it does not prompt', () => {
    offerInstall();
    expect(emittedNames()).toEqual([]);
  });

  it('stays shut after a single region view — one is not a habit', () => {
    localStorage.setItem(REGIONS_KEY, JSON.stringify(['ch-4115']));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });

  it('stays shut just under the engagement threshold', () => {
    localStorage.setItem(TIME_KEY, String(29_999));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });
});

describe('the gate opens on either threshold', () => {
  it('opens after two distinct regions', () => {
    localStorage.setItem(REGIONS_KEY, JSON.stringify(['ch-4115', 'ch-1221']));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(false);
  });

  it('opens at exactly 30 seconds of engagement', () => {
    localStorage.setItem(TIME_KEY, String(30_000));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(false);
  });

  it('emits pwa.install.prompted naming which banner was shown', () => {
    localStorage.setItem(TIME_KEY, String(60_000));
    offerInstall();
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.install.prompted',
      expect.objectContaining({ banner: 'pwa-install-banner' }),
    );
  });

  it('does not re-emit prompted when the banner is already showing', () => {
    localStorage.setItem(TIME_KEY, String(60_000));
    offerInstall();
    offerInstall();
    expect(emittedNames().filter((n) => n === 'pwa.install.prompted')).toHaveLength(1);
  });
});

describe('the 30-day cool-off after a dismissal', () => {
  it('suppresses the nudge immediately after a dismissal', () => {
    localStorage.setItem(TIME_KEY, String(60_000));
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });

  it('still suppresses it 29 days later', () => {
    localStorage.setItem(TIME_KEY, String(60_000));
    localStorage.setItem(DISMISS_KEY, String(Date.now() - 29 * DAY_MS));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });

  it('lets the nudge through once 31 days have passed', () => {
    localStorage.setItem(TIME_KEY, String(60_000));
    localStorage.setItem(DISMISS_KEY, String(Date.now() - 31 * DAY_MS));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(false);
  });

  it('treats a corrupt timestamp as no dismissal rather than a permanent block', () => {
    // Fail-open here, unlike most guards: a garbage value must not silently
    // suppress the nudge forever with no way to recover.
    localStorage.setItem(TIME_KEY, String(60_000));
    localStorage.setItem(DISMISS_KEY, 'not-a-number');
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(false);
  });
});

describe('an already-installed shell never nudges', () => {
  it('stays shut in standalone display-mode even past both thresholds', () => {
    window.matchMedia = () => ({ matches: true });
    localStorage.setItem(REGIONS_KEY, JSON.stringify(['ch-4115', 'ch-1221']));
    localStorage.setItem(TIME_KEY, String(60_000));
    offerInstall();
    expect(banner.classList.contains('hidden')).toBe(true);
  });
});

describe('accepting the prompt', () => {
  beforeEach(() => {
    localStorage.setItem(TIME_KEY, String(60_000));
  });

  it('shows the browser prompt rather than installing silently', async () => {
    const evt = offerInstall('accepted');
    acceptBtn.click();
    await vi.waitFor(() => expect(evt.prompt).toHaveBeenCalled());
  });

  it('emits pwa.install.accepted on an accepted outcome', async () => {
    offerInstall('accepted');
    acceptBtn.click();
    await vi.waitFor(() => expect(emittedNames()).toContain('pwa.install.accepted'));
  });

  it('treats a dismissed outcome as a dismissal, cool-off and all', async () => {
    offerInstall('dismissed');
    acceptBtn.click();
    await vi.waitFor(() => expect(emittedNames()).toContain('pwa.install.dismissed'));
    expect(localStorage.getItem(DISMISS_KEY)).toBeTruthy();
  });

  it('degrades to a dismissal when prompt() throws', async () => {
    const evt = installPromptEvent('accepted');
    evt.prompt = () => {
      throw new Error('user gesture expired');
    };
    window.dispatchEvent(evt);
    acceptBtn.click();
    await vi.waitFor(() => expect(emittedNames()).toContain('pwa.install.dismissed'));
  });
});

describe('dismissing via the shared overlay handler (SNOW-486)', () => {
  it('stamps the cool-off so overlays.js owning the hide does not lose it', () => {
    document.dispatchEvent(
      new CustomEvent('overlay:dismissed', { detail: { overlay: banner } }),
    );
    expect(localStorage.getItem(DISMISS_KEY)).toBeTruthy();
  });

  it('emits pwa.install.dismissed naming the banner', () => {
    document.dispatchEvent(
      new CustomEvent('overlay:dismissed', { detail: { overlay: banner } }),
    );
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.install.dismissed',
      expect.objectContaining({ banner: 'pwa-install-banner' }),
    );
  });

  it('ignores dismissals of unrelated overlays', () => {
    const other = document.createElement('div');
    other.id = 'map-download-evict-confirm';
    document.dispatchEvent(
      new CustomEvent('overlay:dismissed', { detail: { overlay: other } }),
    );
    expect(localStorage.getItem(DISMISS_KEY)).toBeNull();
    expect(emittedNames()).toEqual([]);
  });
});
