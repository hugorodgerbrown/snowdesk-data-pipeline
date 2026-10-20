/*
 * tests/js/test_map_help_mode.js — Vitest unit tests for
 * static/js/map_help_mode.js (SNOW-1119).
 *
 * Help mode is the "?" roundel's job now: it names every control on the
 * map, turns a tap on a control into a card saying what it does, and
 * numbers the parts of a sheet opened while it is on. These cases pin the
 * behaviour a reader depends on — that a control tapped in help mode does
 * NOT act, that "Open" leaves help mode and then does act, that a tap on
 * the map still goes through, and that a guide follows its sheet.
 *
 * Positions are re-read every animation frame; fake timers drive the
 * frames. jsdom has no layout, so the geometry is not asserted — what is
 * drawn, and for what, is.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function buildFixture({ sheetOpen = false } = {}) {
  document.body.innerHTML = `
    <div id="map">
      <button id="basemap-toggle" type="button">Layers</button>
      <button id="route-add-btn" type="button" hidden>Routes</button>
      <div id="map-canvas"></div>
    </div>
    <button id="map-help-toggle" type="button">?</button>
    <div id="region-sheet" data-detents="peek,half,full" data-detent="peek" ${sheetOpen ? '' : 'hidden'}>
      <div data-sheet-handle></div>
      <span data-region-sheet-slot="rating">2</span>
      <p data-testid="region-sheet-breadcrumb">Switzerland › Valais</p>
    </div>
    <div id="route-rail" hidden>
      <p data-route-rail-meta>12 km · 900 m</p>
    </div>
    <div id="map-help-mode" hidden data-help-url="/help/">
      <div id="map-help-mode-bar">
        <button id="map-help-mode-tour" type="button">Tour</button>
        <button id="map-help-mode-done" type="button">Done</button>
      </div>
      <div id="map-help-mode-labels"></div>
      <div id="map-help-mode-ring" hidden></div>
      <div id="map-help-mode-card" hidden tabindex="-1">
        <button id="map-help-mode-card-close" type="button">×</button>
        <h3 id="map-help-mode-card-title"></h3>
        <p id="map-help-mode-card-body"></p>
        <a id="map-help-mode-card-more" href="/help/">More in Help</a>
        <button id="map-help-mode-card-open" type="button"></button>
      </div>
      <div id="map-help-mode-badges"></div>
      <div id="map-help-mode-guide" hidden tabindex="-1">
        <button id="map-help-mode-guide-close" type="button">×</button>
        <p id="map-help-mode-guide-eyebrow"></p>
        <ol id="map-help-mode-guide-key"></ol>
        <a id="map-help-mode-guide-more" href="/help/"></a>
        <button id="map-help-mode-guide-done" type="button">Got it</button>
      </div>
    </div>
    <ul id="map-help-controls" hidden>
      <li data-control="#basemap-toggle" data-label="Layers" data-topic="help-topic-layers" data-open="Open layers">
        Pick the base map.
      </li>
      <li data-control="#route-add-btn" data-label="Routes" data-topic="help-topic-routes" data-open="Open routes">
        Upload a GPX track.
      </li>
    </ul>
    <div id="map-help-guides" hidden>
      <div data-sheet-guide="region" data-sheet="#region-sheet" data-eyebrow="Region sheet"
           data-topic="help-topic-bulletins" data-more="Reading a bulletin">
        <ol>
          <li data-anchor="[data-region-sheet-slot='rating']"><b>Danger level</b> for the region</li>
          <li data-anchor="[data-testid='region-sheet-breadcrumb']"><b>Where it sits</b></li>
          <li data-anchor="[data-testid='panel-title']"><b>Each problem</b></li>
        </ol>
      </div>
      <div data-sheet-guide="route" data-sheet="#route-rail" data-eyebrow="Route"
           data-topic="help-topic-routes" data-more="More about routes">
        <ol><li data-anchor="[data-route-rail-meta]"><b>Length and climb</b></li></ol>
      </div>
    </div>
  `;
}

async function loadModules() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  await import('../../static/js/map_help_mode.js');
}

const $ = (id) => document.getElementById(id);
const helpModeOn = () => document.documentElement.hasAttribute('data-map-help-mode');
const frame = () => vi.advanceTimersByTime(32);
const labels = () => Array.from($('map-help-mode-labels').children).filter((n) => !n.hidden);

let layersPressed;
// Each case re-imports the module against a fresh page; the listeners an
// earlier import put on `document` and `window` would otherwise answer first.
let added = [];
const realAdd = {
  document: document.addEventListener.bind(document),
  window: window.addEventListener.bind(window),
};

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  document.documentElement.removeAttribute('data-map-help-mode');
});

afterEach(() => {
  // Leave help mode so this case's frame loop and listeners go quiet.
  $('map-help-mode-done')?.click();
  added.forEach(([target, args]) => target.removeEventListener(...args));
  added = [];
  vi.useRealTimers();
});

async function setUp(opts) {
  buildFixture(opts);
  document.addEventListener = (...args) => {
    added.push([document, args]);
    realAdd.document(...args);
  };
  window.addEventListener = (...args) => {
    added.push([window, args]);
    realAdd.window(...args);
  };
  await loadModules();
  document.addEventListener = realAdd.document;
  window.addEventListener = realAdd.window;
  layersPressed = vi.fn();
  $('basemap-toggle').addEventListener('click', layersPressed);
}

describe('the "?" roundel', () => {
  it('turns help mode on, and not the tour', async () => {
    await setUp();
    const tour = vi.fn();
    document.addEventListener('snowdesk:map-help-requested', tour);

    $('map-help-toggle').click();

    expect(helpModeOn()).toBe(true);
    expect($('map-help-mode').hidden).toBe(false);
    expect($('map-help-toggle').getAttribute('aria-pressed')).toBe('true');
    expect(tour).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:map-help-requested', tour);
  });

  it('turns it off again', async () => {
    await setUp();
    $('map-help-toggle').click();

    $('map-help-toggle').click();

    expect(helpModeOn()).toBe(false);
    expect($('map-help-mode').hidden).toBe(true);
  });
});

describe('the controls', () => {
  it('names each control on screen, and none that is hidden', async () => {
    await setUp();
    $('map-help-toggle').click();
    frame();

    expect(labels().map((n) => n.textContent)).toEqual(['Layers']);
  });

  it('a tap shows what the control does instead of doing it', async () => {
    await setUp();
    $('map-help-toggle').click();

    $('basemap-toggle').click();

    expect(layersPressed).not.toHaveBeenCalled();
    expect($('map-help-mode-card').hidden).toBe(false);
    expect($('map-help-mode-card-title').textContent).toBe('Layers');
    expect($('map-help-mode-card-body').textContent).toBe('Pick the base map.');
    expect($('map-help-mode-card-more').getAttribute('href')).toBe('/help/#help-topic-layers');
    expect($('map-help-mode-card-open').textContent).toBe('Open layers');
  });

  it('a tap on a control\'s name does the same', async () => {
    await setUp();
    $('map-help-toggle').click();
    frame();

    labels()[0].click();

    expect(layersPressed).not.toHaveBeenCalled();
    expect($('map-help-mode-card-title').textContent).toBe('Layers');
  });

  it('"Open" leaves help mode and presses the control after all', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('basemap-toggle').click();

    $('map-help-mode-card-open').click();

    expect(helpModeOn()).toBe(false);
    expect(layersPressed).toHaveBeenCalledTimes(1);
  });

  it('a tap on the map itself goes through', async () => {
    await setUp();
    const mapTapped = vi.fn();
    $('map-canvas').addEventListener('click', mapTapped);
    $('map-help-toggle').click();

    $('map-canvas').click();

    expect(mapTapped).toHaveBeenCalledTimes(1);
    expect(helpModeOn()).toBe(true);
  });

  it('controls act as usual once help mode is off', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('map-help-mode-done').click();

    $('basemap-toggle').click();

    expect(layersPressed).toHaveBeenCalledTimes(1);
  });
});

describe('the bar', () => {
  it('"Tour" leaves help mode and asks for the tour', async () => {
    await setUp();
    const tour = vi.fn();
    document.addEventListener('snowdesk:map-help-requested', tour);
    $('map-help-toggle').click();

    $('map-help-mode-tour').click();

    expect(helpModeOn()).toBe(false);
    expect(tour).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:map-help-requested', tour);
  });

  it('Escape closes the card first, then help mode', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('basemap-toggle').click();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect($('map-help-mode-card').hidden).toBe(true);
    expect(helpModeOn()).toBe(true);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(helpModeOn()).toBe(false);
  });
});

describe('a sheet opened in help mode', () => {
  it('is keyed in a guide, with a badge for each part', async () => {
    await setUp();
    $('map-help-toggle').click();
    frame();

    $('region-sheet').hidden = false;
    frame();

    expect($('map-help-mode-guide').hidden).toBe(false);
    expect($('map-help-mode-guide-eyebrow').textContent).toBe('Region sheet');
    const rows = Array.from($('map-help-mode-guide-key').children);
    expect(rows.map((r) => r.textContent)).toEqual([
      '1Danger level for the region',
      '2Where it sits',
      '3Each problem',
    ]);
    expect($('map-help-mode-guide-more').getAttribute('href')).toBe('/help/#help-topic-bulletins');
    expect($('map-help-mode-guide-more').textContent).toBe('Reading a bulletin');
    expect($('map-help-mode-badges').children).toHaveLength(3);
    // The bar steps aside for the guide; the names go too.
    expect($('map-help-mode-bar').hidden).toBe(true);
    expect($('map-help-mode-labels').hidden).toBe(true);
  });

  it('is raised off its peek, so the parts are in view', async () => {
    await setUp();
    const sheet = $('region-sheet');
    sheet.querySelector('[data-sheet-handle]').addEventListener('click', () => {
      sheet.dataset.detent = 'half';
    });
    $('map-help-toggle').click();

    sheet.hidden = false;
    frame();

    expect(sheet.dataset.detent).toBe('half');
  });

  it('"Got it" puts the guide away until the sheet closes', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('region-sheet').hidden = false;
    frame();

    $('map-help-mode-guide-done').click();
    frame();
    expect($('map-help-mode-guide').hidden).toBe(true);
    expect($('map-help-mode-bar').hidden).toBe(false);

    $('region-sheet').hidden = true;
    frame();
    $('region-sheet').hidden = false;
    frame();
    expect($('map-help-mode-guide').hidden).toBe(false);
  });

  it('goes with its sheet', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('region-sheet').hidden = false;
    frame();

    $('region-sheet').hidden = true;
    frame();

    expect($('map-help-mode-guide').hidden).toBe(true);
    expect($('map-help-mode-badges').children).toHaveLength(0);
  });

  it('a sheet already open when help mode starts is guided at once', async () => {
    await setUp({ sheetOpen: true });

    $('map-help-toggle').click();
    frame();

    expect($('map-help-mode-guide').hidden).toBe(false);
  });

  it('a sheet already open is kept when help mode starts; other overlays close', async () => {
    await setUp({ sheetOpen: true });
    const sheetClose = vi.fn();
    const layersClose = vi.fn();
    window.pwaMapOverlays.register('region-sheet', { isOpen: () => true, close: sheetClose });
    window.pwaMapOverlays.register('basemap-menu', { isOpen: () => true, close: layersClose });

    $('map-help-toggle').click();

    expect(sheetClose).not.toHaveBeenCalled();
    expect(layersClose).toHaveBeenCalledTimes(1);
  });

  it('Escape steps back through help mode and never reaches the sheet', async () => {
    await setUp();
    const sheetEscape = vi.fn();
    document.addEventListener('keydown', sheetEscape);
    $('map-help-toggle').click();
    $('region-sheet').hidden = false;
    frame();
    $('basemap-toggle').click();

    const esc = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    esc();
    expect($('map-help-mode-card').hidden).toBe(true);
    expect($('map-help-mode-guide').hidden).toBe(false);
    esc();
    expect($('map-help-mode-guide').hidden).toBe(true);
    expect(helpModeOn()).toBe(true);
    esc();
    expect(helpModeOn()).toBe(false);

    expect(sheetEscape).not.toHaveBeenCalled();
    document.removeEventListener('keydown', sheetEscape);
  });

  it('a guide put away stays away while another sheet opens and closes beside it', async () => {
    await setUp();
    $('map-help-toggle').click();
    $('route-rail').hidden = false;
    frame();
    expect($('map-help-mode-guide-eyebrow').textContent).toBe('Route');
    $('map-help-mode-guide-done').click();

    $('region-sheet').hidden = false;
    frame();
    expect($('map-help-mode-guide-eyebrow').textContent).toBe('Region sheet');
    $('region-sheet').hidden = true;
    frame();

    expect($('map-help-mode-guide').hidden).toBe(true);
  });

  it('a sheet opened outside help mode gets no guide', async () => {
    await setUp();

    $('region-sheet').hidden = false;
    frame();

    expect($('map-help-mode-guide').hidden).toBe(true);
  });
});
