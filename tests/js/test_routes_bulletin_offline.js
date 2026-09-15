/*
 * tests/js/test_routes_bulletin_offline.js — one route's reading of one
 * day's bulletin, off signal (SNOW-973,
 * static/js/routes_bulletin_offline.js).
 *
 * The module is storage plus one judgement: it writes what came back from
 * ``routes:bulletin`` with the freshness envelope the response carried,
 * reads it out again for the account that wrote it, and says whether the
 * row is past its own horizon. What ``map_route_detail.js`` then paints is
 * covered in tests/js/test_map_route_detail_panel.js.
 *
 * THE EXPIRY CASE IS THE ONE THAT MATTERS, and until this ticket the
 * project had no test of it anywhere: favourites_offline.js has carried the
 * same rule since SNOW-418, and every fixture in
 * tests/js/test_favourites_offline.js sets ``unsafe_after_seconds: null``,
 * so the branch that refuses to repaint a stale danger reading has never
 * been exercised. A bulletin reading is the same class of claim, so it is
 * asserted here from both sides — inside the horizon it reads back, past it
 * the caller is told it has expired.
 *
 * The principal rules are SNOW-493's, and the signed-in half is in
 * tests/js/test_routes_bulletin_offline_principal.js: db.js's ``context()``
 * memoises <meta name="pwa-user-id"> for the module's lifetime, so a
 * non-anonymous principal needs its own file.
 */

import { beforeEach, describe, expect, it } from 'vitest';

// db.js first: it assigns window.pwaDb, which every entry point below reads
// through — the same document order the page's deferred script tags give.
await import('../../static/js/db.js');
await import('../../static/js/routes_bulletin_offline.js');

const STORE = 'data:route_bulletins';
const UUID = '11111111-2222-3333-4444-555555555555';
const DAY = '2026-03-01';
const KEY = `${UUID}:${DAY}`;
const BODY = '<div data-testid="route-bulletin">Aletsch · 1.4 km on N</div>';
const GENERATED = '2026-03-01T05:00:00+00:00';
const UNSAFE_AFTER = 48 * 60 * 60;

/** The module under test. */
const offline = () => window.pwaRoutesBulletinOffline;

beforeEach(async () => {
  await window.pwaDb.delete(STORE, KEY);
});

describe('the key', () => {
  it('is the route AND the day', () => {
    // A reading belongs to one day. Keyed by route alone, yesterday's
    // reading would be repainted under today's date, which is the exact
    // failure ``/api/ratings`` is date-keyed to avoid.
    expect(offline().keyFor(UUID, DAY)).toBe(KEY);
    expect(offline().keyFor(UUID, '2026-03-02')).not.toBe(KEY);
  });
});

describe('write and read back', () => {
  it('returns the stored body and its envelope', async () => {
    await offline().write(KEY, BODY, GENERATED, UNSAFE_AFTER);

    const record = await offline().read(KEY);
    expect(record.body).toBe(BODY);
    expect(record.generated_at).toBe(GENERATED);
    expect(record.unsafe_after_seconds).toBe(UNSAFE_AFTER);
    expect(record.cached_at).toBeTruthy();
  });

  it('returns null when this route and day have never been cached', async () => {
    expect(await offline().read(KEY)).toBeNull();
  });

  it('keeps one row per day rather than overwriting the route', async () => {
    const other = offline().keyFor(UUID, '2026-03-02');
    await offline().write(KEY, BODY, GENERATED, UNSAFE_AFTER);
    await offline().write(other, 'second day', GENERATED, UNSAFE_AFTER);

    expect((await offline().read(KEY)).body).toBe(BODY);
    expect((await offline().read(other)).body).toBe('second day');

    await window.pwaDb.delete(STORE, other);
  });

  it('stores a missing horizon as null rather than NaN', async () => {
    // The server omits X-Data-Unsafe-After when the answer carries no
    // bulletin, and ``Number(null)`` is 0 — which would expire the row
    // instantly if it were stored.
    await offline().write(KEY, BODY, GENERATED, null);

    expect((await offline().read(KEY)).unsafe_after_seconds).toBeNull();
  });

  it('returns null for a row cached under another account', async () => {
    // The anonymous session this harness runs as writes principal: null, so
    // a row stamped with an account is one this session must not read.
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: BODY,
      generated_at: GENERATED,
      unsafe_after_seconds: UNSAFE_AFTER,
      cached_at: new Date().toISOString(),
      principal: 'user-42',
    });

    expect(await offline().read(KEY)).toBeNull();
  });

  it('returns null for a row carrying no principal at all', async () => {
    // A row written before this partitioning existed belongs to nobody, so
    // it matches nobody — including the anonymous session this harness runs
    // as, whose own principal is null. The stored side is compared
    // untouched for exactly this reason (SNOW-493).
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: BODY,
      cached_at: new Date().toISOString(),
    });

    expect(await offline().read(KEY)).toBeNull();
  });
});

describe('the expiry horizon', () => {
  it('is not reached by a reading issued this morning', async () => {
    const record = {
      generated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
      unsafe_after_seconds: UNSAFE_AFTER,
    };

    expect(offline().isExpired(record)).toBe(false);
  });

  it('is reached by one issued three days ago', () => {
    // The case nothing in this project tested before SNOW-973. A bulletin
    // reading this old must not be repainted as a current one: the
    // forecast has turned over four times since it was taken.
    const record = {
      generated_at: new Date(Date.now() - 72 * 60 * 60 * 1000).toISOString(),
      unsafe_after_seconds: UNSAFE_AFTER,
    };

    expect(offline().isExpired(record)).toBe(true);
  });

  it('is reached exactly ON the horizon, not a second after', () => {
    const record = {
      generated_at: new Date(Date.now() - UNSAFE_AFTER * 1000).toISOString(),
      unsafe_after_seconds: UNSAFE_AFTER,
    };

    expect(offline().isExpired(record)).toBe(true);
  });

  it('never expires a row with no horizon', () => {
    // No bulletin was read, so there is nothing safety-critical in the
    // body — "this route crosses no forecast region" does not go stale.
    const record = {
      generated_at: new Date(2020, 0, 1).toISOString(),
      unsafe_after_seconds: null,
    };

    expect(offline().isExpired(record)).toBe(false);
  });

  it('never expires a row whose timestamp will not parse', () => {
    // Fail open rather than blanking a reading over a malformed header:
    // the "as of" line still says where the body came from.
    expect(
      offline().isExpired({
        generated_at: 'not-a-date',
        unsafe_after_seconds: UNSAFE_AFTER,
      }),
    ).toBe(false);
  });
});

describe('the public surface', () => {
  it('is frozen, like every other window.pwa* bridge', () => {
    expect(Object.isFrozen(offline())).toBe(true);
  });
});
