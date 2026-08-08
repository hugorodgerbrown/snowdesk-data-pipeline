/*
 * tests/js/test_pwa_client_version.js — Vitest unit tests for
 * static/js/pwa_client_version.js (SNOW-388, covered under SNOW-649).
 *
 * This module stamps ``X-Client-Version`` on every same-origin request the
 * page issues, so four server-side ``emit_server_signal`` call sites can
 * attribute a signal to the shell build that produced it. Before it
 * existed the property landed in PostHog as ``""`` for everything.
 *
 * The two properties worth guarding are opposites, and both are quiet
 * failures:
 *
 *   - Under-stamping puts the analytics back to ``""`` and nothing breaks
 *     visibly — the data just stops being able to answer "which build?".
 *   - OVER-stamping leaks the build version to third-party origins on
 *     every cross-origin request the page makes. That is a header nobody
 *     outside this origin has any business receiving, and it would never
 *     show up as a bug report.
 *
 * The e2e suite covered this with three Playwright tests
 * (test_pwa_client_signals.py) that needed a real page, a real service
 * worker and a live server to assert which headers a request carried.
 * Everything here is a wrapped ``window.fetch`` and one HTMX event.
 *
 * The module reads the version from a meta tag ONCE at IIFE time and
 * returns early when it is absent, so the missing-tag case needs its own
 * file — see test_pwa_client_version_no_meta.js.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const VERSION = '2026.08.07.1';
const HEADER = 'x-client-version';

document.head.innerHTML = `<meta name="pwa-app-version" content="${VERSION}">`;

/** Records what the wrapped fetch finally handed to the native one. */
let native;

// Installed BEFORE the import so the module wraps this, not jsdom's fetch —
// the same "first wrapper is innermost" ordering home.html relies on.
native = vi.fn(() => Promise.resolve(new Response('')));
window.fetch = native;

await import('../../static/js/pwa_client_version.js');

/** The headers the native fetch actually received, as a Headers object. */
function sentHeaders() {
  const [resource, init] = native.mock.calls.at(-1);
  if (resource instanceof Request) return resource.headers;
  return new Headers((init && init.headers) || {});
}

beforeEach(() => {
  native.mockClear();
});

describe('fetch(url, init) — the common shape', () => {
  it('stamps the version on a relative path', async () => {
    await window.fetch('/api/regions.geojson');
    expect(sentHeaders().get(HEADER)).toBe(VERSION);
  });

  it('stamps the version on an absolute same-origin URL', async () => {
    await window.fetch(`${location.origin}/api/sw-config`);
    expect(sentHeaders().get(HEADER)).toBe(VERSION);
  });

  it('leaves a third-party request unstamped', async () => {
    // The leak this guards: the tile origin and Open-Meteo are both
    // cross-origin callees on the map page.
    await window.fetch('https://tiles.example.invalid/12/1/1.pbf');
    expect(sentHeaders().has(HEADER)).toBe(false);
  });

  it('preserves the caller’s other headers', async () => {
    await window.fetch('/api/telemetry', {
      headers: { 'Content-Type': 'application/json' },
    });
    const headers = sentHeaders();
    expect(headers.get('content-type')).toBe('application/json');
    expect(headers.get(HEADER)).toBe(VERSION);
  });

  it('does not clobber a version a caller set explicitly', async () => {
    await window.fetch('/api/version', { headers: { [HEADER]: '1999.01.01' } });
    expect(sentHeaders().get(HEADER)).toBe('1999.01.01');
  });

  it('preserves the rest of init, so method and body survive the rebuild', async () => {
    await window.fetch('/api/telemetry', { method: 'POST', body: '{"a":1}' });
    const init = native.mock.calls.at(-1)[1];
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"a":1}');
  });

  it('fails closed on a malformed URL rather than stamping it', async () => {
    await window.fetch('http://[not a url]/x');
    expect(sentHeaders().has(HEADER)).toBe(false);
  });
});

describe('fetch(request) — the Request shape', () => {
  // Node's Request (undici) rejects relative URLs, unlike a browser's, so
  // these build absolute same-origin URLs. The module resolves both the
  // same way — isSameOrigin() runs `new URL(url, location.href)`.
  it('stamps a same-origin Request by rebuilding it (headers are immutable)', async () => {
    await window.fetch(new Request(`${location.origin}/api/regions.geojson`));
    expect(sentHeaders().get(HEADER)).toBe(VERSION);
  });

  it('leaves a cross-origin Request untouched', async () => {
    await window.fetch(new Request('https://tiles.example.invalid/12/1/1.pbf'));
    expect(sentHeaders().has(HEADER)).toBe(false);
  });

  it('does not clobber a version already on the Request', async () => {
    const request = new Request(`${location.origin}/api/version`, {
      headers: { [HEADER]: '1999.01.01' },
    });
    await window.fetch(request);
    expect(sentHeaders().get(HEADER)).toBe('1999.01.01');
  });

  it('keeps the Request’s method through the rebuild', async () => {
    await window.fetch(new Request(`${location.origin}/api/telemetry`, { method: 'POST' }));
    expect(native.mock.calls.at(-1)[0].method).toBe('POST');
  });

  it('still passes a Request through, not a URL string', async () => {
    await window.fetch(new Request(`${location.origin}/api/regions.geojson`));
    expect(native.mock.calls.at(-1)[0]).toBeInstanceOf(Request);
  });
});

describe('HTMX requests — XHR, so the fetch wrapper never sees them', () => {
  /**
   * Fire the htmx:configRequest hook the module listens for.
   *
   * @param {string} path
   * @param {Object} [headers]
   * @returns {Object} the (possibly mutated) headers object
   */
  function configRequest(path, headers) {
    const detail = { path, headers: headers || {} };
    document.body.dispatchEvent(
      new CustomEvent('htmx:configRequest', { detail, bubbles: true }),
    );
    return detail.headers;
  }

  it('stamps a same-origin HTMX path', () => {
    expect(configRequest('/partials/report/form/')['X-Client-Version']).toBe(VERSION);
  });

  it('leaves a cross-origin HTMX path unstamped', () => {
    expect(configRequest('https://example.invalid/x')['X-Client-Version']).toBeUndefined();
  });

  it('does not clobber a version already on the request', () => {
    const headers = configRequest('/partials/report/form/', {
      'X-Client-Version': '1999.01.01',
    });
    expect(headers['X-Client-Version']).toBe('1999.01.01');
  });

  it('ignores an event with no path rather than throwing', () => {
    expect(() => configRequest(undefined)).not.toThrow();
  });
});
