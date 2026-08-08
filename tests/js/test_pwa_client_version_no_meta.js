/*
 * tests/js/test_pwa_client_version_no_meta.js — the fail-safe half of
 * static/js/pwa_client_version.js (SNOW-649).
 *
 * With no `pwa-app-version` meta tag — a stale cached shell, or a template
 * that dropped the tag — the module must return before wrapping
 * `window.fetch` at all. Stamping an empty header would be worse than
 * stamping nothing: the server call sites read the header's presence, so
 * `X-Client-Version: ""` is an assertion that the build is unknown rather
 * than an absence of information, and it would land in PostHog looking
 * exactly like the pre-SNOW-388 bug it was written to fix.
 *
 * Own file because the version is captured once at IIFE time and the early
 * return is unrecoverable — see test_pwa_client_version.js for the
 * populated case.
 */

import { describe, expect, it, vi } from 'vitest';

// No meta tag at all.
document.head.innerHTML = '';

const native = vi.fn(() => Promise.resolve(new Response('')));
window.fetch = native;

await import('../../static/js/pwa_client_version.js');

describe('no pwa-app-version meta tag', () => {
  it('leaves window.fetch completely unwrapped', () => {
    expect(window.fetch).toBe(native);
  });

  it('sends no version header on a same-origin request', async () => {
    await window.fetch('/api/regions.geojson');
    const init = native.mock.calls.at(-1)[1];
    const headers = new Headers((init && init.headers) || {});
    expect(headers.has('x-client-version')).toBe(false);
  });

  it('never stamps an empty header, which would read as "build unknown"', async () => {
    await window.fetch('/api/sw-config');
    const init = native.mock.calls.at(-1)[1];
    const headers = new Headers((init && init.headers) || {});
    expect(headers.get('x-client-version')).toBeNull();
  });

  it('registers no htmx:configRequest listener', () => {
    const detail = { path: '/partials/report/form/', headers: {} };
    document.body.dispatchEvent(
      new CustomEvent('htmx:configRequest', { detail, bubbles: true }),
    );
    expect(detail.headers).toEqual({});
  });
});
