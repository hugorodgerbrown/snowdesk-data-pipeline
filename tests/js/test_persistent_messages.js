/*
 * tests/js/test_persistent_messages.js — Vitest unit tests for
 * static/js/persistent_messages.js.
 *
 * The module is the server-side half of an admin banner's "×": overlays.js
 * hides the strip and fires `overlay:dismissed`, and this listens for that
 * and DELETEs the row's `data-dismiss-url` so the dismissal is recorded
 * against the reader's account.
 *
 * Both modules are imported for side effects — each is a browser IIFE that
 * self-wires a `document` listener at import time — so these tests drive
 * the real pair through a real click, not a synthesised event. That
 * matters: the contract under test is "the attribute overlays.js hands us
 * in its event detail is the one we read", and a hand-built event would
 * assert only that this module reads its own detail correctly.
 *
 * `fetch` is stubbed rather than mocked at the module boundary: the module
 * has no exports and no injection point, which is the point of it being an
 * IIFE.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/overlays.js';
import '../../static/js/persistent_messages.js';

/**
 * Render one admin banner strip and return its element.
 *
 * Mirrors what includes/_overlay_banner.html emits for the "strip" variant
 * with `dismissible` — the class hide idiom, and `data-dismiss-url` only
 * when the reader's dismissal can be recorded.
 *
 * @param {{id?: string, dismissUrl?: string|null}} options
 * @returns {HTMLElement}
 */
function bannerFixture({ id = 'pmid-1', dismissUrl = '/messages/dismiss/1/' } = {}) {
  const urlAttr = dismissUrl === null ? '' : ` data-dismiss-url="${dismissUrl}"`;
  document.body.innerHTML = `
    <div id="${id}" role="note" data-overlay data-overlay-hide="class"${urlAttr}>
      <span>Scheduled maintenance on Sunday.</span>
      <button type="button" data-action="dismiss">&times;</button>
    </div>
  `;
  return document.getElementById(id);
}

/** Click the fixture's "×". */
function dismiss(banner) {
  banner.querySelector('[data-action="dismiss"]').click();
}

beforeEach(() => {
  document.body.innerHTML = '';
  // `keepalive` means the module never awaits the response, so a resolved
  // promise is all a passing path needs.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ status: 204 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('recording a dismissal', () => {
  it('DELETEs the banner dismiss URL when the reader is signed in', () => {
    const banner = bannerFixture();

    dismiss(banner);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/messages/dismiss/1/');
    expect(init.method).toBe('DELETE');
    // Cookies, or the endpoint cannot know who dismissed it.
    expect(init.credentials).toBe('same-origin');
    // Survives the reader dismissing and navigating in the same gesture.
    expect(init.keepalive).toBe(true);
  });

  it('sends nothing for an anonymous reader, who has no dismiss URL', () => {
    // apps.public.banners.dismiss_url_for renders no attribute unless the
    // dismissal can be recorded, so its absence IS the anonymous case —
    // the endpoint is login_required and a DELETE would only be redirected.
    const banner = bannerFixture({ dismissUrl: null });

    dismiss(banner);

    expect(fetch).not.toHaveBeenCalled();
  });

  it('still hides the strip when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline'))),
    );
    const banner = bannerFixture();

    dismiss(banner);
    // Let the rejection settle — an unhandled rejection here would fail the
    // run, which is the assertion: the module swallows it.
    await Promise.resolve();

    expect(banner.classList.contains('hidden')).toBe(true);
  });

  it('records only the banner that was dismissed', () => {
    document.body.innerHTML = `
      <div id="pmid-1" data-overlay data-overlay-hide="class" data-dismiss-url="/messages/dismiss/1/">
        <button type="button" data-action="dismiss">&times;</button>
      </div>
      <div id="pmid-2" data-overlay data-overlay-hide="class" data-dismiss-url="/messages/dismiss/2/">
        <button type="button" data-action="dismiss">&times;</button>
      </div>
    `;

    dismiss(document.getElementById('pmid-2'));

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('/messages/dismiss/2/');
    expect(document.getElementById('pmid-1').classList.contains('hidden')).toBe(false);
  });

  it('ignores an overlay:dismissed from a banner with no dismiss URL', () => {
    // Every other overlay on the page — the SW update banner, the install
    // nudge, the map sheets — fires the same event through the same shared
    // handler. None of them carry the attribute, and none of them should
    // cost a request.
    document.body.innerHTML = `
      <div id="sw-update-banner" data-overlay data-overlay-hide="class">
        <button type="button" data-action="dismiss">&times;</button>
      </div>
    `;

    dismiss(document.getElementById('sw-update-banner'));

    expect(fetch).not.toHaveBeenCalled();
  });
});
