/*
 * tests/js/test_map_resort_guide.js — Vitest unit tests for
 * static/js/map_resort_guide.js (SNOW-1231).
 *
 * The "Find a resort" walkthrough. These cases pin what Hugo asked for:
 * it starts on request and opens search with popular resorts; each step
 * moves on when the visitor does it (a resort's sheet opening, then its
 * region's); nothing is locked — closing a sheet or leaving the day folds
 * it into the paused pill; out of season it runs on the example's day and
 * puts the map back on today at the end, saying so; and the finish card
 * offers the picked resort's pin.
 *
 * Search, the sheets and favourites.js are stand-ins in the DOM; the pure
 * cores and the strings reader are the real modules.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const DAY = '2026-04-10';
const TODAY = '2026-10-26';
const POPULAR = [
  { slug: 'verbier', name: 'Verbier', region_id: 'CH-4115', region_name: 'Martigny-Verbier' },
];

function buildFixture({ guideDate = DAY, signedIn = false } = {}) {
  document.body.innerHTML = `
    <div id="season-scrubber" data-today="${TODAY}" data-date="${TODAY}"></div>
    <span id="map-example-day-badge" hidden>Example day</span>
    <div id="map-example-toast" class="hidden"><p data-toast-body></p></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" type="button">Search</button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="map">
      <div id="map-resort-guide" data-guide-date="${guideDate}" data-map-url="/map/" hidden>
        <div data-resort-guide-lead>
          <p data-resort-guide-eyebrow></p>
          <button type="button" data-resort-guide-exit>Exit</button>
          <strong data-resort-guide-day></strong>
        </div>
      </div>
      <div id="map-resort-guide-bar" hidden>
        <p data-resort-guide-eyebrow></p>
        <button type="button" data-resort-guide-exit>Exit</button>
        <div data-resort-guide-step="resort"><p>Avalanche bulletins are regional.</p></div>
        <div data-resort-guide-step="region" hidden><strong data-resort-guide-region></strong>
          <button type="button" data-resort-guide-keep>Keep exploring this example</button>
          <button type="button" data-resort-guide-finish>Finish</button></div>
        <ol class="map-example-guide-dots"><li></li><li></li><li></li></ol>
      </div>
      <div id="map-resort-guide-pill" hidden>
        <span data-resort-guide-pill="paused">paused</span>
        <span data-resort-guide-pill="off-day" hidden>off day</span>
        <span data-resort-guide-pill="exploring" hidden>Example day · 10 Apr</span>
        <button type="button" data-resort-guide-resume>Resume</button>
        <button type="button" data-resort-guide-back-to-today hidden>Back to today</button>
        <button type="button" data-resort-guide-back-to-day hidden></button>
        <button type="button" data-resort-guide-exit>Exit</button>
      </div>
      <div id="map-resort-guide-finish" hidden>
        <h2 id="map-resort-guide-finish-title"></h2>
        ${signedIn
    ? '<button type="button" data-resort-star data-resort-guide-pin data-favourited="false" hidden></button>'
    : '<a href="/accounts/sign-in/" data-resort-guide-signin hidden></a>'}
        <button type="button" data-resort-guide-done>Done</button>
      </div>
      <script id="map-resort-guide-popular" type="application/json">${JSON.stringify(POPULAR)}</script>
    </div>
    <div id="place-sheet" hidden></div>
    <div id="region-sheet" hidden><button type="button" data-action="dismiss">×</button></div>
  `;
}

const $ = (id) => document.getElementById(id);
const bar = () => $('map-resort-guide-bar');
const pill = () => $('map-resort-guide-pill');

let added = [];
let events = [];
const realAdd = {
  document: document.addEventListener.bind(document),
  window: window.addEventListener.bind(window),
};
const WATCHED = ['snowdesk:search-suggestions', 'snowdesk:scrub-to', 'snowdesk:resort-open',
  'snowdesk:region-open'];

async function load() {
  vi.resetModules();
  delete window.pwaResortGuideCore;
  delete window.pwaExampleGuideCore;
  delete window.pwaStrings;
  await import('../../static/js/i18n_strings.js');
  await import('../../static/js/example_guide_core.js');
  await import('../../static/js/resort_guide_core.js');
  document.addEventListener = (...args) => {
    added.push([document, args]);
    realAdd.document(...args);
  };
  window.addEventListener = (...args) => {
    added.push([window, args]);
    realAdd.window(...args);
  };
  await import('../../static/js/map_resort_guide.js');
  document.addEventListener = realAdd.document;
  window.addEventListener = realAdd.window;
}

/** Let the MutationObserver run, then the guide's timers. */
async function settle(ms = 1000) {
  await Promise.resolve();
  vi.advanceTimersByTime(ms);
  await Promise.resolve();
  vi.advanceTimersByTime(ms);
}

const emitted = (name) => window.pwaTelemetry.emit.mock.calls.filter(([n]) => n === name);
const dispatched = (name) => events.filter((e) => e.type === name);

function start() {
  document.dispatchEvent(new CustomEvent('snowdesk:resort-guide-requested'));
}

/** What map.js's search does when the toggle is pressed. */
function wireSearch() {
  $('search-toggle').addEventListener('click', () => {
    $('search-pill').setAttribute('data-state', 'expanded');
    $('search-results').hidden = false;
  });
}

function openResortSheet({ favourited = false, signedInSheet = true } = {}) {
  $('search-pill').setAttribute('data-state', 'collapsed');
  $('search-results').hidden = true;
  $('place-sheet').innerHTML = `
    <div data-panel-key="resort:verbier"><h2>Verbier</h2>
    ${signedInSheet ? `<button data-resort-star data-resort-slug="verbier" data-resort-name="Verbier"
      data-resort-lat="46.1" data-resort-lon="7.2" data-favourited="${favourited}"></button>`
    : '<a href="/accounts/sign-in/">Sign in to save</a>'}</div>
    <div data-place-link-card><a data-testid="resort-region-link" data-panel-open="region"
      data-panel-id="CH-4115"><span class="font-semibold">Martigny-Verbier</span></a></div>`;
  $('place-sheet').hidden = false;
}

function openRegionSheet() {
  $('place-sheet').hidden = true;
  $('region-sheet').hidden = false;
}

async function walkToRegion() {
  start();
  openResortSheet();
  await settle();
  openRegionSheet();
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  buildFixture();
  window.pwaTelemetry = { emit: vi.fn() };
  window.pwaPlaceSheet = { detent: vi.fn() };
  window.MapSheet = { canQueueMutations: () => true };
  events = [];
  WATCHED.forEach((name) => realAdd.document(name, (e) => events.push(e)));
});

afterEach(() => {
  added.forEach(([target, args]) => target.removeEventListener(...args));
  added = [];
  vi.useRealTimers();
  delete window.pwaPlaceSheet;
  delete window.MapSheet;
});

describe('starting', () => {
  it('waits to be asked', async () => {
    await load();
    await settle();

    expect(bar().hidden).toBe(true);
    expect(pill().hidden).toBe(true);
    expect(emitted('map.resort_guide.opened')).toHaveLength(0);
  });

  it('opens search with the popular resorts, led by its words', async () => {
    await load();
    wireSearch();

    start();

    const offer = dispatched('snowdesk:search-suggestions')[0].detail;
    expect(offer.resorts).toEqual(POPULAR);
    expect(offer.heading).toBe('Popular resorts');
    expect(offer.lead.textContent).toContain('Find a resort · 1 of 3');
    expect($('search-pill').getAttribute('data-state')).toBe('expanded');
    expect(emitted('map.resort_guide.opened')[0][1]).toEqual({ offseason: true });
  });

  it('out of season, moves the map to the example\'s day and marks it', async () => {
    await load();

    start();

    expect(dispatched('snowdesk:scrub-to')[0].detail).toEqual({ date: DAY, source: 'resort-guide' });
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: DAY } }));
    expect($('map-example-day-badge').hidden).toBe(false);
  });

  it('in season, keeps the map\'s day', async () => {
    buildFixture({ guideDate: '' });
    await load();

    start();

    expect(dispatched('snowdesk:scrub-to')).toHaveLength(0);
    expect($('map-example-day-badge').hidden).toBe(true);
  });
});

describe('the steps', () => {
  beforeEach(() => buildFixture({ guideDate: '' }));

  it('moves to the resort step when a resort\'s sheet opens, and names its region', async () => {
    await load();
    start();

    openResortSheet();
    await settle();

    expect(bar().hidden).toBe(false);
    expect(bar().querySelector('[data-resort-guide-region]').textContent).toBe('Martigny-Verbier');
    expect(bar().querySelector('[data-resort-guide-eyebrow]').textContent).toBe('Find a resort · 2 of 3');
    // The card holding the row, so the ring is not clipped at its corners.
    expect(document.querySelector('[data-place-link-card]')
      .hasAttribute('data-resort-guide-ring')).toBe(true);
    expect(dispatched('snowdesk:search-suggestions').at(-1).detail.resorts).toEqual([]);
    expect(emitted('map.resort_guide.step_completed')[0][1]).toEqual({ step: 'search' });
  });

  it('moves to the region step when the region\'s sheet opens', async () => {
    await load();
    await walkToRegion();

    expect(bar().hidden).toBe(false);
    expect(bar().querySelector('[data-resort-guide-step="region"]').hidden).toBe(false);
    expect(document.querySelector('[data-resort-guide-ring]')).toBeNull();
  });

  it('folds into the paused pill when the sheet is closed, and Resume reopens it', async () => {
    await load();
    start();
    openResortSheet();
    await settle();

    $('place-sheet').hidden = true;
    await settle();

    expect(bar().hidden).toBe(true);
    expect(pill().hidden).toBe(false);
    pill().querySelector('[data-resort-guide-resume]').click();
    expect(dispatched('snowdesk:resort-open')[0].detail).toEqual({ slug: 'verbier' });
  });

  it('signed out, still knows the resort from the sheet\'s panel key', async () => {
    await load();
    start();
    openResortSheet({ signedInSheet: false });
    await settle();

    $('place-sheet').hidden = true;
    await settle();
    pill().querySelector('[data-resort-guide-resume]').click();

    expect(dispatched('snowdesk:resort-open')[0].detail).toEqual({ slug: 'verbier' });
  });

  it('Resume on the region step reopens the region', async () => {
    await load();
    await walkToRegion();

    $('region-sheet').hidden = true;
    await settle();
    pill().querySelector('[data-resort-guide-resume]').click();

    expect(dispatched('snowdesk:region-open')[0].detail).toEqual({ region_id: 'CH-4115' });
  });
});

describe('the day', () => {
  it('offers the way back to the example\'s day when the map leaves it', async () => {
    await load();
    start();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: DAY } }));
    openResortSheet();
    await settle();

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-04-11' } }));
    await settle();

    expect(pill().hidden).toBe(false);
    expect(pill().querySelector('[data-resort-guide-pill="off-day"]').hidden).toBe(false);
    const back = pill().querySelector('[data-resort-guide-back-to-day]');
    expect(back.hidden).toBe(false);
    back.click();
    expect(dispatched('snowdesk:scrub-to').at(-1).detail.date).toBe(DAY);
  });
});

describe('keep exploring (SNOW-1244)', () => {
  it('keeps the bulletin and the day, with the way back on the pill', async () => {
    await load();
    await walkToRegion();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: DAY } }));
    await settle();
    const scrubs = dispatched('snowdesk:scrub-to').length;

    bar().querySelector('[data-resort-guide-keep]').click();

    expect(bar().hidden).toBe(true);
    expect(pill().hidden).toBe(false);
    expect(pill().querySelector('[data-resort-guide-pill="exploring"]').hidden).toBe(false);
    expect(pill().querySelector('[data-resort-guide-resume]').hidden).toBe(true);
    expect(pill().querySelector('[data-resort-guide-exit]').hidden).toBe(true);
    expect(dispatched('snowdesk:scrub-to')).toHaveLength(scrubs);
    expect($('map-resort-guide-finish').hidden).toBe(true);
    expect(emitted('map.resort_guide.finished')[0][1]).toEqual({ kept: true });

    pill().querySelector('[data-resort-guide-back-to-today]').click();

    expect(pill().hidden).toBe(true);
    expect(dispatched('snowdesk:scrub-to').at(-1).detail.date).toBe(TODAY);
    expect($('map-resort-guide-finish').hidden).toBe(false);
    expect(emitted('map.resort_guide.left')).toHaveLength(1);
  });
});

describe('leaving', () => {
  it('Exit puts the map back on today and says so', async () => {
    await load();
    start();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: DAY } }));

    document.querySelector('[data-resort-guide-exit]').click();

    expect(dispatched('snowdesk:scrub-to').at(-1).detail).toEqual({ date: TODAY, source: 'resort-guide' });
    expect($('map-example-toast').classList.contains('hidden')).toBe(false);
    expect($('map-example-toast').textContent).toMatch(/^Back to today · /);
    expect(emitted('map.resort_guide.exited')[0][1]).toEqual({ step: 'search' });
    expect(dispatched('snowdesk:search-suggestions').at(-1).detail.resorts).toEqual([]);
  });

  it('Finish closes the bulletin and offers to sign in to pin the resort', async () => {
    await load();
    await walkToRegion();
    const closed = vi.fn();
    $('region-sheet').querySelector('[data-action="dismiss"]').addEventListener('click', closed);

    bar().querySelector('[data-resort-guide-finish]').click();

    expect(closed).toHaveBeenCalled();
    expect($('map-resort-guide-finish').hidden).toBe(false);
    expect($('map-resort-guide-finish-title').textContent).toBe('Keep Verbier one tap away');
    const link = document.querySelector('[data-resort-guide-signin]');
    expect(link.hidden).toBe(false);
    expect(link.textContent).toBe('Continue with email to pin Verbier');
    expect(link.getAttribute('href')).toBe('/accounts/sign-in/?next=%2Fmap%2F%3Fresort%3Dverbier&intent=pin');
    expect(emitted('map.resort_guide.finished')).toHaveLength(1);

    $('map-resort-guide-finish').querySelector('[data-resort-guide-done]').click();
    expect($('map-resort-guide-finish').hidden).toBe(true);
  });

  it('signed in, the pin carries the resort for favourites.js and then says it is pinned', async () => {
    buildFixture({ signedIn: true });
    await load();
    await walkToRegion();
    bar().querySelector('[data-resort-guide-finish]').click();

    const pin = document.querySelector('[data-resort-guide-pin]');
    expect(pin.hidden).toBe(false);
    expect(pin.textContent).toBe('Pin Verbier');
    expect(pin.dataset.resortSlug).toBe('verbier');
    expect(pin.dataset.resortLat).toBe('46.1');
    expect(pin.dataset.favourited).toBe('false');

    pin.click();
    vi.advanceTimersByTime(0);
    expect(pin.disabled).toBe(true);
    expect(pin.textContent).toBe('Verbier is pinned');
    expect(emitted('map.resort_guide.pinned')).toHaveLength(1);
  });

  it('claims no pin on a device that cannot queue the save', async () => {
    buildFixture({ signedIn: true });
    window.MapSheet.canQueueMutations = () => false;
    await load();
    await walkToRegion();
    bar().querySelector('[data-resort-guide-finish]').click();

    const pin = document.querySelector('[data-resort-guide-pin]');
    pin.click();
    vi.advanceTimersByTime(0);

    expect(pin.disabled).toBe(false);
    expect(pin.textContent).toBe('Pin Verbier');
    expect(emitted('map.resort_guide.pinned')).toHaveLength(0);
  });

  it('a resort already pinned offers no pin', async () => {
    buildFixture({ signedIn: true });
    await load();
    start();
    openResortSheet({ favourited: true });
    await settle();
    openRegionSheet();
    await settle();

    bar().querySelector('[data-resort-guide-finish]').click();

    expect(document.querySelector('[data-resort-guide-pin]').hidden).toBe(true);
    expect($('map-resort-guide-finish-title').textContent).toBe('Verbier is pinned');
  });
});
