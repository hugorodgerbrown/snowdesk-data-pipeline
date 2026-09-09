/*
 * tests/js/test_observations_offline_principal.js — the offline row store
 * read back by a SIGNED-IN reader (SNOW-661,
 * static/js/observations_offline.js).
 *
 * Its own file for the reason test_mutation_queue_principal.js is: db.js's
 * context() memoises <meta name="pwa-user-id"> once for the life of the
 * module, so a non-anonymous principal needs the tag in place before the
 * one-time dynamic import. test_observations_offline.js therefore runs as
 * an anonymous session and can only assert this partitioning from that
 * side.
 *
 * Two rules, both SNOW-493's. A reader sees the rows they cached
 * themselves, and nothing else: not a row belonging to another account, and
 * not a row belonging to NOBODY — one carrying no principal key at all,
 * written before the partitioning existed. The absent key is compared
 * untouched rather than normalised to null, which is what stops such a row
 * matching an anonymous reader (asserted in test_observations_offline.js,
 * from the session that has one).
 */

import { beforeEach, describe, expect, it } from 'vitest';

const STORE = 'data:panel_rows';
const KEY = 'observations';
const ROWS = '<ul><li id="observation-a1b2">Whumpfing</li></ul>';
const USER_ID = 'user-7';

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', USER_ID);
document.head.appendChild(meta);

await import('../../static/js/db.js');
await import('../../static/js/observations_offline.js');

beforeEach(async () => {
  await window.pwaDb.delete(STORE, KEY);
});

describe('a signed-in reader', () => {
  it('reads back the rows it cached itself', async () => {
    await window.pwaObservationsOffline.write(ROWS);

    const record = await window.pwaObservationsOffline.read();
    expect(record.body).toBe(ROWS);
    expect(record.principal).toBe(USER_ID);
  });

  it("returns null for another account's row", async () => {
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: ROWS,
      cached_at: new Date().toISOString(),
      principal: 'user-42',
    });

    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });

  it('returns null for a row cached by an anonymous session', async () => {
    // The account-switch case in the direction the anonymous harness cannot
    // reach: signing in must not inherit what the signed-out session saw.
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: ROWS,
      cached_at: new Date().toISOString(),
      principal: null,
    });

    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });

  it('returns null for a row carrying no principal at all', async () => {
    await window.pwaDb.put(STORE, {
      key: KEY,
      body: ROWS,
      cached_at: new Date().toISOString(),
    });

    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });
});
