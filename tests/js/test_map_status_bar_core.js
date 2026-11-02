/*
 * tests/js/test_map_status_bar_core.js — what the map status bar says (SNOW-1392).
 *
 * statusState decides, from plain inputs, which painted layers the bar
 * names and when it points at zooming in: the region fill below z10 and
 * its edge band from it, the bulletin's slopes or the Steep terrain filter
 * from z12, Switzerland only, and the basemap last.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_status_bar_core.js';

const core = self.pwaStatusBarCore;

const base = {
  zoom: 12,
  bulletinOn: true,
  level: 'moderate',
  country: 'CH',
  slopesEligible: true,
  slopesOn: true,
  filterOn: false,
  basemap: 'Swisstopo winter',
};
const kinds = (input) => core.statusState({ ...base, ...input }).segments.map((s) => s.kind);
const hint = (input) => core.statusState({ ...base, ...input }).hint;

describe('statusState', () => {
  it('names the fill below z10 and the edge band from it', () => {
    expect(kinds({ zoom: 9 })[0]).toBe('region-fill');
    expect(kinds({ zoom: 10 })[0]).toBe('region-edge');
    expect(core.EDGE_ZOOM).toBe(10);
  });

  it('adds the bulletin slopes from z12, and the zoom hint below it', () => {
    expect(kinds({ zoom: 12 })).toEqual(['region-edge', 'slopes', 'basemap']);
    expect(hint({ zoom: 12 })).toBe('');
    expect(kinds({ zoom: 11 })).toEqual(['region-edge', 'basemap']);
    expect(hint({ zoom: 11 })).toBe('zoom-slopes');
    expect(core.SLOPES_ZOOM).toBe(12);
  });

  it('names the slopes as off from z12 when switched off, and gives no hint', () => {
    expect(kinds({ slopesOn: false })).toEqual(['region-edge', 'slopes-off', 'basemap']);
    expect(core.statusState({ ...base, slopesOn: false }).segments[1].muted).toBe(true);
    expect(hint({ slopesOn: false, zoom: 11 })).toBe('');
  });

  it('lets Steep terrain replace the slopes', () => {
    expect(kinds({ filterOn: true, slopesOn: false })).toEqual(['region-edge', 'filter', 'basemap']);
    expect(hint({ filterOn: true, slopesOn: false, zoom: 11 })).toBe('zoom-filter');
  });

  it('says Switzerland only outside the tiles, and never promises a zoom there', () => {
    expect(kinds({ country: 'FR' })).toEqual(['region-edge', 'slopes-ch-only', 'basemap']);
    expect(hint({ country: 'FR', zoom: 11 })).toBe('');
    expect(hint({ country: 'FR', zoom: 11, filterOn: true, slopesOn: false })).toBe('');
  });

  it('treats no region at the centre as maybe-Switzerland', () => {
    expect(hint({ country: null, zoom: 9 })).toBe('zoom-slopes');
  });

  it('drops the region with the bulletin layer off, and the slopes on a page without them', () => {
    expect(kinds({ bulletinOn: false })).toEqual(['slopes', 'basemap']);
    expect(kinds({ slopesEligible: false, slopesOn: false })).toEqual(['region-edge', 'basemap']);
  });

  it('colours each swatch by the centre region, and no_rating without one', () => {
    expect(core.statusState(base).segments[0].level).toBe('moderate');
    expect(core.statusState({ ...base, level: null }).segments[0].level).toBe('no_rating');
  });

  it('carries the basemap label, and leaves it out without one', () => {
    expect(core.statusState(base).segments.at(-1).text).toBe('Swisstopo winter');
    expect(kinds({ basemap: '' })).toEqual(['region-edge', 'slopes']);
  });
});
