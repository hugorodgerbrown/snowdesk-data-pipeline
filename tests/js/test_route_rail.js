/*
 * tests/js/test_route_rail.js — rail one's DOM half
 * (static/js/route_rail.js, SNOW-1018).
 *
 * The assertion the ticket names: pressing a leg opens it on the route
 * cursor and pressing it again clears it, with `aria-pressed` following
 * the CURSOR rather than the click — so a leg closed from rail two's side
 * un-presses here too. Around it: one path per leg with its direction, an
 * unsampled route still cut into legs, a pending share without its
 * menu, the rail closing with the detail sheet, and Delete confirming
 * before it posts.
 *
 * The markup below is the hooks of templates/includes/_route_rail.html;
 * tests/public/test_route_rail.py holds the partial itself to them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_rail_core.js';

const UUID = '11111111-2222-3333-4444-555555555555';

document.body.innerHTML = `
  <div id="map">
    <section id="route-rail" data-route-rail hidden
             data-route-rename-url-template="/routes/partials/__UUID__/rename/"
             data-route-share-url-template="/routes/__UUID__/share/"
             data-route-delete-url-template="/routes/partials/__UUID__/delete/"
             data-route-plan-trip-url="/trips/new/">
      <div data-row-renameable>
        <span data-row-label data-route-rail-name></span>
        <input data-row-rename-input hidden>
        <div data-route-rail-actions>
          <div data-overflow-menu>
            <ul role="menu">
              <li><a role="menuitem" data-route-rail-plan-trip>Plan a trip</a></li>
              <li><button role="menuitem" data-route-rail-share>Share</button></li>
              <li><button role="menuitem" data-row-rename data-route-rename="">Rename</button></li>
              <li><button role="menuitem" data-route-rail-delete>Delete</button></li>
            </ul>
          </div>
        </div>
        <p data-route-rail-figures></p>
      </div>
      <svg data-route-rail-lane></svg>
      <div data-route-rail-ticks></div>
      <div data-route-rail-readout></div>
      <form data-route-rail-csrf hidden>
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      </form>
    </section>
  </div>
  <div id="route-detail-sheet"></div>
`;

await import('../../static/js/route_rail.js');

const rail = document.getElementById('route-rail');
const mapEl = document.getElementById('map');
const sheet = document.getElementById('route-detail-sheet');

/**
 * A 24-segment route: a climb over segments 0-11 and a descent over 12-23.
 *
 * @param {object} [overrides] Properties replacing the defaults.
 * @returns {object} A routes-GeoJSON feature.
 */
function feature(overrides = {}) {
  const coordinates = Array.from({ length: 81 }, (_, i) => [
    7.4 + i / 10000,
    46.1,
    i <= 40 ? 1500 + i * 5 : 1700 - (i - 40) * 5,
  ]);
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates },
    properties: {
      uuid: UUID,
      name: 'Mont Fort',
      distance_m: 620,
      ascent_m: 200,
      descent_m: 200,
      slope: { points: [], angles: new Array(24).fill(20) },
      legs: [
        { i: 1, from: 0, to: 11, climbing: true },
        { i: 2, from: 12, to: 23, climbing: false },
      ],
      ...overrides,
    },
  };
}

/** @returns {Array<SVGPathElement>} The leg fills currently drawn. */
function legPaths() {
  return Array.from(rail.querySelectorAll('.route-rail-leg'));
}

beforeEach(() => {
  sheet.removeAttribute('hidden');
});

afterEach(() => {
  window.pwaRouteRail.close();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('open', () => {
  it('shows the rail and lifts the map’s bottom chrome', () => {
    expect(window.pwaRouteRail.open(feature())).toBe(true);

    expect(rail.hidden).toBe(false);
    expect(mapEl.hasAttribute('data-route-rail-open')).toBe(true);
    expect(window.pwaRouteRail.isOpen()).toBe(true);
  });

  it('draws one path per leg, marked with its direction', () => {
    window.pwaRouteRail.open(feature());

    expect(legPaths().map((p) => p.getAttribute('data-climbing'))).toEqual([
      'true',
      'false',
    ]);
    expect(legPaths().map((p) => p.getAttribute('aria-label'))).toEqual([
      'Leg 1 — climb',
      'Leg 2 — descent',
    ]);
  });

  it('writes the name and the figures line', () => {
    window.pwaRouteRail.open(feature());

    expect(rail.querySelector('[data-route-rail-name]').textContent).toBe('Mont Fort');
    expect(rail.querySelector('[data-route-rail-figures]').textContent).toBe(
      '0.6 km · ▲200 m · ▼200 m · 1500→1500 m',
    );
  });

  it('labels its ticks in one unit', () => {
    window.pwaRouteRail.open(feature());

    const labels = Array.from(
      rail.querySelectorAll('[data-route-rail-ticks] span'),
    ).map((span) => span.textContent);
    expect(labels.length).toBeGreaterThan(1);
    expect(new Set(labels.map((label) => label.split(' ')[1])).size).toBe(1);
  });

  it('points the menu at the open route', () => {
    window.pwaRouteRail.open(feature());

    expect(rail.querySelector('[data-route-rail-plan-trip]').getAttribute('href')).toBe(
      `/trips/new/?route=${UUID}`,
    );
    expect(rail.querySelector('[data-route-rename]').getAttribute('data-route-rename')).toBe(
      UUID,
    );
    expect(rail.querySelector('[data-route-rail-actions]').hidden).toBe(false);
  });

  it('cuts a never-sampled route into legs, sizing the cursor from them', () => {
    window.pwaRouteRail.open(feature({ slope: undefined }));

    expect(legPaths()).toHaveLength(2);
    const cursor = window.pwaRouteRail.cursor();
    expect(cursor).not.toBeNull();
    cursor.setIndex(1000);
    expect(cursor.state().index).toBe(23);
  });

  it('cuts a pending share into legs, though its slope is not drawn', () => {
    window.pwaRouteRail.open(feature({ uuid: undefined, token: 'abc', pending: true }));

    expect(legPaths()).toHaveLength(2);
  });

  it('draws the outline alone for a route with no legs', () => {
    window.pwaRouteRail.open(feature({ slope: undefined, legs: undefined }));

    expect(legPaths()).toHaveLength(0);
    expect(rail.querySelectorAll('[data-route-rail-lane] path')).toHaveLength(1);
    expect(window.pwaRouteRail.cursor()).toBeNull();
  });

  it('hides the menu for a pending share, which has no uuid', () => {
    window.pwaRouteRail.open(feature({ uuid: undefined, token: 'abc', pending: true }));

    expect(rail.querySelector('[data-route-rail-actions]').hidden).toBe(true);
  });
});

describe('pressing a leg', () => {
  it('opens it on the cursor, and pressing it again clears it', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(window.pwaRouteRail.cursor().state().openLeg).toMatchObject({
      i: 1,
      from: 0,
      to: 11,
    });
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(rail.querySelector('[data-route-rail-readout]').textContent).toBe(
      'Leg 1 — climb',
    );

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(window.pwaRouteRail.cursor().state().openLeg).toBeNull();
    expect(first.getAttribute('aria-pressed')).toBe('false');
  });

  it('moves the open leg when another is pressed', () => {
    window.pwaRouteRail.open(feature());
    const [first, second] = legPaths();

    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    second.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(2);
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(second.getAttribute('aria-pressed')).toBe('true');
  });

  it('answers Enter from the keyboard', () => {
    window.pwaRouteRail.open(feature());
    const [, second] = legPaths();

    second.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    expect(window.pwaRouteRail.cursor().state().openLeg.i).toBe(2);
  });

  it('un-presses when the leg is closed from another surface', () => {
    window.pwaRouteRail.open(feature());
    const [first] = legPaths();
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    window.pwaRouteRail.cursor().closeLeg();

    expect(first.getAttribute('aria-pressed')).toBe('false');
  });
});

describe('lifetime', () => {
  it('closes when the route detail sheet closes', async () => {
    window.pwaRouteRail.open(feature());

    sheet.setAttribute('hidden', '');
    await Promise.resolve();

    expect(rail.hidden).toBe(true);
    expect(mapEl.hasAttribute('data-route-rail-open')).toBe(false);
    expect(window.pwaRouteRail.cursor()).toBeNull();
  });
});

describe('Delete', () => {
  it('posts nothing when the confirmation is declined', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    window.pwaRouteRail.open(feature());

    rail.querySelector('[data-route-rail-delete]').click();

    expect(window.confirm).toHaveBeenCalledWith(
      "Delete Mont Fort? You'll need the .gpx file again to put it back.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts, closes and announces once confirmed', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const changed = vi.fn();
    document.addEventListener('snowdesk:routes-changed', changed);
    window.pwaRouteRail.open(feature());

    rail.querySelector('[data-route-rail-delete]').click();
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/routes/partials/${UUID}/delete/`);
    expect(init.method).toBe('POST');
    expect(init.headers['X-CSRFToken']).toBe('tok');
    expect(rail.hidden).toBe(true);
    document.removeEventListener('snowdesk:routes-changed', changed);
  });
});
