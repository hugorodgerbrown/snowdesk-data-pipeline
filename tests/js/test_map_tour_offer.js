/*
 * tests/js/test_map_tour_offer.js — Vitest unit tests for
 * static/js/map_tour_offer.js (SNOW-1117).
 *
 * The offer replaced home_intro.js and its welcome panel. These cases pin
 * what makes it an offer rather than an interruption: it is never on screen
 * at first paint, it waits for the map to settle, it is made once per
 * device, it stays away from deep links and from a screen something else
 * already holds, and only an answer — not being displaced — records it.
 *
 * The module reads #map-tour-offer at parse time and returns early without
 * it, so the fixture exists before each import; `vi.resetModules()` gives
 * every case a fresh IIFE run. Fake timers drive the settle wait.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const OFFER_KEY = 'snowdesk.map.tour-offer';
const RATINGS_WAIT_MS = 6000;
const SETTLE_MS = 1500;

function buildFixture() {
  document.body.innerHTML = `
    <div id="map">
      <div id="map-tour-offer" hidden>
        <div class="map-tour-offer-card">
          <button id="map-tour-offer-close" type="button">×</button>
          <button id="map-tour-offer-later" type="button">Not now</button>
          <button id="map-tour-offer-start" type="button">Start the tour</button>
        </div>
      </div>
      <div id="map-help-overlay" hidden></div>
    </div>
  `;
}

/**
 * Re-import the overlay registry and the module against the current
 * fixture, in the page's load order. The registry is deleted first because
 * it is frozen once assigned.
 */
async function loadModules() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  await import('../../static/js/map_tour_offer.js');
}

const offer = () => document.getElementById('map-tour-offer');
const isShown = () => !offer().hasAttribute('hidden');
const ratingsLoaded = () => document.dispatchEvent(
  new CustomEvent('snowdesk:country-ratings-loaded'),
);

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  history.replaceState(null, '', '/map/');
  document.documentElement.removeAttribute('data-map-sheet');
  buildFixture();
});

afterEach(() => {
  vi.useRealTimers();
  history.replaceState(null, '', '/');
});

describe('when it appears', () => {
  it('is not shown at first paint', async () => {
    await loadModules();

    expect(isShown()).toBe(false);
  });

  it('appears once the map has its colours and has settled', async () => {
    await loadModules();
    ratingsLoaded();

    vi.advanceTimersByTime(SETTLE_MS - 1);
    expect(isShown()).toBe(false);
    vi.advanceTimersByTime(1);
    expect(isShown()).toBe(true);
  });

  it('does not wait for the colours for ever', async () => {
    await loadModules();

    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(true);
  });

  it('is offered once: an answer recorded on this device keeps it away', async () => {
    localStorage.setItem(OFFER_KEY, 'dismissed');
    await loadModules();
    ratingsLoaded();
    vi.advanceTimersByTime(SETTLE_MS);

    expect(isShown()).toBe(false);
  });

  it('is not offered to someone who finished the tour', async () => {
    localStorage.setItem('snowdesk.map.help', 'seen');
    await loadModules();
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(false);
  });

  it('is not offered to someone who dismissed the welcome panel it replaced', async () => {
    localStorage.setItem('snowdesk.home.intro', 'dismissed');
    await loadModules();
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(false);
  });

  it.each(['route_share=abc', 'trip=1', 'resort=verbier', 'panel=routes'])(
    'is not offered on a deep link (?%s)',
    async (query) => {
      history.replaceState(null, '', `/map/?${query}`);
      await loadModules();
      vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

      expect(isShown()).toBe(false);
    },
  );

  it('is offered on a link that only names a day', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-01');
    await loadModules();
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(true);
  });

  it('stays away when a sheet holds the screen, and records nothing', async () => {
    await loadModules();
    document.documentElement.setAttribute('data-map-sheet', 'peek');
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(false);
    expect(localStorage.getItem(OFFER_KEY)).toBeNull();
  });

  it('stays away while a route is open', async () => {
    await loadModules();
    document.getElementById('map').setAttribute('data-route-rail-open', '');
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(false);
  });

  it('?tour-offer=1 shows it whatever was recorded', async () => {
    localStorage.setItem(OFFER_KEY, 'dismissed');
    history.replaceState(null, '', '/map/?tour-offer=1');
    await loadModules();
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);

    expect(isShown()).toBe(true);
  });
});

describe('answering it', () => {
  async function showOffer() {
    await loadModules();
    vi.advanceTimersByTime(RATINGS_WAIT_MS + SETTLE_MS);
    expect(isShown()).toBe(true);
  }

  it('"Start the tour" records the answer and asks for the tour', async () => {
    await showOffer();
    const requested = vi.fn();
    document.addEventListener('snowdesk:map-help-requested', requested);

    document.getElementById('map-tour-offer-start').click();

    expect(isShown()).toBe(false);
    expect(localStorage.getItem(OFFER_KEY)).toBe('dismissed');
    expect(requested).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:map-help-requested', requested);
  });

  it.each(['map-tour-offer-later', 'map-tour-offer-close'])(
    '#%s records the answer without the tour',
    async (id) => {
      await showOffer();
      const requested = vi.fn();
      document.addEventListener('snowdesk:map-help-requested', requested);

      document.getElementById(id).click();

      expect(isShown()).toBe(false);
      expect(localStorage.getItem(OFFER_KEY)).toBe('dismissed');
      expect(requested).not.toHaveBeenCalled();
      document.removeEventListener('snowdesk:map-help-requested', requested);
    },
  );

  it('Escape records the answer', async () => {
    await showOffer();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(isShown()).toBe(false);
    expect(localStorage.getItem(OFFER_KEY)).toBe('dismissed');
  });

  it('strips ?tour-offer=1 once answered, keeping the rest of the query', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-01&tour-offer=1');
    await showOffer();

    document.getElementById('map-tour-offer-later').click();

    expect(location.search).toBe('?d=2026-02-01');
  });

  it('another overlay opening closes it without recording an answer', async () => {
    await showOffer();

    window.pwaMapOverlays.opening('basemap-menu');

    expect(isShown()).toBe(false);
    expect(localStorage.getItem(OFFER_KEY)).toBeNull();
  });
});
