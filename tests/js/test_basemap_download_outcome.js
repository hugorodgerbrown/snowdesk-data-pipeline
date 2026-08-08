/*
 * tests/js/test_basemap_download_outcome.js — Vitest unit tests for
 * `downloadSucceeded`, the "is this area actually available offline?"
 * predicate in static/js/basemap_download_core.js (SNOW-649).
 *
 * This is the seam basemap_download_runner.js's header flagged when the run
 * was extracted in SNOW-611: "the two `finish` implementations (`map.js`)
 * are the next pass's job, not this one." Until now the only coverage was
 * four Playwright tests in tests/e2e/test_cache_this_area.py driving a real
 * Chromium, a real service worker and a real MapLibre canvas to assert a
 * four-clause boolean.
 *
 * Getting this predicate wrong is not cosmetic. It decides whether the
 * roundel goes green, and a green roundel is a promise to the user that the
 * area works offline — a promise redeemed on a mountain with no signal. A
 * false positive here is worse than a failed download, because the user
 * stops worrying about it.
 *
 * `basemap_download_core.js` is a browser IIFE with no exports — importing
 * it for side effects publishes a frozen `self.pwaBasemapDownloadCore`.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const { downloadSucceeded } = self.pwaBasemapDownloadCore;

/**
 * Build a warm-cache worker reply.
 *
 * @param {Object} [overrides]
 * @returns {Object}
 */
function result(overrides) {
  return Object.assign({ ok: 12, failed: 0, bytes: 3_500_000 }, overrides || {});
}

describe('downloadSucceeded — the green-roundel predicate', () => {
  it('accepts a clean run: tiles landed and none failed', () => {
    expect(downloadSucceeded(result())).toBe(true);
  });

  it('accepts a single-tile run — one tile is still a complete area', () => {
    expect(downloadSucceeded(result({ ok: 1 }))).toBe(true);
  });

  it('rejects a partial run, because the missing tiles are the offline holes', () => {
    expect(downloadSucceeded(result({ ok: 400, failed: 1 }))).toBe(false);
  });

  it('rejects a wholly failed run', () => {
    expect(downloadSucceeded(result({ ok: 0, failed: 12 }))).toBe(false);
  });

  it('rejects a vacuous run — no tiles fetched is not a download', () => {
    expect(downloadSucceeded(result({ ok: 0, failed: 0 }))).toBe(false);
  });

  it('rejects a cancelled run even though its failed count is 0 (SNOW-632)', () => {
    // The trap this clause exists for: cancelling skips tiles rather than
    // failing them, so `failed` stays 0 and the run is otherwise
    // indistinguishable from a clean success.
    expect(downloadSucceeded(result({ cancelled: true }))).toBe(false);
  });

  it('rejects a cancelled run that had already banked successes', () => {
    expect(downloadSucceeded(result({ ok: 900, failed: 0, cancelled: true }))).toBe(false);
  });

  it('rejects null — the runner settles with null when no worker ran', () => {
    expect(downloadSucceeded(null)).toBe(false);
  });

  it('rejects undefined', () => {
    expect(downloadSucceeded(undefined)).toBe(false);
  });

  it('never returns a truthy non-boolean, whatever the input shape', () => {
    // `finish` feeds this straight into `progressFill.finish(ok)` and the
    // roundel state, both of which are boolean contracts.
    for (const input of [null, undefined, {}, result(), result({ failed: 3 })]) {
      expect(typeof downloadSucceeded(input)).toBe('boolean');
    }
  });
});

describe('downloadSucceeded — bytes never influence the verdict', () => {
  // SNOW-632 / the tile-origin gzip finding: a live tile response carries no
  // Content-Length under gzip, so a bucket measurement reads 0 in
  // production while passing any test whose fixture Response also omits the
  // header. `bytes` is recorded, but it must never be part of deciding
  // whether the download happened — a 0-byte reading on a real, complete
  // run would otherwise turn the roundel red for every user.

  it('accepts a complete run that reported zero bytes', () => {
    expect(downloadSucceeded(result({ bytes: 0 }))).toBe(true);
  });

  it('accepts a complete run with no bytes field at all', () => {
    const noBytes = result();
    delete noBytes.bytes;
    expect(downloadSucceeded(noBytes)).toBe(true);
  });

  it('still rejects a failed run that reported plenty of bytes', () => {
    expect(downloadSucceeded(result({ failed: 2, bytes: 90_000_000 }))).toBe(false);
  });
});

describe('downloadSucceeded — hostile shapes degrade to false', () => {
  // The result crosses a postMessage boundary from the service worker, so
  // its shape is not guaranteed by the type system or by a schema.

  it('rejects an empty object', () => {
    expect(downloadSucceeded({})).toBe(false);
  });

  it('rejects a negative ok count', () => {
    expect(downloadSucceeded(result({ ok: -1 }))).toBe(false);
  });

  it('rejects a string ok count rather than coercing it', () => {
    // '0' is truthy in JS; a `> 0` comparison coerces it to 0, so this
    // lands on false for the right reason.
    expect(downloadSucceeded(result({ ok: '0' }))).toBe(false);
  });

  it('rejects when failed is a non-zero string', () => {
    expect(downloadSucceeded(result({ failed: '3' }))).toBe(false);
  });

  it('treats any truthy cancelled marker as cancelled', () => {
    for (const marker of [true, 1, 'yes', {}]) {
      expect(downloadSucceeded(result({ cancelled: marker }))).toBe(false);
    }
  });
});
