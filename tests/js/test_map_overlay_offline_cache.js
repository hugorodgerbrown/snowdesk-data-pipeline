/*
 * tests/js/test_map_overlay_offline_cache.js — the overlay cache keeps
 * search's summit list for every reader (SNOW-1320).
 *
 * `ski_summits` is public data — SAC's summits and huts, the same for
 * everyone — so, like `weather` and `community_reports`, it is not in
 * PRINCIPAL_SCOPED: a list stored while signed in must read back after a
 * sign-out, and an anonymous one after a sign-in. `favourites` is the
 * contrast that proves the principal check is live in this harness.
 *
 * The real module against an in-memory `window.pwaDb` whose
 * `context().user_id` the test switches. The module defines its global
 * once and non-configurably, so it is imported once for the file.
 */

import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

const SUMMITS = { summits: [{ key: 'rosablanche', name: 'Rosablanche', lon: 7.33, lat: 46.06 }] };
const FAVOURITES = { type: 'FeatureCollection', features: [] };

let userId = '';
const rows = new Map();

beforeAll(async () => {
  window.pwaDb = {
    isResetRequired: () => false,
    context: () => ({ user_id: userId }),
    get: async (_store, key) => rows.get(key),
    put: async (_store, row) => {
      rows.set(row.key, row);
    },
  };
  await import('../../static/js/map_overlay_offline_cache.js');
});

beforeEach(() => {
  rows.clear();
  userId = '';
});

describe('the stored summit list', () => {
  it('is stored with no principal stamp', async () => {
    userId = 'u-1';
    await window.pwaMapOverlayCache.putOverlay('ski_summits', SUMMITS);

    expect(rows.get('ski_summits').principal).toBeUndefined();
  });

  it('reads back for an anonymous reader after a signed-in write', async () => {
    userId = 'u-1';
    await window.pwaMapOverlayCache.putOverlay('ski_summits', SUMMITS);
    userId = '';

    expect(await window.pwaMapOverlayCache.getOverlay('ski_summits')).toEqual(SUMMITS);
  });

  it('reads back for a signed-in reader after an anonymous write', async () => {
    await window.pwaMapOverlayCache.putOverlay('ski_summits', SUMMITS);
    userId = 'u-2';

    expect(await window.pwaMapOverlayCache.getOverlay('ski_summits')).toEqual(SUMMITS);
  });

  it('unlike favourites, which another account never reads', async () => {
    userId = 'u-1';
    await window.pwaMapOverlayCache.putOverlay('favourites', FAVOURITES);
    userId = 'u-2';

    expect(await window.pwaMapOverlayCache.getOverlay('favourites')).toBeNull();
  });
});
