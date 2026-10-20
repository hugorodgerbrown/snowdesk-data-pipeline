/*
 * tests/js/test_map_help_chapters.js — Vitest unit tests for the SNOW-1118
 * tour shape in static/js/map_help.js.
 *
 * SNOW-1118 rebuilt the tour as seven steps in four chapters, about the map
 * and not only its buttons. That needed four things the step engine could
 * not do, and each is pinned here against the template's own markup shape:
 *
 *   - a progress bar of segments grouped under chapter names;
 *   - a step with no target (the colours), and a step's [data-help-extra]
 *     (the danger scale, the four lists) cloned into the card;
 *   - a step with several targets (search, locate, zoom: a ring each) and
 *     one with alternatives ("#region-sheet | #region-readout");
 *   - an action step: "Tap a region" asks map.js to select a region, holds
 *     the tour open while the region sheet it opens announces itself to
 *     window.pwaMapOverlays, and lets the region go again on leaving.
 *
 * test_map_help.js keeps the sequencing, persistence and keyboard cases on
 * the older plain-<li> shape, which map_help.js still reads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SHOW_REGION = 'snowdesk:map-help-show-region';
const RELEASE_REGION = 'snowdesk:map-help-release-region';

/**
 * The tour's markup in the template's shape, cut to one step of each kind.
 *
 * @param {{readout?: boolean}} [opts] Render #region-readout, the action
 *   step's desktop alternative. Off to model a phone, where it is hidden.
 */
function buildFixture({ readout = true } = {}) {
  document.body.innerHTML = `
    <button id="search-toggle"></button>
    <button id="locate-toggle"></button>
    <button id="map-zoom-toggle"></button>
    ${readout ? '<div id="region-readout"></div>' : ''}
    <div id="region-sheet" hidden></div>
    <ol id="map-help-chapters" hidden>
      <li data-chapter="read">Read the map</li>
      <li data-chapter="move">Move</li>
      <li data-chapter="help">Help</li>
    </ol>
    <ul id="map-help-steps" hidden>
      <li data-help-target="" data-help-chapter="read" data-help-title="Colour is the day's danger">
        <p data-help-body>Every region is painted with its danger level.</p>
        <div data-help-extra><span class="danger-tile" data-level="low">1</span></div>
      </li>
      <li data-help-target="#region-sheet | #region-readout" data-help-chapter="read"
          data-help-action="show-region" data-help-title="Tap a region">
        <p data-help-body>Its name and danger level open here.</p>
      </li>
      <li data-help-target="#search-toggle, #locate-toggle, #map-zoom-toggle"
          data-help-chapter="move" data-help-title="Find your way">
        <p data-help-body>Search, locate and zoom.</p>
      </li>
      <li data-help-target="#map-help-toggle" data-help-chapter="help" data-help-title="Help is always here">
        <p data-help-body>Tap ? to take this tour again.</p>
      </li>
    </ul>
    <button id="map-help-toggle" type="button">?</button>
    <div id="map-help-overlay" hidden data-overlay data-overlay-persist="snowdesk.map.help=seen" data-map-help-no-autostart>
      <div id="map-help-ring" class="map-help-ring" aria-hidden="true"></div>
      <div id="map-help-tooltip" tabindex="-1">
        <button type="button" id="map-help-close" data-action="dismiss">×</button>
        <div id="map-help-progress" aria-hidden="true" hidden></div>
        <p id="map-help-step-count" aria-live="polite"></p>
        <span id="map-help-step-template" hidden>Step {n} of {total}</span>
        <h3 id="map-help-tooltip-title"></h3>
        <p id="map-help-tooltip-body"></p>
        <div id="map-help-tooltip-extra" hidden></div>
        <button type="button" id="map-help-back">Back</button>
        <button type="button" id="map-help-next" data-label-next="Next" data-label-done="Done">Next</button>
      </div>
    </div>
  `;
}

/**
 * Stand in for map.js and the region sheet: a show-region request opens
 * #region-sheet the way MapSheet does, announcing itself to the overlay
 * registry first — which is what would close the tour if it did not hold.
 * A release records itself; the registry's close hides the sheet.
 */
function fakeRegionSheet() {
  const sheet = document.getElementById('region-sheet');
  const released = vi.fn();
  window.pwaMapOverlays.register('region-sheet', {
    isOpen: () => !sheet.hidden,
    close: () => { sheet.hidden = true; },
  });
  document.addEventListener(SHOW_REGION, () => {
    window.pwaMapOverlays.opening('region-sheet');
    sheet.hidden = false;
  });
  document.addEventListener(RELEASE_REGION, released);
  return { sheet, released };
}

async function loadModules() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  await import('../../static/js/overlays.js');
  await import('../../static/js/map_help.js');
}

const $ = (id) => document.getElementById(id);
const title = () => $('map-help-tooltip-title').textContent;
const tourOpen = () => !$('map-help-overlay').hasAttribute('hidden');
const next = () => $('map-help-next').click();
const visibleRings = () =>
  Array.from(document.querySelectorAll('.map-help-ring')).filter((el) => !el.hidden);

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('the progress bar', () => {
  it('groups a segment per step under its chapter, filled up to this step', async () => {
    buildFixture();
    await loadModules();
    fakeRegionSheet();
    $('map-help-toggle').click();
    next();

    const progress = $('map-help-progress');
    expect(progress.hidden).toBe(false);
    const chapters = Array.from(progress.querySelectorAll('.map-help-progress-chapter'));
    expect(chapters.map((c) => c.textContent)).toEqual(['Read the map', 'Move', 'Help']);
    expect(chapters.map((c) => c.dataset.state)).toEqual(['current', 'other', 'other']);
    const segments = Array.from(progress.querySelectorAll('.map-help-progress-segment'));
    expect(segments.map((s) => s.dataset.state)).toEqual(['done', 'done', 'todo', 'todo']);
  });

  it('leaves the step count to screen readers once the bar says it', async () => {
    buildFixture();
    await loadModules();
    $('map-help-toggle').click();

    const count = $('map-help-step-count');
    expect(count.textContent).toBe('Step 1 of 4');
    expect(count.classList.contains('map-help-visually-hidden')).toBe(true);
  });
});

describe('a step about the map itself', () => {
  it('draws no ring, and puts its extra under the copy', async () => {
    buildFixture();
    await loadModules();
    $('map-help-toggle').click();

    expect(title()).toBe("Colour is the day's danger");
    expect($('map-help-tooltip-body').textContent).toBe('Every region is painted with its danger level.');
    expect(visibleRings()).toHaveLength(0);
    expect($('map-help-tooltip-extra').hidden).toBe(false);
    expect($('map-help-tooltip-extra').querySelector('.danger-tile')).not.toBeNull();
  });

  it('clears the extra from a step that has none', async () => {
    buildFixture();
    await loadModules();
    fakeRegionSheet();
    $('map-help-toggle').click();
    next();

    expect($('map-help-tooltip-extra').hidden).toBe(true);
    expect($('map-help-tooltip-extra').childNodes).toHaveLength(0);
  });
});

describe('a step with several targets', () => {
  it('rings each one, and drops the extra rings on the next step', async () => {
    buildFixture();
    await loadModules();
    fakeRegionSheet();
    $('map-help-toggle').click();
    next();
    next();

    expect(title()).toBe('Find your way');
    expect(visibleRings()).toHaveLength(3);

    next();
    expect(visibleRings()).toHaveLength(1);
  });
});

describe('"Tap a region"', () => {
  it('asks for a region, and stays open while the sheet it opens announces itself', async () => {
    buildFixture({ readout: false });
    await loadModules();
    const { sheet } = fakeRegionSheet();
    const asked = vi.fn();
    document.addEventListener(SHOW_REGION, asked);
    $('map-help-toggle').click();

    next();

    expect(asked).toHaveBeenCalledTimes(1);
    expect(sheet.hidden).toBe(false);
    expect(tourOpen()).toBe(true);
    expect(title()).toBe('Tap a region');
    expect(visibleRings()).toHaveLength(1);
  });

  it('lets the region go, and closes its sheet, on leaving the step', async () => {
    buildFixture({ readout: false });
    await loadModules();
    const { sheet, released } = fakeRegionSheet();
    $('map-help-toggle').click();
    next();

    next();

    expect(released).toHaveBeenCalledTimes(1);
    expect(sheet.hidden).toBe(true);
    expect(tourOpen()).toBe(true);
    expect(title()).toBe('Find your way');
  });

  it('lets the region go when the tour is closed on that step', async () => {
    buildFixture({ readout: false });
    await loadModules();
    const { released } = fakeRegionSheet();
    $('map-help-toggle').click();
    next();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(tourOpen()).toBe(false);
    // At least once, not exactly: Escape is heard on document, and the
    // tours earlier cases imported still listen there (each import is a
    // fresh IIFE; none of them can be unloaded).
    expect(released).toHaveBeenCalled();
  });

  it('points at the readout where no sheet opens (a desktop)', async () => {
    buildFixture({ readout: true });
    await loadModules();
    $('map-help-toggle').click();

    next();

    expect(title()).toBe('Tap a region');
    expect(visibleRings()).toHaveLength(1);
  });

  it('is skipped the way the visitor was going when there is nothing to show', async () => {
    // A phone, before the regions have loaded: nothing answers the request,
    // so no sheet opens and the readout is not on screen either.
    buildFixture({ readout: false });
    await loadModules();
    $('map-help-toggle').click();

    next();
    expect(title()).toBe('Find your way');

    $('map-help-back').click();
    expect(title()).toBe("Colour is the day's danger");
  });

  it('is still closed by another overlay opening', async () => {
    buildFixture();
    await loadModules();
    $('map-help-toggle').click();

    window.pwaMapOverlays.opening('basemap-menu');

    expect(tourOpen()).toBe(false);
  });
});
