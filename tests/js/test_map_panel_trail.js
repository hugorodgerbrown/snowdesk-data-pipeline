/*
 * tests/js/test_map_panel_trail.js — moving between the map's region,
 * resort and weather panels, and back (static/js/map_panel_trail.js,
 * Hugo, 25 Oct 2026).
 *
 * A place row in one panel opens the other place's panel through that
 * kind's own door (an event, or pwaWeatherDetail.open) rather than leaving
 * the map; the panel it opens shows a back pill naming where the reader
 * came from; the pill opens that panel again and is spent; and the trail
 * ends once the panel it led to shows another place or closes. A modified
 * click is left to the link.
 *
 * The three sheets are plain elements here: the module only reads each
 * one's `hidden` attribute and the `[data-panel-key]` body inside it, which
 * is all the real sheets expose to it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

document.body.innerHTML = `
  <div id="region-sheet" hidden></div>
  <div id="place-sheet" hidden></div>
  <div id="weather-sheet" hidden></div>
  <div id="favourite-sheet"></div>
  <template id="panel-trail-back-template">
    <button type="button" data-panel-back data-testid="panel-back">
      <span data-panel-back-name></span>
    </button>
  </template>
  <template id="panel-trail-strings-template">
    <span data-string="back">Back to {name}</span>
  </template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/map_panel_trail.js');

const region = /** @type {HTMLElement} */ (document.getElementById('region-sheet'));
const place = /** @type {HTMLElement} */ (document.getElementById('place-sheet'));
const weather = /** @type {HTMLElement} */ (document.getElementById('weather-sheet'));

/** Let the module's MutationObserver run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * Paint a panel into a sheet and show it, hiding the others — what the
 * one-object-sheet rule does when a sheet opens.
 *
 * @param {HTMLElement} sheet
 * @param {string} key
 * @param {string} label
 * @param {string} [rows] Markup under the peek.
 */
function show(sheet, key, label, rows = '') {
  [region, place, weather].forEach((el) => el.setAttribute('hidden', ''));
  sheet.innerHTML = `
    <div data-panel-key="${key}" data-panel-label="${label}">
      <div data-sheet-peek><h2>${label}</h2></div>
      ${rows}
    </div>`;
  sheet.removeAttribute('hidden');
}

const REGION_ROW = `<a href="/ch-4115/martigny-verbier/" data-panel-open="region"
  data-panel-id="CH-4115" data-testid="resort-region-link">Martigny-Verbier</a>`;
const WEATHER_ROW = `<a href="/weather/ab12/" data-panel-open="weather"
  data-panel-id="ab12">Read the full 7-day forecast →</a>`;

/**
 * Click an element as a plain left click, and report whether it was taken.
 *
 * @param {Element} el
 * @param {MouseEventInit} [init]
 * @returns {MouseEvent}
 */
function click(el, init = {}) {
  const ev = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init });
  el.dispatchEvent(ev);
  return ev;
}

/** @type {Array<[string, (ev: Event) => void]>} */
let listeners = [];
function listen(name) {
  const fn = vi.fn();
  document.addEventListener(name, fn);
  listeners.push([name, fn]);
  return fn;
}

beforeEach(() => {
  [region, place, weather].forEach((el) => {
    el.innerHTML = '';
    el.setAttribute('hidden', '');
  });
});

afterEach(() => {
  listeners.forEach(([name, fn]) => document.removeEventListener(name, fn));
  listeners = [];
  delete window.pwaWeatherDetail;
});

describe('a place row', () => {
  it("opens the resort's region on the map rather than navigating", async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    const asked = listen('snowdesk:region-open');
    const ev = click(place.querySelector('[data-panel-open]'));
    expect(ev.defaultPrevented).toBe(true);
    expect(asked).toHaveBeenCalledTimes(1);
    expect(asked.mock.calls[0][0].detail).toEqual({
      region_id: 'CH-4115',
      href: new URL('/ch-4115/martigny-verbier/', document.baseURI).href,
    });
  });

  it("opens a weather point's panel on the map's day", async () => {
    const open = vi.fn();
    window.pwaWeatherDetail = { open };
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-03' } }));
    show(place, 'resort:veysonnaz', 'Veysonnaz', WEATHER_ROW);
    click(place.querySelector('[data-panel-open]'));
    expect(open).toHaveBeenCalledWith('ab12', '2026-02-03');
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: {} }));
  });

  it("follows a region row in the Favourites panel's Today list (SNOW-1359)", () => {
    const favourites = /** @type {HTMLElement} */ (document.getElementById('favourite-sheet'));
    favourites.innerHTML = REGION_ROW;
    const asked = listen('snowdesk:region-open');
    const ev = click(favourites.querySelector('[data-panel-open]'));
    expect(ev.defaultPrevented).toBe(true);
    expect(asked).toHaveBeenCalledTimes(1);
    expect(asked.mock.calls[0][0].detail.region_id).toBe('CH-4115');
    expect(region.querySelector('[data-panel-back]')).toBeNull();
    favourites.innerHTML = '';
  });

  it('leaves a modified click to the link, for a new tab', () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    const asked = listen('snowdesk:region-open');
    let leftToTheLink = false;
    const record = (ev) => {
      leftToTheLink = !ev.defaultPrevented;
      ev.preventDefault();
    };
    document.addEventListener('click', record);
    click(place.querySelector('[data-panel-open]'), { metaKey: true });
    document.removeEventListener('click', record);
    expect(leftToTheLink).toBe(true);
    expect(asked).not.toHaveBeenCalled();
  });

  it("only records the trail when the sheet's own handler opened the place", async () => {
    show(region, 'region:CH-4115', 'Martigny-Verbier', `<a href="/resorts/verbier/"
      data-panel-open="resort" data-panel-id="verbier" data-resort-slug="verbier">Verbier</a>`);
    const own = (ev) => ev.preventDefault();
    region.addEventListener('click', own);
    const asked = listen('snowdesk:resort-open');
    click(region.querySelector('[data-panel-open]'));
    region.removeEventListener('click', own);
    expect(asked).not.toHaveBeenCalled();

    show(place, 'resort:verbier', 'Verbier');
    await settle();
    expect(place.querySelector('[data-panel-back-name]').textContent).toBe('Martigny-Verbier');
  });
  it("still records the trail when the sheet's own handler repaints the sheet", async () => {
    // The real region sheet closes, and is repainted, inside its own click
    // handler as the resort sheet opens: by the bubble phase the row is in
    // no sheet. The trail is read before that, on the way down.
    show(region, 'region:CH-4115', 'Martigny-Verbier', `<a href="/resorts/nendaz/"
      data-panel-open="resort" data-panel-id="nendaz" data-resort-slug="nendaz">Nendaz</a>`);
    const own = (ev) => {
      ev.preventDefault();
      region.innerHTML = '';
      region.setAttribute('hidden', '');
    };
    region.addEventListener('click', own);
    click(region.querySelector('[data-panel-open]'));
    region.removeEventListener('click', own);

    show(place, 'resort:nendaz', 'Nendaz');
    await settle();
    expect(place.querySelector('[data-panel-back-name]').textContent).toBe('Martigny-Verbier');
  });
});

describe('the height the reader had', () => {
  it('is handed to the next panel by a row and by the back pill', async () => {
    const handOff = vi.fn();
    window.MapSheet = { handOff };
    try {
      show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
      click(place.querySelector('[data-panel-open]'));
      expect(handOff).toHaveBeenCalledTimes(1);
      show(region, 'region:CH-4115', 'Martigny-Verbier');
      await settle();
      click(region.querySelector('[data-panel-back]'));
      expect(handOff).toHaveBeenCalledTimes(2);
    } finally {
      delete window.MapSheet;
    }
  });
});

describe('a panel opening', () => {
  it('starts at the top when the sheet shows a new place', async () => {
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    region.scrollTop = 300;
    show(region, 'region:CH-4116', 'Conthey-Fully');
    await settle();
    expect(region.scrollTop).toBe(0);
  });

  it('starts at the top when the sheet opens again', async () => {
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    region.scrollTop = 300;
    region.setAttribute('hidden', '');
    await settle();
    region.removeAttribute('hidden');
    await settle();
    expect(region.scrollTop).toBe(0);
  });

  it("keeps the reader's place when the same place repaints", async () => {
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    region.scrollTop = 300;
    // A change of day repaints the body for the same region.
    region.querySelector('[data-panel-key]').innerHTML = '<p>the next day</p>';
    await settle();
    expect(region.scrollTop).toBe(300);
  });
});

describe('the back pill', () => {
  it('names the panel the reader came from, at the top of the new one', async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    click(place.querySelector('[data-panel-open]'));
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    const pill = region.querySelector('[data-panel-back]');
    expect(pill).not.toBeNull();
    expect(pill.parentElement.hasAttribute('data-sheet-peek')).toBe(true);
    expect(pill.parentElement.firstElementChild).toBe(pill);
    expect(pill.getAttribute('aria-label')).toBe('Back to Veysonnaz');
    expect(pill.querySelector('[data-panel-back-name]').textContent).toBe('Veysonnaz');
  });

  it('opens the panel it names again, and is spent', async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    click(place.querySelector('[data-panel-open]'));
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    const asked = listen('snowdesk:resort-open');
    click(region.querySelector('[data-panel-back]'));
    expect(asked).toHaveBeenCalledTimes(1);
    expect(asked.mock.calls[0][0].detail.slug).toBe('veysonnaz');
    expect(region.querySelector('[data-panel-back]')).toBeNull();

    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    await settle();
    expect(place.querySelector('[data-panel-back]')).toBeNull();
  });

  it('is not drawn before the panel it leads to has painted', async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    click(place.querySelector('[data-panel-open]'));
    // The region sheet opens on its blank peek first: no key yet.
    region.innerHTML = '<div data-sheet-peek><h2></h2></div>';
    region.removeAttribute('hidden');
    await settle();
    expect(region.querySelector('[data-panel-back]')).toBeNull();
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    expect(region.querySelector('[data-panel-back]')).not.toBeNull();
  });

  it('ends when the panel shows another place', async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    click(place.querySelector('[data-panel-open]'));
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    // A map tap on a neighbouring region.
    show(region, 'region:CH-4116', 'Conthey-Fully');
    await settle();
    expect(region.querySelector('[data-panel-back]')).toBeNull();
    // Coming back to the first region does not bring the old trail back.
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    expect(region.querySelector('[data-panel-back]')).toBeNull();
  });

  it('ends when the panel closes', async () => {
    show(place, 'resort:veysonnaz', 'Veysonnaz', REGION_ROW);
    click(place.querySelector('[data-panel-open]'));
    show(region, 'region:CH-4115', 'Martigny-Verbier');
    await settle();
    region.setAttribute('hidden', '');
    await settle();
    region.removeAttribute('hidden');
    await settle();
    expect(region.querySelector('[data-panel-back]')).toBeNull();
  });
});
