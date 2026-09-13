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
 * SNOW-932 adds `contentOutcome`, the same kind of predicate for the other
 * half of a download. The tile half decides whether the map DRAWS; the
 * content half decides whether the bulletins on it are today's. It lives
 * beside `downloadSucceeded` in the core for the reason that one does —
 * three controls had inlined it — and it is tested beside it here.
 *
 * `basemap_download_core.js` is a browser IIFE with no exports — importing
 * it for side effects publishes a frozen `self.pwaBasemapDownloadCore`.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const { downloadSucceeded, contentOutcome } = self.pwaBasemapDownloadCore;

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

describe('contentOutcome — the amber-roundel predicate (SNOW-932)', () => {
  it('calls a fully-landed content half complete', () => {
    expect(contentOutcome({ ok: 9, total: 9 })).toEqual({
      complete: true,
      incomplete: false,
    });
  });

  it('calls a half-landed one incomplete', () => {
    expect(contentOutcome({ ok: 4, total: 9 })).toEqual({
      complete: false,
      incomplete: true,
    });
  });

  it('says NEITHER when there was nothing to fetch', () => {
    // The third state, and the whole reason this returns a pair rather
    // than one boolean: an area whose boundary contains no bulletins, or a
    // shell whose deps bundle predates the content phase, has nothing to
    // say. Recording `contentIncomplete` for it would strand it amber with
    // no shortfall behind it, and recording `contentAt` would claim a
    // freshness it never had.
    expect(contentOutcome({ ok: 0, total: 0 })).toEqual({
      complete: false,
      incomplete: false,
    });
  });

  it('calls a SHORT plan incomplete even on a clean tally', () => {
    // SNOW-931's channel. Every url the plan named landed, and the plan was
    // still missing a country's bulletins — which is a shortfall no tally
    // over that list can ever see, because there was nothing in the list to
    // fail. This is the clause that makes `incomplete` not the negation of
    // `complete`.
    expect(contentOutcome({ ok: 9, total: 9, short: true })).toEqual({
      complete: false,
      incomplete: true,
    });
  });

  it('calls a short plan with nothing in it incomplete too', () => {
    // The list is empty BECAUSE the countries never arrived. "Nothing to
    // say" is the wrong reading here — there was plenty to say and none of
    // it could be resolved.
    expect(contentOutcome({ ok: 0, total: 0, short: true })).toEqual({
      complete: false,
      incomplete: true,
    });
  });

  it('reads an absent tally as nothing to say, not as a failure', () => {
    // An older shell mid-rollout whose runner reports no `content` extra.
    // Both false is the pre-SNOW-924 behaviour: no stamp, no flag.
    for (const input of [null, undefined, {}]) {
      expect(contentOutcome(input)).toEqual({ complete: false, incomplete: false });
    }
  });

  it('never returns a truthy non-boolean on either member', () => {
    // Both feed a conditional record spread, where a truthy non-boolean
    // would be written into IndexedDB as itself.
    for (const input of [null, {}, { ok: 1, total: 1 }, { ok: 0, total: 3 }]) {
      const outcome = contentOutcome(input);
      expect(typeof outcome.complete).toBe('boolean');
      expect(typeof outcome.incomplete).toBe('boolean');
    }
  });

  it('is never both at once', () => {
    const inputs = [
      { ok: 0, total: 0 },
      { ok: 3, total: 3 },
      { ok: 1, total: 3 },
      { ok: 3, total: 3, short: true },
      { ok: 0, total: 0, short: true },
    ];
    for (const input of inputs) {
      const outcome = contentOutcome(input);
      expect(outcome.complete && outcome.incomplete).toBe(false);
    }
  });
});
