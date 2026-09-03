/*
 * tests/js/test_favourites_region_pin.js — the region pin control (SNOW-802,
 * static/js/favourites.js).
 *
 * The control arrives inside the region tooltip that the region + date
 * panel and the region popup inject, so favourites.js handles it by
 * delegation. A tap POSTs to the toggle endpoint as a plain fetch — a
 * region pin has no coordinate, so nothing goes through the mutation
 * queue — and the response IS the control in its new state, swapped in
 * whole. The pins sheet is told its rows are stale.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_focus.js';
import '../../static/js/row_removed.js';
import '../../static/js/map_sheet.js';

const LIST_URL = '/favourites/partials/list/';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="/favourites/partials/create/"
          data-favourite-list-url="${LIST_URL}"
          data-favourite-rename-url-template="/favourites/partials/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/partials/__UUID__/delete/"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-list-template"><div><div data-favourites-rows></div></div></template>
  <template id="favourite-create-template">
    <form id="favourite-create-form">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
    </form>
  </template>
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class" class="hidden">
    <span data-toast-body></span>
  </div>
  <div id="tooltip"></div>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/favourites.js');

const PINNED = '<button data-region-pin data-region-id="CH-4115" data-region-pin-toggle-url="/favourites/partials/region/CH-4115/toggle/" data-pinned="true" aria-pressed="true">Pinned</button>';
const UNPINNED = '<button data-region-pin data-region-id="CH-4115" data-region-pin-toggle-url="/favourites/partials/region/CH-4115/toggle/" data-pinned="false" aria-pressed="false">Pin this region</button>';

/** Render the control as the tooltip would and return it. */
function mountControl(html = UNPINNED) {
  const tooltip = document.getElementById('tooltip');
  tooltip.innerHTML = html;
  return tooltip.querySelector('[data-region-pin]');
}

/** Wait for queued promise callbacks to run. */
async function settle() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

let changed;

beforeEach(() => {
  changed = vi.fn();
  document.addEventListener('snowdesk:favourites-changed', changed);
});

afterEach(() => {
  document.removeEventListener('snowdesk:favourites-changed', changed);
  vi.unstubAllGlobals();
  document.getElementById('tooltip').innerHTML = '';
});

describe('the region pin control', () => {
  it('POSTs to the toggle URL as an HTMX-headed fetch with the CSRF token', async () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true, text: () => Promise.resolve(PINNED) }));
    vi.stubGlobal('fetch', fetchSpy);
    mountControl().click();
    await settle();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('/favourites/partials/region/CH-4115/toggle/');
    expect(init.method).toBe('POST');
    expect(init.headers['HX-Request']).toBe('true');
    expect(init.headers['X-CSRFToken']).toBe('tok');
  });

  it('swaps the control for the response and announces the change', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, text: () => Promise.resolve(PINNED) })));
    mountControl().click();
    await settle();

    const next = document.querySelector('[data-region-pin]');
    expect(next.dataset.pinned).toBe('true');
    expect(next.textContent).toContain('Pinned');
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('ignores a second tap while the first is in flight', async () => {
    let resolveFetch;
    const fetchSpy = vi.fn(() => new Promise((resolve) => { resolveFetch = resolve; }));
    vi.stubGlobal('fetch', fetchSpy);
    const control = mountControl();
    control.click();
    control.click();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    resolveFetch({ ok: true, text: () => Promise.resolve(UNPINNED) });
    await settle();
  });

  it('leaves the control alone and toasts on a failed request', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, status: 500 })));
    const control = mountControl();
    control.click();
    await settle();

    expect(document.querySelector('[data-region-pin]')).toBe(control);
    expect(control.dataset.pinBusy).toBeUndefined();
    expect(changed).not.toHaveBeenCalled();
    expect(document.querySelector('[data-toast-body]').textContent).toContain("couldn't be pinned");
  });
});
