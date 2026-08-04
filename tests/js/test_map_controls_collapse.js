/*
 * tests/js/test_map_controls_collapse.js — Vitest unit tests for
 * static/js/map_controls_collapse.js.
 *
 * The module reads #map-controls-br / #map-controls-collapsible /
 * #map-controls-toggle and returns early if any is absent, so the fixture
 * (mirroring apps/public/templates/public/partials/_map_embed.html) must exist
 * BEFORE the module is imported. Each test re-imports via vi.resetModules()
 * so the IIFE runs fresh against its own fixture and localStorage state.
 *
 * Only state is asserted here — data-expanded, ARIA, the persisted
 * preference and the --map-controls-count custom property. The open/close
 * animation is pure CSS (static/css/map.css) and jsdom does not compute it;
 * the height it produces is covered by browser-side checks instead.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const STORAGE_KEY = 'snowdesk.map.controls.expanded';

/**
 * Build the control stack: layers + locate outside the group, four
 * collapsible controls inside it, toggle anchored last.
 *
 * @param {number} collapsibleCount - children rendered inside the group.
 */
function buildFixture(collapsibleCount = 4) {
  const items = Array.from(
    { length: collapsibleCount },
    (_, i) => `<div class="map-utility-pill" id="collapsible-${i}"></div>`,
  ).join('');
  document.body.innerHTML = `
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div class="map-utility-pill map-utility-pill--basemap" id="basemap-pill"></div>
      <div class="map-utility-pill map-utility-pill--locate"></div>
      <div id="map-controls-collapsible">
        <div class="map-controls-collapsible-inner">${items}</div>
      </div>
      <button type="button" id="map-controls-toggle"
              aria-controls="map-controls-collapsible" aria-expanded="true"></button>
    </div>
  `;
}

async function loadModule() {
  vi.resetModules();
  await import('../../static/js/map_controls_collapse.js');
}

const stack = () => document.getElementById('map-controls-br');
const toggle = () => document.getElementById('map-controls-toggle');

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  document.body.innerHTML = '';
  localStorage.clear();
});

describe('map controls collapse', () => {
  it('defaults to expanded when nothing is stored', async () => {
    buildFixture();
    await loadModule();
    expect(stack().dataset.expanded).toBe('true');
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(toggle().getAttribute('aria-label')).toBe('Hide map controls');
  });

  it('publishes the live child count for the CSS height calculation', async () => {
    buildFixture(3);
    await loadModule();
    const group = document.getElementById('map-controls-collapsible');
    // Read from the DOM, not hardcoded — a flag-gated control must not leave
    // the group clipped short or standing open too tall.
    expect(group.style.getPropertyValue('--map-controls-count')).toBe('3');
  });

  it('collapses on click and persists the choice', async () => {
    buildFixture();
    await loadModule();
    toggle().click();
    expect(stack().dataset.expanded).toBe('false');
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    expect(toggle().getAttribute('aria-label')).toBe('Show map controls');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false');
  });

  it('restores a stored collapsed preference on load', async () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    buildFixture();
    await loadModule();
    expect(stack().dataset.expanded).toBe('false');
    expect(toggle().getAttribute('aria-label')).toBe('Show map controls');
  });

  it('forces the group open for the help tour without touching the preference', async () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    buildFixture();
    await loadModule();
    expect(stack().dataset.expanded).toBe('false');

    // Steps target controls inside the group; a collapsed one still matches
    // querySelector, so the ring would land on a zero-height box.
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-open'));
    expect(stack().dataset.expanded).toBe('true');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false');

    document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'));
    expect(stack().dataset.expanded).toBe('false');
  });

  it('leaves an already-expanded group alone across the tour', async () => {
    buildFixture();
    await loadModule();
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-open'));
    expect(stack().dataset.expanded).toBe('true');
    // Not forced open, so closing the tour must not collapse it.
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'));
    expect(stack().dataset.expanded).toBe('true');
  });

  it('adopts collapsed when tapped while the tour holds it open', async () => {
    localStorage.setItem(STORAGE_KEY, 'false');
    buildFixture();
    await loadModule();
    document.dispatchEvent(new CustomEvent('snowdesk:map-help-open'));
    expect(stack().dataset.expanded).toBe('true');

    // Without this the tap would appear to do nothing (already open) and the
    // group would silently revert when the tour closed.
    toggle().click();
    expect(stack().dataset.expanded).toBe('false');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false');

    document.dispatchEvent(new CustomEvent('snowdesk:map-help-close'));
    expect(stack().dataset.expanded).toBe('false');
  });

  it('is inert when the stack is absent', async () => {
    document.body.innerHTML = '<div id="unrelated"></div>';
    await expect(loadModule()).resolves.not.toThrow();
  });
});

describe('translated labels (SNOW-620)', () => {
  /**
   * Append a strings template to the fixture, as _map_embed.html does.
   *
   * @param {Object<string, string>} entries
   */
  function mountStrings(entries) {
    const tpl = document.createElement('template');
    tpl.id = 'map-controls-strings-template';
    tpl.innerHTML = Object.entries(entries)
      .map(([key, value]) => `<span data-string="${key}">${value}</span>`)
      .join('');
    document.body.appendChild(tpl);
  }

  it('labels the toggle from the strings template when it is present', async () => {
    buildFixture();
    mountStrings({ collapse: 'Bedienelemente ausblenden', expand: 'Bedienelemente' });
    await loadModule();

    // Rendered expanded, so the label describes what the next tap does.
    expect(toggle().getAttribute('aria-label')).toBe('Bedienelemente ausblenden');

    toggle().click();

    expect(toggle().getAttribute('aria-label')).toBe('Bedienelemente');
  });

  it('falls back to English when the partial did not render the template', async () => {
    // The real case this guards: a page that includes the module but not
    // the surface partial. The control must still be labelled.
    buildFixture();
    await loadModule();

    expect(toggle().getAttribute('aria-label')).toBe('Hide map controls');

    toggle().click();

    expect(toggle().getAttribute('aria-label')).toBe('Show map controls');
  });
});
