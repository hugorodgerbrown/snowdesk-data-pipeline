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
            aria-haspopup="dialog" aria-expanded="false" aria-controls="map-zoom-flyout">
      <span data-zoom-face aria-hidden="true"></span>
    </button>
    <div id="map-zoom-flyout" role="dialog" aria-label="Zoom" hidden>
      <button type="button" data-zoom-step="out">-</button>
      <div>
        <ol data-zoom-levels></ol>
        <ol data-zoom-ticks></ol>
        <span data-zoom-marker></span>
      </div>
      <button type="button" data-zoom-step="in">+</button>
    </div>
    <template id="map-zoom-tick-template">
      <li class="map-zoom-tick" data-zoom-tick="">
        <button type="button" data-zoom-tick-button data-zoom-target="">
          <span data-zoom-tick-label></span><span data-zoom-tick-threshold></span>
        </button>
      </li>
    </template>
    <template id="map-zoom-strings-template">
      <span data-string="toggle-label">Zoom level %(zoom)s</span>
      <span data-string="threshold-from">≥%(zoom)s</span>
      <span data-string="threshold-up-to">≤%(zoom)s</span>
      <span data-string="gate-region_select">Region selector</span>
      <span data-string="gate-terrain_filter">Terrain overlay</span>
      <span data-string="gate-route_slopes">Route slope colours</span>
    </template>
  </div>
  <div id="elsewhere"></div>
`;

/** The camera zoom the stub reports; set per test. */
let zoom = 10.4;

const handlers = {};
const map = {
  on: (ev, fn) => { (handlers[ev] ||= []).push(fn); },
  getZoom: () => zoom,
  getMinZoom: () => 4,
  getMaxZoom: () => 18,
  zoomTo: vi.fn(),
};
window.MAP = map;
window.pwaZoomGates = {
  list: () => [
    { key: 'region_select', zoom: 10, upTo: true },
    { key: 'terrain_filter', zoom: 12 },
    { key: 'route_slopes', zoom: 14 },
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

/**
 * Fire MapLibre's `moveend`.
 *
 * @param {object} [event] The event data; `originalEvent` marks a gesture.
 */
function fireMoveEnd(event = {}) {
  for (const fn of handlers.moveend || []) fn(event);
}

/** @returns {Array<number>} Every zoomTo target, in order. */
const targets = () => map.zoomTo.mock.calls.map((call) => call[0]);

/** @returns {string} The roundel's face. */
const faceText = () => toggle.querySelector('[data-zoom-face]').textContent;

beforeEach(() => {
  zoom = 10.4;
  // A gesture ends any step a previous test left pending.
  fireMoveEnd({ originalEvent: new Event('touchend') });
  map.zoomTo.mockClear();
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

describe('the roundel\'s writes', () => {
  it('skips the face and name when a zoom frame leaves the whole zoom unchanged', () => {
    const spy = vi.spyOn(toggle, 'setAttribute');
    zoom = 10.6;
    fireZoom();
    zoom = 10.8;
    fireZoom();
    expect(spy.mock.calls.filter((c) => c[0] === 'aria-label')).toEqual([]);
    zoom = 11.1;
    fireZoom();
    expect(spy.mock.calls.filter((c) => c[0] === 'aria-label')).toHaveLength(1);
    spy.mockRestore();
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

  it('zooms one level out and in from − and +, and stays open', () => {
    toggle.click();
    zoomOutBtn.click();
    expect(targets()).toEqual([9.4]);
    zoom = 9.4;
    fireMoveEnd();
    zoomInBtn.click();
    expect(targets()).toEqual([9.4, 10.4]);
    expect(flyout.hidden).toBe(false);
  });

  it('moves two levels for two quick taps, stepping from the pending target', () => {
    toggle.click();
    zoom = 10;
    zoomOutBtn.click();
    // The first step's animation is part-way when the second tap lands.
    zoom = 9.6;
    fireZoom();
    zoomOutBtn.click();
    expect(targets()).toEqual([9, 8]);

    // The second zoomTo stops the first animation short of its target;
    // that moveend must not forget where the map is going.
    fireMoveEnd();
    zoomOutBtn.click();
    expect(targets()).toEqual([9, 8, 7]);

    zoom = 7;
    fireMoveEnd();
    zoomInBtn.click();
    expect(targets().at(-1)).toBe(8);
  });

  it('clamps a step to the range, and disables + once it targets the maximum', () => {
    toggle.click();
    zoom = 17.2;
    zoomInBtn.click();
    expect(targets()).toEqual([18]);
    // Disabled on the tap, not when the animation lands, so a second
    // quick tap has nothing further to ask for.
    expect(zoomInBtn.disabled).toBe(true);
    zoomInBtn.click();
    expect(targets()).toEqual([18]);
  });

  it('moves focus to the other step when the focused one is disabled', () => {
    toggle.click();
    zoom = 17.5;
    fireZoom();
    zoomInBtn.focus();
    expect(document.activeElement).toBe(zoomInBtn);
    zoomInBtn.click();
    expect(zoomInBtn.disabled).toBe(true);
    expect(document.activeElement).toBe(zoomOutBtn);
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
    // The scale runs bottom to top: z11 is halfway up z4..z18.
    expect(flyout.querySelector('[data-zoom-marker]').style.top).toBe('50%');
  });
});

describe('the ticks', () => {
  const ticks = () => [...flyout.querySelectorAll('[data-zoom-ticks] > li')];
  const tick = (key) => ticks().find((li) => li.dataset.zoomTick === key);

  it('name every gate at its level', () => {
    toggle.click();
    expect(ticks().map((li) => li.dataset.zoomTick))
      .toEqual(['region_select', 'terrain_filter', 'route_slopes']);
    expect(tick('terrain_filter').querySelector('[data-zoom-tick-label]').textContent)
      .toBe('Terrain overlay');
    // z12 sits 8/14 of the way up z4..z18.
    expect(tick('terrain_filter').style.top).toBe('42.86%');
  });

  it('number every whole level on the line, z18 at the top', () => {
    toggle.click();
    const levels = [...flyout.querySelectorAll('[data-zoom-levels] > li')];
    expect(levels.map((li) => li.textContent)).toEqual(
      ['4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18'],
    );
    expect(levels.at(-1).style.top).toBe('0%');
  });

  it('mark a gate reached once the camera passes it, switched on or not', () => {
    toggle.click();
    expect(tick('terrain_filter').hasAttribute('data-reached')).toBe(false);
    zoom = 12.2;
    fireZoom();
    expect(tick('terrain_filter').hasAttribute('data-reached')).toBe(true);
  });

  it('give each threshold its direction: ≤10 for regions, ≥ for the rest', () => {
    toggle.click();
    const thresholds = ticks().map((li) => li.querySelector('[data-zoom-tick-threshold]').textContent);
    expect(thresholds).toEqual(['≤10', '≥12', '≥14']);
  });

  it('mark the region selector available only at z10 or below', () => {
    toggle.click();
    expect(tick('region_select').hasAttribute('data-reached')).toBe(true);
    zoom = 11.2;
    fireZoom();
    expect(tick('region_select').hasAttribute('data-reached')).toBe(false);
  });

  it('zoom the map to a feature\'s threshold when its row is tapped, and stay open', () => {
    toggle.click();
    tick('route_slopes').querySelector('button').click();
    expect(targets()).toEqual([14]);
    tick('region_select').querySelector('button').click();
    expect(targets()).toEqual([14, 10]);
    expect(flyout.hidden).toBe(false);
  });

  it('restyle a row in place as the zoom crosses its threshold, keeping focus', () => {
    toggle.click();
    const button = tick('terrain_filter').querySelector('button');
    button.focus();
    zoom = 12.1;
    fireZoom();
    expect(tick('terrain_filter').hasAttribute('data-reached')).toBe(true);
    expect(document.activeElement).toBe(button);
  });
});
