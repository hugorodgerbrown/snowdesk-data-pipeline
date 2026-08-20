/*
 * tests/js/test_map_help.js — Vitest unit tests for static/js/map_help.js
 * (SNOW-457, SNOW-496).
 *
 * Ports the behaviour/persistence/keyboard subset of
 * tests/e2e/test_map_help.py's 9 cases onto the Vitest + jsdom harness.
 * None of the retired Playwright assertions actually checked pixel
 * positioning (ring/tooltip placement is computed internally via
 * `getBoundingClientRect`, but never asserted on) — jsdom returns a
 * zero-rect from `getBoundingClientRect` regardless, so `render()`'s
 * internal `positionRing`/`positionTooltip` calls run without error and
 * produce zeroed inline styles that this file doesn't assert against.
 * `Element.prototype.scrollIntoView` (called on every step render) is
 * stubbed globally in tests/js/setup.js.
 *
 * map_help.js's own dismiss ("×" close button) is handled by
 * overlays.js's shared delegated handler (SNOW-486), so both modules load
 * here, matching production order. Both are dynamically re-imported per
 * test (`vi.resetModules()` + `await import(...)`) so each test gets a
 * fresh IIFE run against its own fixture/localStorage state.
 *
 * SNOW-535 replaced the "does auto-start read #home-intro's visibility"
 * mechanism with a data-driven opt-out (`data-map-help-no-autostart` on
 * `#map-help-overlay`, set by home.html) and added a
 * `snowdesk:map-help-requested` CustomEvent — dispatched by
 * `#home-intro`'s "Explore the map" CTA (home_intro.js) — as a second way
 * to open the tour alongside the "?" roundel. `buildFixture`'s
 * `homeIntroShowing` option and the home-intro fixture markup were dropped
 * accordingly; `noAutostart` replaces them wherever a case needs auto-start
 * suppressed to isolate a different open path.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'snowdesk.map.help';
const DISMISSED_VALUE = 'seen';

// Mirrors the template's 13 step definitions (apps/public/templates/public/
// partials/_map_embed.html), in template order.
//
// Every one of those targets is server-rendered unconditionally — none of
// them is gated on a waffle flag or an auth check any more (#favourite-add-btn
// since SNOW-414, #report-btn since SNOW-333), so there is no longer a
// production case where a step's target is genuinely missing. The absent-
// target filter in map_help.js is still defensive code worth covering, so
// the DOM fixture below deliberately omits two targets to exercise it —
// 11 of the 13 steps stay active.
const STEP_DEFS = [
  { target: '#region-readout', title: 'Status ribbon', body: 'Names the region selected and its danger for the day.' },
  { target: '#region-readout-action', title: 'View bulletin', body: 'Click through to the bulletin detail page.' },
  { target: '#search-toggle', title: 'Find a region or resort', body: 'Search regions and resorts by name.' },
  { target: '#basemap-toggle', title: 'Basemap and layers', body: 'Switch base maps and toggle country outlines, region overlays and resorts.' },
  { target: '#locate-toggle', title: 'Locate me', body: 'Centre the map on your current position.' },
  // The download step follows its control into the bottom-right stack; the
  // tour walks the map in spatial order.
  { target: '#map-download-control', title: 'Download region', body: "Download the selected region's basemap for offline access." },
  { target: '#favourite-add-btn', title: 'Add a favourite', body: 'Pin a spot to your favourites for quick access later.' },
  { target: '#report-btn', title: 'Report conditions', body: 'Share a quick field observation with other visitors.' },
  { target: '#map-help-toggle', title: 'Map help', body: 'Reopen this tour whenever you need it.' },
  { target: '#map-controls-toggle', title: 'Hide the controls', body: 'Collapse the controls to clear the map; your choice is remembered.' },
  { target: '#season-scrubber', title: 'Timeline scrubber', body: 'Run through the season day by day.' },
  { target: '#map-legend-toggle', title: 'Map information', body: 'View attribution and EAWS danger level key.' },
  { target: '#map-date-ribbon', title: 'Map display date', body: 'The date currently shown on the map.' },
];
// Held out of the DOM fixture purely to exercise the absent-target filter.
const ABSENT_TARGETS = new Set(['#favourite-add-btn', '#report-btn']);
const ACTIVE_STEP_COUNT = STEP_DEFS.filter((s) => !ABSENT_TARGETS.has(s.target)).length;

// #map-help-toggle is a step target AND the tour's own trigger, which
// buildFixture already renders as a <button> below. Generating a placeholder
// <div> for it too would put a duplicate id in the document — getElementById
// returns the first match, so every
// `document.getElementById('map-help-toggle').click()` in these tests would
// hit the inert div and the tour would never open. The step stays active;
// only its placeholder is skipped.
const SELF_RENDERED_TARGETS = new Set(['#map-help-toggle']);

function stepsMarkup() {
  return STEP_DEFS.map(
    (s) => `<li data-help-target="${s.target}" data-help-title="${s.title}">${s.body}</li>`,
  ).join('\n');
}

function targetsMarkup() {
  return STEP_DEFS.filter(
    (s) => !ABSENT_TARGETS.has(s.target) && !SELF_RENDERED_TARGETS.has(s.target),
  )
    .map((s) => `<div id="${s.target.slice(1)}"></div>`)
    .join('\n');
}

/**
 * Build the #map-help-overlay fixture (mirroring
 * apps/public/templates/public/partials/_map_embed.html) plus its step targets,
 * optionally carrying the data-map-help-no-autostart opt-out home.html
 * sets (SNOW-535) to gate map_help.js's auto-start.
 */
function buildFixture({ noAutostart = false } = {}) {
  document.body.innerHTML = `
    ${targetsMarkup()}
    <ul id="map-help-steps" hidden>${stepsMarkup()}</ul>
    <button id="map-help-toggle" type="button">?</button>
    <div id="map-help-overlay" hidden role="dialog" data-overlay data-overlay-persist="snowdesk.map.help=seen" ${noAutostart ? 'data-map-help-no-autostart' : ''}>
      <div id="map-help-ring" aria-hidden="true"></div>
      <div id="map-help-tooltip" class="map-help-tooltip" tabindex="-1">
        <button type="button" id="map-help-close" data-action="dismiss" aria-label="Close">×</button>
        <p id="map-help-step-count" aria-live="polite"></p>
        <span id="map-help-step-template" hidden>Step {n} of {total}</span>
        <h3 id="map-help-tooltip-title"></h3>
        <p id="map-help-tooltip-body"></p>
        <div class="map-help-tooltip-actions">
          <button type="button" id="map-help-back">Back</button>
          <button type="button" id="map-help-next" data-label-next="Next" data-label-done="Done">Next</button>
        </div>
      </div>
    </div>
  `;
}

async function loadModules() {
  vi.resetModules();
  await import('../../static/js/overlays.js');
  await import('../../static/js/map_help.js');
}

function overlayHidden() {
  return document.getElementById('map-help-overlay').hasAttribute('hidden');
}

function stepCountText() {
  return document.getElementById('map-help-step-count').textContent;
}

function currentStepAndTotal() {
  const match = /(\d+)\D+(\d+)/.exec(stepCountText());
  if (!match) throw new Error(`unexpected step-count text: ${stepCountText()}`);
  return [Number(match[1]), Number(match[2])];
}

function tooltipTitle() {
  return document.getElementById('map-help-tooltip-title').textContent;
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch (_e) {
    // Ignore.
  }
});

describe('opening the tour', () => {
  it('opens on step 1 with the correct title via the "?" roundel', async () => {
    // noAutostart suppresses the first-load auto-start so this only asserts
    // the toggle-driven open path, matching the homepage's own opt-out.
    buildFixture({ noAutostart: true });
    await loadModules();

    expect(overlayHidden()).toBe(true);

    document.getElementById('map-help-toggle').click();

    expect(overlayHidden()).toBe(false);
    const [step] = currentStepAndTotal();
    expect(step).toBe(1);
    // First active step in this fixture is #region-readout — "Status
    // ribbon" — which is also first in the template's own order.
    expect(tooltipTitle()).toBe('Status ribbon');
  });
});

describe('Next / Back', () => {
  it('Next advances to a different step; Back returns to the first', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('map-help-toggle').click();
    const firstTitle = tooltipTitle();

    document.getElementById('map-help-next').click();
    const secondTitle = tooltipTitle();
    expect(secondTitle).not.toBe(firstTitle);
    expect(overlayHidden()).toBe(false);

    document.getElementById('map-help-back').click();
    expect(tooltipTitle()).toBe(firstTitle);
  });
});

describe('close', () => {
  it('hides the overlay and persists the dismissed flag', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('map-help-toggle').click();

    document.getElementById('map-help-close').click();

    expect(overlayHidden()).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });
});

describe('reload after dismissal', () => {
  it('does not auto-start when the dismissed flag is already set', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();

    expect(overlayHidden()).toBe(true);
  });
});

describe('re-opening after dismissal', () => {
  it('the "?" roundel restarts the tour at step 1 regardless of stored state', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('map-help-toggle').click();
    document.getElementById('map-help-next').click();
    document.getElementById('map-help-close').click();
    expect(overlayHidden()).toBe(true);

    document.getElementById('map-help-toggle').click();

    expect(overlayHidden()).toBe(false);
    const [step] = currentStepAndTotal();
    expect(step).toBe(1);
  });
});

describe('Escape', () => {
  it('closes the overlay and persists the dismissed flag', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('map-help-toggle').click();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(overlayHidden()).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });
});

describe('steps whose target is missing from the DOM', () => {
  it('skips targets absent from the DOM, walking exactly the present ones', async () => {
    buildFixture();
    await loadModules();
    expect(document.getElementById('favourite-add-btn')).toBeNull();
    expect(document.getElementById('report-btn')).toBeNull();

    document.getElementById('map-help-toggle').click();
    const [, total] = currentStepAndTotal();
    expect(total).toBe(ACTIVE_STEP_COUNT);

    const titles = [];
    for (let i = 0; i < total; i++) {
      titles.push(tooltipTitle());
      document.getElementById('map-help-next').click();
    }

    expect(titles).not.toContain('Add a favourite');
    expect(titles).not.toContain('Report conditions');
    expect(overlayHidden()).toBe(true);
  });
});

describe('auto-start', () => {
  it('auto-starts on load when no opt-out attribute is set', async () => {
    buildFixture();
    await loadModules();

    expect(overlayHidden()).toBe(false);
  });

  it('does not auto-start when the embedding page opts out via data-map-help-no-autostart', async () => {
    // SNOW-535: home.html sets this — the tour there only opens via the "?"
    // roundel or #home-intro's "Explore the map" CTA.
    buildFixture({ noAutostart: true });
    await loadModules();

    expect(overlayHidden()).toBe(true);
  });
});

describe('snowdesk:map-help-requested', () => {
  it('opens the tour from step 1, same as the "?" roundel', async () => {
    // SNOW-535: this is the event home_intro.js dispatches when its
    // "Explore the map" CTA is clicked, alongside the shared dismiss
    // handling — noAutostart isolates this from the auto-start path so the
    // assertion is only about the event-driven open.
    buildFixture({ noAutostart: true });
    await loadModules();

    expect(overlayHidden()).toBe(true);

    document.dispatchEvent(new CustomEvent('snowdesk:map-help-requested'));

    expect(overlayHidden()).toBe(false);
    const [step] = currentStepAndTotal();
    expect(step).toBe(1);
  });
});
