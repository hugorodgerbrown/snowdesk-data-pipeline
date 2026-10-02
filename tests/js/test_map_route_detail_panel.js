/*
 * tests/js/test_map_route_detail_panel.js — the route detail sheet
 * (SNOW-973, static/js/map_route_detail.js).
 *
 * The module's other half — that the rail's "Terrain" item opens this sheet
 * at all, that it joins the exclusivity registry, and that the terrain
 * lines map.js builds arrive in it — is covered in
 * test_map_detail_popup_exclusivity.js, which boots the whole map bundle.
 * THIS file skips the map and drives ``window.pwaRouteDetail.open()``
 * directly.
 *
 * SNOW-1062 removed the day's bulletin reading from the sheet. What is left
 * to prove is that the sheet seats what map.js built, and that it asks the
 * network for nothing — on open or when the map's day moves — because
 * nothing it shows depends on the date.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const DAY = '2026-03-02';

document.body.innerHTML = `
  <div id="map"></div>
  <div id="route-detail-sheet" hidden tabindex="-1" data-overlay></div>
  <template id="route-detail-template">
    <div>
      <div data-route-detail-figures></div>
    </div>
  </template>`;

await import('../../static/js/map_sheet.js');
await import('../../static/js/map_route_detail.js');

/** The terrain lines map.js would have built. */
function figures() {
  const node = document.createElement('div');
  node.setAttribute('data-route-detail', '');
  node.textContent = 'Steepest 43°';
  return node;
}

/** Move the map's day, exactly as map_scrubber.js commits one. */
function changeDateTo(date) {
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
    detail: { date: date, source: 'scrubber' },
  }));
}

beforeEach(() => {
  window.pwaRouteDetail.close();
  vi.unstubAllGlobals();
  vi.stubGlobal('fetch', vi.fn());
});

describe('opening the panel', () => {
  it('opens and seats the node map.js built', () => {
    const node = figures();

    expect(window.pwaRouteDetail.open({ node: node })).toBe(true);

    expect(window.pwaRouteDetail.isOpen()).toBe(true);
    expect(document.querySelector('[data-route-detail-figures]').firstChild).toBe(node);
  });

  it('refuses an open with nothing to show', () => {
    expect(window.pwaRouteDetail.open({})).toBe(false);
    expect(window.pwaRouteDetail.isOpen()).toBe(false);
  });

  it('rebuilds the body on every open', () => {
    window.pwaRouteDetail.open({ node: figures() });
    window.pwaRouteDetail.close();
    const second = figures();
    second.textContent = 'Steepest 38°';

    window.pwaRouteDetail.open({ node: second });

    const seated = document.querySelectorAll('[data-route-detail]');
    expect(seated).toHaveLength(1);
    expect(seated[0]).toBe(second);
  });

  it('asks the network for nothing', () => {
    // SNOW-1062: the bulletin reading and its fetch are gone.
    window.pwaRouteDetail.open({ node: figures() });

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe('when the map\'s day moves under an open panel', () => {
  it('stays open and leaves the terrain lines where they are', () => {
    // The scrubber lives inside #map, which map_sheet.js excludes from
    // click-outside dismissal, so the sheet stays open across a date
    // change — and nothing in it is a function of the date.
    const node = figures();
    window.pwaRouteDetail.open({ node: node });

    changeDateTo(DAY);

    expect(window.pwaRouteDetail.isOpen()).toBe(true);
    expect(document.querySelector('[data-route-detail]')).toBe(node);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
