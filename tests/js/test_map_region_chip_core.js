/*
 * tests/js/test_map_region_chip_core.js — what the region chip says
 * (SNOW-1376).
 *
 * From z10 the chip names the region at the centre and its level; from z12
 * its second line says what the slopes show. Each branch is a way the
 * chip could tell a reader the wrong thing about the ground in front of
 * them, so each is pinned.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_region_chip_core.js';

const core = self.pwaRegionChipCore;

const base = {
  zoom: 12.5,
  regionId: 'CH-4115',
  name: 'Martigny-Verbier',
  country: 'CH',
  rating: 'considerable',
  slopesPainted: true,
  slopesOn: true,
  filterOn: false,
};

describe('chipState', () => {
  it('is hidden below z10, with no region, or with no rating', () => {
    expect(core.chipState({ ...base, zoom: 9.9 }).hidden).toBe(true);
    expect(core.chipState({ ...base, regionId: null }).hidden).toBe(true);
    expect(core.chipState({ ...base, rating: null }).hidden).toBe(true);
    expect(core.chipState({ ...base, rating: 'no_rating' }).hidden).toBe(true);
  });

  it('names the region and its level, the data-level matching .danger-tile', () => {
    const state = core.chipState(base);
    expect(state).toMatchObject({
      hidden: false, regionId: 'CH-4115', name: 'Martigny-Verbier', level: 'considerable', digit: '3',
    });
  });

  it('has no second line below z12', () => {
    expect(core.chipState({ ...base, zoom: 11.9 }).detail).toBe('');
  });

  it('says "slopes" where the region\'s slopes are painted', () => {
    expect(core.chipState(base).detail).toBe('slopes');
    expect(core.chipState({ ...base, slopesPainted: false }).detail).toBe('');
  });

  it('names the reader\'s own filter when Steep terrain is on', () => {
    expect(core.chipState({ ...base, filterOn: true }).detail).toBe('filter');
  });

  it('says slopes are Swiss-only outside Switzerland, from z12 and only with slopes on', () => {
    const fr = { ...base, country: 'FR', slopesPainted: false };
    expect(core.chipState(fr).detail).toBe('ch-only');
    expect(core.chipState({ ...fr, zoom: 11 }).detail).toBe('');
    expect(core.chipState({ ...fr, slopesOn: false }).detail).toBe('');
  });
});
