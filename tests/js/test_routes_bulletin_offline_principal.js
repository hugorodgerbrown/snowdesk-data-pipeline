/*
 * tests/js/test_routes_bulletin_offline_principal.js — a cached bulletin
 * reading read back by a SIGNED-IN reader (SNOW-973,
 * static/js/routes_bulletin_offline.js).
 *
 * Its own file for the reason test_observations_offline_principal.js is:
 * db.js's ``context()`` memoises <meta name="pwa-user-id"> once for the
 * life of the module, so a non-anonymous principal needs the tag in place
 * before the one-time dynamic import. test_routes_bulletin_offline.js
 * therefore runs as an anonymous session and can only assert this
 * partitioning from that side.
 *
 * Two rules, both SNOW-493's, and they matter more here than for a list of
 * route names: this row is a reading of avalanche terrain along somebody's
 * own line. A reader sees the rows they cached themselves, and nothing
 * else — not another account's, and not NOBODY's (a row carrying no
 * principal key at all, written before the partitioning existed).
 */

import { beforeEach, describe, expect, it } from 'vitest';

const STORE = 'data:route_bulletins';
const KEY = '11111111-2222-3333-4444-555555555555:2026-03-01';
const BODY = '<div data-testid="route-bulletin">Aletsch · 1.4 km on N</div>';
const USER_ID = 'user-7';

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', USER_ID);
document.head.appendChild(meta);

await import('../../static/js/db.js');
await import('../../static/js/routes_bulletin_offline.js');

beforeEach(async () => {
  await window.pwaDb.delete(STORE, KEY);
});

describe('a signed-in reader', () => {
  it('reads back the reading it cached itself', async () => {
    await window.pwaRoutesBulletinOffline.write(KEY, BODY, null, null);

    const record = await window.pwaRoutesBulletinOffline.read(KEY);
    expect(record.body).toBe(BODY);
    expect(record.principal).toBe(USER_ID);
  });

  it("returns null for another account's row", async () => {
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: BODY,
      cached_at: new Date().toISOString(),
      principal: 'user-42',
    });

    expect(await window.pwaRoutesBulletinOffline.read(KEY)).toBeNull();
  });

  it('returns null for a row cached by an anonymous session', async () => {
    // The account-switch case in the direction the anonymous harness cannot
    // reach: signing in must not inherit what the signed-out session saw.
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: BODY,
      cached_at: new Date().toISOString(),
      principal: null,
    });

    expect(await window.pwaRoutesBulletinOffline.read(KEY)).toBeNull();
  });

  it('returns null for a row carrying no principal at all', async () => {
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: BODY,
      cached_at: new Date().toISOString(),
    });

    expect(await window.pwaRoutesBulletinOffline.read(KEY)).toBeNull();
  });
});
