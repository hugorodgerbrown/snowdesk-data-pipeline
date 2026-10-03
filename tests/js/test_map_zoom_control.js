/*
 * tests/js/test_map_zoom_control.js — the map's zoom control
 * (static/js/map_zoom_control.js, SNOW-1077).
 *
 * The roundel opens and closes its flyout, − and + call MapLibre's
 * zoomOut / zoomIn and disable at the map's bounds, the face and the
 * roundel's accessible name follow the zoom, and a tick appears when the
 * terrain filter is switched on.
 *
 * The module is loaded ONCE, at file scope: its IIFE binds document-level
 * listeners, and jsdom's document outlives a test, so a per-test boot would
 * leave every earlier boot's listeners attached (test_map_geolocate.js's
 * reasoning). The markup is public/partials/_map_zoom_control.html cut to
 * what the module binds.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import '../../static/js/zoom_control_core.js';
import { loadMapBundle } from './_load_map_bundle.js';

document.body.innerHTML = `
  <div id="map"></div>
  <div id="map-legend" class="map-legend">
    <button id="map-zoom-toggle" type="button" aria-label="Zoom level"
            aria-haspopup="true" aria-expanded="false" aria-controls="map-zoom-flyout">
      <span data-zoom-face aria-hidden="true"></span>
    </button>
    <div id="map-zoom-flyout" role="group" hidden>
      <button type="button" data-zoom-step="out">-</button>
      <div aria-hidden="true">
        <ol data-zoom-ticks></ol>
        <span data-zoom-marker></span>
      </div>
      <button type="button" data-zoom-step="in">+</button>
      <ul data-zoom-gates hidden></ul>
    </div>
    <template id="map-zoom-tick-template">
      <li class="map-zoom-tick" data-zoom-tick=""><span data-zoom-tick-zoom></span></li>
    </template>
    <template id="map-zoom-gate-template">
      <li class="map-zoom-gate" data-zoom-gate="">
        <span data-zoom-gate-label></span><span data-zoom-gate-zoom></span>
      </li>
    </template>
    <template id="map-zoom-strings-template">
      <span data-string="toggle-label">Zoom level %(zoom)s</span>
      <span data-string="gate-zoom">z%(zoom)s</span>
      <span data-string="gate-terrain_filter">Terrain filter</span>
      <span data-string="gate-route_slopes">Route slope colours</span>
    </template>
  </div>
  <div id="elsewhere"></div>
`;

/** The camera zoom the stub reports; set per test. */
let zoom = 10.4;
/** The terrain filter's switch, as window.pwaZoomGates reports it. */
let terrainOn = false;

const handlers = {};
const map = {
  on: (ev, fn) => { (handlers[ev] ||= []).push(fn); },
  getZoom: () => zoom,
  getMinZoom: () => 4,
  getMaxZoom: () => 18,
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
};
window.MAP = map;
window.pwaZoomGates = {
  list: () => [
    { key: 'terrain_filter', zoom: 12, active: terrainOn },
    { key: 'route_slopes', zoom: 14, active: false },
  ],
};

loadMapBundle(['map_zoom_control.js']);

const toggle = document.getElementById('map-zoom-toggle');
const flyout = document.getElementById('map-zoom-flyout');
const zoomOutBtn = flyout.querySelector('[data-zoom-step="out"]');
const zoomInBtn = flyout.querySelector('[data-zoom-step="in"]');

/** Fire MapLibre's `zoom` event at the stub's current zoom. */
function fireZoom() {
  for (const fn of handlers.zoom || []) fn();
}

/** @returns {string} The roundel's face. */
const faceText = () => toggle.querySelector('[data-zoom-face]').textContent;

beforeEach(() => {
  zoom = 10.4;
  terrainOn = false;
  map.zoomIn.mockClear();
  map.zoomOut.mockClear();
  if (!flyout.hidden) toggle.click();
  fireZoom();
});

describe('the roundel', () => {
  it('shows the whole zoom on its face and in its accessible name', () => {
    expect(faceText()).toBe('10');
    expect(toggle.getAttribute('aria-label')).toBe('Zoom level 10');
  });

  it('follows the map on zoom', () => {
    zoom = 13.7;
    fireZoom();
    expect(faceText()).toBe('13');
    expect(toggle.getAttribute('aria-label')).toBe('Zoom level 13');
  });
});

describe('the flyout', () => {
  it('opens and closes from the roundel, keeping aria-expanded in step', () => {
    toggle.click();
    expect(flyout.hidden).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    toggle.click();
    expect(flyout.hidden).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on a click outside it, and on Escape', () => {
    toggle.click();
    document.getElementById('elsewhere').click();
    expect(flyout.hidden).toBe(true);

    toggle.click();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(flyout.hidden).toBe(true);
  });

  it('registers with the map-overlay registry, which can close it', () => {
    toggle.click();
    window.pwaMapOverlays.opening('some-other-surface');
    expect(flyout.hidden).toBe(true);
  });

  it('calls zoomOut and zoomIn from − and +, and stays open', () => {
    toggle.click();
    zoomOutBtn.click();
    zoomInBtn.click();
    expect(map.zoomOut).toHaveBeenCalledTimes(1);
    expect(map.zoomIn).toHaveBeenCalledTimes(1);
    expect(flyout.hidden).toBe(false);
  });

  it('disables − at the minimum zoom and + at the maximum', () => {
    toggle.click();
    zoom = 4;
    fireZoom();
    expect(zoomOutBtn.disabled).toBe(true);
    expect(zoomInBtn.disabled).toBe(false);
    zoom = 18;
    fireZoom();
    expect(zoomOutBtn.disabled).toBe(false);
    expect(zoomInBtn.disabled).toBe(true);
  });

  it('marks the camera on the scale', () => {
    toggle.click();
    zoom = 11;
    fireZoom();
    expect(flyout.querySelector('[data-zoom-marker]').style.left).toBe('50%');
  });
});

describe('the ticks', () => {
  const ticks = () => [...flyout.querySelectorAll('[data-zoom-ticks] > li')];
  const gates = () => [...flyout.querySelectorAll('[data-zoom-gates] > li')];

  it('draw nothing while no gated layer is on', () => {
    toggle.click();
    expect(ticks()).toEqual([]);
    expect(flyout.querySelector('[data-zoom-gates]').hidden).toBe(true);
  });

  it('appear when the terrain filter is switched on, labelled with the layer', () => {
    toggle.click();
    terrainOn = true;
    document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));

    expect(ticks().map((li) => li.dataset.zoomTick)).toEqual(['terrain_filter']);
    expect(ticks()[0].textContent.trim()).toBe('12');
    expect(gates()[0].querySelector('[data-zoom-gate-label]').textContent).toBe('Terrain filter');
    expect(gates()[0].querySelector('[data-zoom-gate-zoom]').textContent).toBe('z12');
    expect(flyout.querySelector('[data-zoom-gates]').hidden).toBe(false);
  });

  it('mark a gate reached once the camera passes it', () => {
    terrainOn = true;
    toggle.click();
    expect(ticks()[0].hasAttribute('data-reached')).toBe(false);
    zoom = 12.2;
    fireZoom();
    expect(ticks()[0].hasAttribute('data-reached')).toBe(true);
    expect(gates()[0].hasAttribute('data-reached')).toBe(true);
  });

  it('go when the layer is switched off again', () => {
    terrainOn = true;
    toggle.click();
    expect(ticks()).toHaveLength(1);
    terrainOn = false;
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    expect(ticks()).toEqual([]);
  });
});
