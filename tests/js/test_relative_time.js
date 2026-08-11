/*
 * tests/js/test_relative_time.js — Vitest DOM tests for
 * static/js/relative_time.js.
 *
 * The module exists for one scenario, and it is the one this file leads
 * with: a report filed an hour ago, a phone in a pocket for four hours
 * with no signal, and a panel that must not still claim "1 hour ago" when
 * it comes back out. Nothing re-renders in that gap — the list endpoint is
 * network-only — so the correction has to be client-side arithmetic, and
 * it has to be triggered by the page waking rather than by a timer that a
 * frozen page never ran.
 *
 * The rest covers what a re-implementation would get wrong: leaving the
 * server's text alone when there is nothing better to say (no datetime, an
 * unparseable one, no Intl support), and not touching elements that never
 * opted in.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Load the module fresh against the current fake clock.
 *
 * The module refreshes and starts its interval at import time, so each
 * test needs its own instance — hence resetModules + a dynamic import
 * rather than a top-level one.
 *
 * @returns {Promise<void>}
 */
async function load() {
  vi.resetModules();
  await import('../../static/js/relative_time.js');
}

/** Render one row carrying a relative timestamp.
 *
 * Mirrors observations/partials/_observation_meta.html: the server writes
 * both the instant and its own rendering of the age.
 *
 * @param {string} iso The instant, as the server would stamp it.
 * @param {string} serverText The age as the server rendered it.
 * @returns {HTMLElement} The `<time>` element.
 */
function buildRow(iso, serverText) {
  document.body.innerHTML = `
    <span data-row-meta>
      Martigny-Verbier · reported
      <time datetime="${iso}" data-relative-time>${serverText}</time>
    </span>`;
  return document.querySelector('time');
}

const HOUR = 3600 * 1000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-08-11T12:00:00Z'));
  document.documentElement.setAttribute('lang', 'en');
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('relative_time', () => {
  it('corrects a stale age when the page comes back into view', async () => {
    // Rendered an hour after the report, then four hours in a pocket. A
    // frozen page fires no interval, so visibilitychange is the only
    // signal — and the arithmetic it does needs no network.
    const el = buildRow('2026-08-11T11:00:00Z', '1 hour ago');
    await load();
    expect(el.textContent).toBe('1 hour ago');

    vi.setSystemTime(new Date('2026-08-11T16:00:00Z'));
    document.dispatchEvent(new Event('visibilitychange'));

    expect(el.textContent).toBe('5 hours ago');
  });

  it('corrects on bfcache restore, which fires no visibilitychange', async () => {
    const el = buildRow('2026-08-11T11:00:00Z', '1 hour ago');
    await load();

    vi.setSystemTime(new Date('2026-08-11T16:00:00Z'));
    window.dispatchEvent(new Event('pageshow'));

    expect(el.textContent).toBe('5 hours ago');
  });

  it('ticks while the panel is being read', async () => {
    const el = buildRow('2026-08-11T11:58:00Z', '2 minutes ago');
    await load();

    // advanceTimersByTime moves the fake clock as well as firing the
    // interval, so this is five real minutes passing with the panel open.
    vi.advanceTimersByTime(5 * 60000);

    expect(el.textContent).toBe('7 minutes ago');
  });

  it('picks the unit from the size of the gap', async () => {
    await load();
    const format = window.pwaRelativeTime.format;
    expect(format(-30 * 1000)).toBe('30 seconds ago');
    expect(format(-45 * 60 * 1000)).toBe('45 minutes ago');
    expect(format(-5 * HOUR)).toBe('5 hours ago');
    expect(format(-3 * 24 * HOUR)).toBe('3 days ago');
    expect(format(-3 * 7 * 24 * HOUR)).toBe('3 weeks ago');
    expect(format(-200 * 24 * HOUR)).toBe('7 months ago');
  });

  it('never says "0 seconds ago" for a report just filed', async () => {
    await load();
    expect(window.pwaRelativeTime.format(-200)).toBe('1 second ago');
  });

  it('reads a clock-skewed future instant as the future', async () => {
    // report_submit accepts an observed_at up to five minutes ahead, so a
    // row can legitimately be in the future on a device whose clock runs
    // fast. Django's own naturaltime says "in 3 minutes" here too.
    await load();
    expect(window.pwaRelativeTime.format(3 * 60 * 1000)).toBe('in 3 minutes');
  });

  it('refreshes rows HTMX swaps in after the first pass', async () => {
    await load();
    const rows = document.createElement('div');
    document.body.appendChild(rows);
    rows.innerHTML =
      '<time datetime="2026-08-11T09:00:00Z" data-relative-time>stale</time>';

    document.dispatchEvent(
      Object.assign(new Event('htmx:afterSwap'), { detail: { target: rows } }),
    );

    expect(rows.querySelector('time').textContent).toBe('3 hours ago');
  });

  it('leaves an unparseable instant showing the server text', async () => {
    const el = buildRow('not-a-date', '1 hour ago');
    await load();
    expect(el.textContent).toBe('1 hour ago');
  });

  it('leaves elements that did not opt in alone', async () => {
    document.body.innerHTML =
      '<time datetime="2026-08-11T09:00:00Z">11 Aug 2026, 09:00</time>';
    await load();
    expect(document.querySelector('time').textContent).toBe('11 Aug 2026, 09:00');
  });

  it('keeps the server text where Intl.RelativeTimeFormat is missing', async () => {
    const original = Intl.RelativeTimeFormat;
    // @ts-expect-error — deleting a platform global for the duration.
    delete Intl.RelativeTimeFormat;
    try {
      await load();
      // Detached, and refreshed through this instance's own handle: every
      // earlier instance in this file still holds document-level listeners
      // (jsdom keeps those across resetModules), and one of them WOULD
      // format it — which is not what is under test here.
      const scope = document.createElement('div');
      scope.innerHTML =
        '<time datetime="2026-08-11T11:00:00Z" data-relative-time>1 hour ago</time>';

      window.pwaRelativeTime.refresh(scope);

      expect(scope.querySelector('time').textContent).toBe('1 hour ago');
    } finally {
      Intl.RelativeTimeFormat = original;
    }
  });
});
