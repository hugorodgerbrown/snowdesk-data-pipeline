/*
 * tests/js/test_map_slopes_notice_core.js — when the map says the
 * bulletin's slopes have taken over (SNOW-1378).
 *
 * The card shows once per device, the first time the map settles at z12 or
 * closer with the bulletin's slopes drawn over a country the tiles cover
 * and nothing else on screen. Each condition is a way it could appear over
 * a map that is not showing what it says, or interrupt the reader, so
 * each is pinned.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/map_slopes_notice_core.js';

const core = self.pwaSlopesNoticeCore;

const base = {
  zoom: 12.4,
  slopesOn: true,
  filterOn: false,
  country: 'CH',
  sheetOpen: false,
  overlayOpen: false,
  tourRunning: false,
};

/** A storage double over a plain map. */
function memoryStorage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => { items.set(key, String(value)); },
    items,
  };
}

/** A storage whose every call throws, as a locked-down private mode does. */
const throwingStorage = {
  getItem: () => { throw new Error('denied'); },
  setItem: () => { throw new Error('denied'); },
};

describe('claim', () => {
  let storage;

  beforeEach(() => {
    storage = memoryStorage();
  });

  it('shows once per device', () => {
    expect(core.claim(base, storage)).toBe(true);
    expect(core.claim(base, storage)).toBe(false);
    // A second page load on the same device reads the same record.
    expect(core.hasBeenSeen(storage)).toBe(true);
  });

  it('writes the seen flag on show, under its key', () => {
    expect(storage.items.has(core.SEEN_KEY)).toBe(false);
    core.claim(base, storage);
    expect(core.SEEN_KEY).toBe('snowdesk.map.slopes_notice_seen');
    expect(storage.items.has(core.SEEN_KEY)).toBe(true);
  });

  it('does not show below z12', () => {
    expect(core.claim({ ...base, zoom: 11.99 }, storage)).toBe(false);
    expect(core.claim({ ...base, zoom: 12 }, storage)).toBe(true);
  });

  it('does not show with the layer off or Steep terrain on', () => {
    expect(core.claim({ ...base, slopesOn: false }, storage)).toBe(false);
    expect(core.claim({ ...base, filterOn: true }, storage)).toBe(false);
  });

  it('does not show outside the terrain countries', () => {
    expect(core.claim({ ...base, country: 'AT' }, storage)).toBe(false);
    expect(core.claim({ ...base, country: null }, storage)).toBe(false);
  });

  it('does not show with a sheet, another overlay or a tour up', () => {
    expect(core.claim({ ...base, sheetOpen: true }, storage)).toBe(false);
    expect(core.claim({ ...base, overlayOpen: true }, storage)).toBe(false);
    expect(core.claim({ ...base, tourRunning: true }, storage)).toBe(false);
  });

  it('records nothing when it does not show, so a later move can', () => {
    core.claim({ ...base, zoom: 10 }, storage);
    expect(storage.items.has(core.SEEN_KEY)).toBe(false);
    expect(core.claim(base, storage)).toBe(true);
  });

  it('does not show if storage throws, or there is none', () => {
    expect(core.claim(base, throwingStorage)).toBe(false);
    expect(core.claim(base, null)).toBe(false);
    expect(core.hasBeenSeen(throwingStorage)).toBe(true);
  });

  it('does not show if only the write throws', () => {
    const readOnly = { getItem: () => null, setItem: () => { throw new Error('quota'); } };
    expect(core.claim(base, readOnly)).toBe(false);
  });
});

describe('unmarkSeen', () => {
  it('takes back the record, and survives storage that throws', () => {
    const store = new Map();
    const storage = {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, v); },
      removeItem: (k) => { store.delete(k); },
    };
    storage.setItem(core.SEEN_KEY, 'seen');
    core.unmarkSeen(storage);
    expect(core.hasBeenSeen(storage)).toBe(false);
    const broken = { getItem: () => null, setItem: () => {}, removeItem: () => { throw new Error('no'); } };
    expect(() => core.unmarkSeen(broken)).not.toThrow();
    expect(() => core.unmarkSeen(null)).not.toThrow();
  });
});
