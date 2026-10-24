/*
 * tests/js/test_sheet_leader.js — the map sheets' leader line
 * (static/js/sheet_leader.js, Hugo, 24 Oct 2026).
 *
 * The geometry first — sideways to the docked panel's right edge on a
 * desktop, up from the bottom sheet on a phone, and no line when the symbol
 * is under or behind the sheet — then the DOM half: hidden while the sheet
 * is closed, drawn once it is open on a point.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/route_leader_core.js';

document.body.innerHTML = '<div id="map"></div>'
  + '<div id="weather-sheet" hidden></div><div id="place-sheet" hidden></div>';

const sheetEl = document.getElementById('weather-sheet');
const placeEl = document.getElementById('place-sheet');
/** What each sheet stub answers for its point; a test may change them. */
const state = { lngLat: null, place: null };

window.pwaWeatherDetail = { point: () => state.lngLat };
window.pwaPlaceSheet = { point: () => state.place };
window.pwaMapProjection = {
  project: () => ({ x: 600, y: 400 }),
  onChange: vi.fn(() => () => {}),
};
window.matchMedia = () => ({ matches: true }); // desktop
sheetEl.getBoundingClientRect = () => ({ left: 16, right: 336, top: 100, bottom: 700 });
placeEl.getBoundingClientRect = () => ({ left: 16, right: 336, top: 100, bottom: 700 });

await import('../../static/js/sheet_leader.js');

const { stops, redraw, element } = window.pwaSheetLeader;
const SHEET = { left: 16, right: 336, top: 100, bottom: 700 };

describe('stops', () => {
  it('runs sideways to the panel edge, level with the heading', () => {
    const line = stops({ x: 600, y: 400 }, SHEET, { top: 120, bottom: 140 }, false);

    expect(line).toEqual({
      from: { x: 600, y: 400 },
      to: { x: 336, y: 130 },
      axis: 'x',
      side: 'right',
    });
  });

  it('holds the notch inside the panel when the heading has scrolled away', () => {
    const line = stops({ x: 600, y: 400 }, SHEET, { top: -200, bottom: -180 }, false);

    expect(line.to).toEqual({ x: 336, y: 116 });
  });

  it('draws no line to a symbol the panel covers', () => {
    expect(stops({ x: 200, y: 400 }, SHEET, null, false)).toBeNull();
  });

  it('runs up from the bottom sheet, under the symbol', () => {
    const sheet = { left: 0, right: 390, top: 600, bottom: 844 };
    const line = stops({ x: 200, y: 300 }, sheet, null, true);

    expect(line).toEqual({
      from: { x: 200, y: 300 },
      to: { x: 200, y: 600 },
      axis: 'y',
      side: 'top',
    });
  });

  it('draws no line to a symbol under the bottom sheet', () => {
    const sheet = { left: 0, right: 390, top: 600, bottom: 844 };

    expect(stops({ x: 200, y: 700 }, sheet, null, true)).toBeNull();
  });

  it('draws nothing without a point', () => {
    expect(stops(null, SHEET, null, false)).toBeNull();
  });
});

describe('the drawn line', () => {
  it('is hidden while the sheet is closed', () => {
    state.lngLat = [7.93, 46.11];
    redraw();

    expect(element.style.display).toBe('none');
  });

  it('is drawn once the sheet is open on a point', () => {
    sheetEl.removeAttribute('hidden');
    state.lngLat = [7.93, 46.11];
    redraw();

    expect(element.style.display).toBe('');
    expect(element.querySelector('path').getAttribute('d')).toMatch(/^M600 400 C/);
  });

  it('is hidden again when the sheet has no point', () => {
    state.lngLat = null;
    redraw();

    expect(element.style.display).toBe('none');
  });

  it('points from the place sheet too', () => {
    sheetEl.setAttribute('hidden', '');
    placeEl.removeAttribute('hidden');
    state.place = [7.22, 46.1];
    redraw();

    expect(element.style.display).toBe('');
    expect(element.querySelector('path').getAttribute('d')).toMatch(/^M600 400 C/);
  });
});
