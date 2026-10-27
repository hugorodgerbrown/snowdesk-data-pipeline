/*
 * tests/js/test_map_example_guide.js — Vitest unit tests for
 * static/js/map_example_guide.js (SNOW-1230).
 *
 * The walkthrough over the example route. These cases pin the behaviour
 * Hugo asked for: it starts only from the example link and strips that
 * parameter; it says what happened to the date before anything else; each
 * doing step moves on by itself; nothing is locked — closing the route or
 * changing the day folds it into the paused pill rather than fighting the
 * visitor; and leaving always puts the map back on today and says so.
 *
 * The rail, the route opener and the map are stubs; the pure core and the
 * strings reader are the real modules.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const TOKEN = 'ExAmPlE123';
const DAY = '2026-04-10';
const TODAY = '2026-10-25';

function buildFixture() {
  document.body.innerHTML = `
    <input type="hidden" name="csrfmiddlewaretoken" value="csrf-1">
    <div id="season-scrubber" data-today="${TODAY}"></div>
    <div id="map" data-route-share-dismiss-url-template="/routes/s/__TOKEN__/dismiss/">
      <span id="map-example-day-badge" hidden>Example day</span>
      <div id="map-example-guide" hidden>
        <p id="map-example-guide-eyebrow"></p>
        <button type="button" data-example-exit>Exit</button>
        <div data-example-step="intro"><strong data-example-day></strong>
          <button type="button" data-example-next>Got it</button></div>
        <div data-example-step="drag" hidden><strong data-example-name></strong>
          <button type="button" data-example-skip hidden>Next</button></div>
        <div data-example-step="zoom" hidden><span data-example-zoom-fill></span>
          <button type="button" data-example-show-me>Show me</button></div>
        <div data-example-step="colours" hidden>
          <button type="button" data-example-keep>Keep exploring this example</button>
          <button type="button" data-example-finish>Back to today</button></div>
        <ol class="map-example-guide-dots"><li></li><li></li><li></li><li></li></ol>
      </div>
      <div id="map-example-pill" hidden>
        <span data-example-pill="paused">paused</span>
        <span data-example-pill="off-day" hidden>off day</span>
        <span data-example-pill="exploring" hidden>Example route · <span data-example-short-day></span></span>
        <button type="button" data-example-resume>Resume</button>
        <button type="button" data-example-back-to-today hidden>Back to today</button>
        <button type="button" data-example-back-to-day hidden></button>
        <button type="button" data-example-exit>Exit</button>
      </div>
      <div id="map-example-finish" hidden>
        <button type="button" data-example-resort>Find a resort</button>
        <button type="button" data-example-done>Done</button>
      </div>
      <div id="map-example-toast" class="hidden"><p data-toast-body></p></div>
    </div>
    <div id="route-rail"><h2 data-route-rail-name>Mont Fort – Col de la Chaux</h2>
      <div data-route-rail-lane></div></div>
  `;
}

const $ = (id) => document.getElementById(id);
const guide = () => $('map-example-guide');
const pill = () => $('map-example-pill');
const visibleStep = () => guide().querySelector('[data-example-step]:not([hidden])')
  ?.getAttribute('data-example-step');

let rail;
let cursorListener;
let zoomListener;
let zoom;
let added = [];
const realAdd = {
  document: document.addEventListener.bind(document),
  window: window.addEventListener.bind(window),
};

let cursor;
let cursorIndex;

/** Build the stubs the guide reads from the page's other modules. */
function stubs() {
  cursorIndex = null;
  cursor = {
    state: () => ({ index: cursorIndex }),
    setIndex: (index) => {
      cursorIndex = index;
      if (cursorListener) cursorListener({ index });
    },
    subscribe: (fn) => {
      cursorListener = fn;
      return () => { cursorListener = null; };
    },
  };
  rail = {
    element: $('route-rail'),
    open: false,
    openToken: null,
    isOpen: () => rail.open,
    isSheet: () => false,
    token: () => (rail.open ? rail.openToken : null),
    sampleCount: () => 101,
    cursor: () => cursor,
    close: vi.fn(() => { rail.open = false; railChanged(); }),
  };
  window.pwaRouteRail = rail;
  window.pwaRouteOpener = {
    openShare: vi.fn(),
    frame: vi.fn(),
    slopeAngles: () => [10, 20, 42, 30],
    showPoint: vi.fn((index) => { cursor.setIndex(index); return true; }),
  };
  zoom = 11;
  window.snowdeskMapState = {
    map: {
      getZoom: () => zoom,
      on: (name, fn) => { if (name === 'zoom') zoomListener = fn; },
    },
  };
  window.pwaTelemetry = { emit: vi.fn() };
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true }));
}

function railChanged() {
  document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed'));
}

function openExample(token = TOKEN) {
  rail.open = true;
  rail.openToken = token;
  railChanged();
}

async function load(search = `?route_share=${TOKEN}&example=1&d=${DAY}`) {
  history.replaceState(null, '', `/map/${search}`);
  vi.resetModules();
  delete window.pwaExampleGuideCore;
  delete window.pwaStrings;
  await import('../../static/js/i18n_strings.js');
  await import('../../static/js/example_guide_core.js');
  document.addEventListener = (...args) => {
    added.push([document, args]);
    realAdd.document(...args);
  };
  window.addEventListener = (...args) => {
    added.push([window, args]);
    realAdd.window(...args);
  };
  await import('../../static/js/map_example_guide.js');
  document.addEventListener = realAdd.document;
  window.addEventListener = realAdd.window;
}

const emitted = (name) => window.pwaTelemetry.emit.mock.calls.filter(([n]) => n === name);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  localStorage.clear();
  buildFixture();
  stubs();
});

afterEach(() => {
  added.forEach(([target, args]) => target.removeEventListener(...args));
  added = [];
  vi.useRealTimers();
  history.replaceState(null, '', '/');
});

describe('starting', () => {
  it('does nothing without the example parameter', async () => {
    await load(`?route_share=${TOKEN}`);
    openExample();

    expect(guide().hidden).toBe(true);
    expect(emitted('map.example.opened')).toHaveLength(0);
  });

  it('strips the parameter at once, keeping the rest', async () => {
    await load();

    expect(location.search).toBe(`?route_share=${TOKEN}&d=${DAY}`);
  });

  it('waits for the example route to open, then says what happened to the date', async () => {
    await load();
    expect(guide().hidden).toBe(true);

    openExample();

    expect(guide().hidden).toBe(false);
    expect(visibleStep()).toBe('intro');
    expect(guide().querySelector('[data-example-day]').textContent).toMatch(/2026/);
    expect($('map-example-guide-eyebrow').textContent).toBe('Example route · 1 of 4');
    expect($('map-example-day-badge').hidden).toBe(false);
    expect(window.pwaRouteOpener.frame).toHaveBeenCalledWith(TOKEN);
    expect(emitted('map.example.opened')).toHaveLength(1);
  });

  it('ignores another route opening', async () => {
    await load();

    openExample('someone-elses');

    expect(guide().hidden).toBe(true);
  });
});

describe('the steps', () => {
  it('moves from Got it to the drag step, naming the route', async () => {
    await load();
    openExample();

    guide().querySelector('[data-example-next]').click();
    vi.advanceTimersByTime(0);

    expect(visibleStep()).toBe('drag');
    expect(guide().querySelector('[data-example-name]').textContent).toBe('Mont Fort – Col de la Chaux');
  });

  it('a drag across a fifth of the route completes the drag step; a tap does not', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-next]').click();
    vi.advanceTimersByTime(0);

    cursorListener({ index: 10 });
    vi.advanceTimersByTime(1000);
    expect(visibleStep()).toBe('drag');

    cursorListener({ index: 35 });
    vi.advanceTimersByTime(1000);
    expect(visibleStep()).toBe('zoom');
    expect(emitted('map.example.step_completed').map(([, p]) => p.step)).toEqual(['intro', 'drag']);
  });

  it('zooming in to the slope zoom completes the zoom step', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-next]').click();
    vi.advanceTimersByTime(0);
    cursorListener({ index: 0 });
    cursorListener({ index: 50 });
    vi.advanceTimersByTime(1000);

    zoom = 12.5;
    zoomListener();
    expect(guide().querySelector('[data-example-zoom-fill]').style.width).toBe('50%');

    zoom = 14;
    zoomListener();
    vi.advanceTimersByTime(1000);
    expect(visibleStep()).toBe('colours');
  });
});

describe('one gesture at a time (SNOW-1244)', () => {
  /** Load, open the example and pass Got it. */
  async function toDrag() {
    await load();
    openExample();
    guide().querySelector('[data-example-next]').click();
    vi.advanceTimersByTime(0);
  }

  it('the drag step starts with the point placed and the profile ringed', async () => {
    await toDrag();

    expect(window.pwaRouteOpener.showPoint).toHaveBeenCalledWith(10);
    expect(cursorIndex).toBe(10);
    expect(document.querySelector('[data-route-rail-lane]').hasAttribute('data-example-ring')).toBe(true);
  });

  it('leaves a point the visitor already placed where it is', async () => {
    cursorIndex = 60;
    await toDrag();

    expect(window.pwaRouteOpener.showPoint).not.toHaveBeenCalled();
  });

  it('offers Next after a while, which moves on', async () => {
    await toDrag();
    const skip = guide().querySelector('[data-example-skip]');
    expect(skip.hidden).toBe(true);

    vi.advanceTimersByTime(8000);
    expect(skip.hidden).toBe(false);

    skip.click();
    vi.advanceTimersByTime(1000);
    expect(visibleStep()).toBe('zoom');
    expect(document.querySelector('[data-route-rail-lane]').hasAttribute('data-example-ring')).toBe(false);
  });

  it('Show me flies to the steepest stretch past the slope zoom', async () => {
    await toDrag();
    guide().querySelector('[data-example-skip]').hidden = false;
    guide().querySelector('[data-example-skip]').click();
    vi.advanceTimersByTime(1000);

    guide().querySelector('[data-example-show-me]').click();

    expect(window.pwaRouteOpener.showPoint).toHaveBeenLastCalledWith(2, 14.5);
    expect(emitted('map.example.show_me')).toHaveLength(1);
  });
});

describe('keep exploring (SNOW-1244)', () => {
  it('ends the guide but keeps the route, the day and a way back', async () => {
    await load();
    openExample();
    const scrub = vi.fn();
    document.addEventListener('snowdesk:scrub-to', scrub);

    guide().querySelector('[data-example-keep]').click();

    expect(guide().hidden).toBe(true);
    expect(pill().hidden).toBe(false);
    expect(pill().querySelector('[data-example-pill="exploring"]').hidden).toBe(false);
    expect(pill().querySelector('[data-example-pill="exploring"]').textContent).toMatch(/Apr/);
    expect(pill().querySelector('[data-example-pill="paused"]').hidden).toBe(true);
    expect(pill().querySelector('[data-example-resume]').hidden).toBe(true);
    expect(pill().querySelector('[data-example-exit]').hidden).toBe(true);
    expect(pill().querySelector('[data-example-back-to-today]').hidden).toBe(false);
    expect($('map-example-day-badge').hidden).toBe(false);
    expect(rail.close).not.toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(scrub).not.toHaveBeenCalled();
    expect(emitted('map.example.finished')[0][1]).toEqual({ kept: true });
    document.removeEventListener('snowdesk:scrub-to', scrub);
  });

  it('the pill stays when the route is closed', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-keep]').click();

    rail.open = false;
    railChanged();

    expect(pill().hidden).toBe(false);
    expect(pill().querySelector('[data-example-back-to-today]').hidden).toBe(false);
  });

  it('Back to today from the pill leaves and offers the finish card', async () => {
    await load();
    openExample();
    const scrub = vi.fn();
    document.addEventListener('snowdesk:scrub-to', scrub);
    guide().querySelector('[data-example-keep]').click();

    pill().querySelector('[data-example-back-to-today]').click();

    expect(pill().hidden).toBe(true);
    expect(rail.close).toHaveBeenCalled();
    expect(scrub.mock.calls[0][0].detail.date).toBe(TODAY);
    expect($('map-example-finish').hidden).toBe(false);
    expect(emitted('map.example.left')).toHaveLength(1);
    document.removeEventListener('snowdesk:scrub-to', scrub);
  });
});

describe('nothing is locked', () => {
  it('closing the route folds the guide into the paused pill; Resume reopens it', async () => {
    await load();
    openExample();

    rail.open = false;
    railChanged();

    expect(guide().hidden).toBe(true);
    expect(pill().hidden).toBe(false);
    expect(pill().querySelector('[data-example-pill="paused"]').hidden).toBe(false);

    pill().querySelector('[data-example-resume]').click();
    expect(window.pwaRouteOpener.openShare).toHaveBeenCalledWith(TOKEN, { frame: true });
  });

  it('another day takes the badge off and offers the way back', async () => {
    await load();
    openExample();
    const scrub = vi.fn();
    document.addEventListener('snowdesk:scrub-to', scrub);

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: TODAY } }));

    expect(guide().hidden).toBe(true);
    expect(pill().querySelector('[data-example-pill="off-day"]').hidden).toBe(false);
    expect($('map-example-day-badge').hidden).toBe(true);
    const back = pill().querySelector('[data-example-back-to-day]');
    expect(back.hidden).toBe(false);

    back.click();
    expect(scrub.mock.calls[0][0].detail.date).toBe(DAY);
    document.removeEventListener('snowdesk:scrub-to', scrub);
  });
});

describe('leaving', () => {
  it('Exit drops the example, closes the route, returns to today and says so', async () => {
    await load();
    openExample();
    const scrub = vi.fn();
    document.addEventListener('snowdesk:scrub-to', scrub);

    guide().querySelector('[data-example-exit]').click();

    expect(globalThis.fetch).toHaveBeenCalledWith(`/routes/s/${TOKEN}/dismiss/`, expect.objectContaining({
      method: 'POST',
      headers: { 'X-CSRFToken': 'csrf-1' },
    }));
    expect(rail.close).toHaveBeenCalled();
    expect(scrub.mock.calls[0][0].detail.date).toBe(TODAY);
    expect($('map-example-toast').classList.contains('hidden')).toBe(false);
    expect($('map-example-toast').textContent).toMatch(/^Back to today · /);
    expect(guide().hidden).toBe(true);
    expect(pill().hidden).toBe(true);
    expect(emitted('map.example.exited')[0][1]).toEqual({ step: 'intro' });
    expect(localStorage.getItem('snowdesk.example.walked')).toBe('yes');
    document.removeEventListener('snowdesk:scrub-to', scrub);
  });

  it('Finish shows the finish card; Done puts it away', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-next]').click();
    vi.advanceTimersByTime(0);

    guide().querySelector('[data-example-finish]').click();

    expect($('map-example-finish').hidden).toBe(false);
    expect(emitted('map.example.finished')).toHaveLength(1);

    $('map-example-finish').querySelector('[data-example-done]').click();
    expect($('map-example-finish').hidden).toBe(true);
  });

  it('the finish card\'s "Find a resort" starts the resort walkthrough', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-finish]').click();
    const requested = vi.fn();
    document.addEventListener('snowdesk:resort-guide-requested', requested);

    $('map-example-finish').querySelector('[data-example-resort]').click();

    expect(requested).toHaveBeenCalledTimes(1);
    expect($('map-example-finish').hidden).toBe(true);
    document.removeEventListener('snowdesk:resort-guide-requested', requested);
  });

  it('once ended, the route reopening starts nothing', async () => {
    await load();
    openExample();
    guide().querySelector('[data-example-exit]').click();

    openExample();

    expect(guide().hidden).toBe(true);
    expect(pill().hidden).toBe(true);
  });
});
