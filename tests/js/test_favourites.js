/*
 * tests/js/test_favourites.js — Vitest unit tests for the card static/js/
 * favourites.js builds for the popup anchored to a saved pin (SNOW-658).
 *
 * Hugo's brief, with the two pin popups side by side: "No need to have the
 * editable name / remove in this dialogue - now it's in the favourites
 * overlay. Add the timestamp as the subheader. Same as the observations."
 * So the card is the pin's name, a muted relative time, and the popup's own
 * close — and the tests below assert both what is there and what is not,
 * since the whole point of the change is the two controls that left.
 *
 * map.js owns the anchoring: it dispatches snowdesk:favourite-selected with
 * an empty [data-favourite-detail] container, this module fills it, and map.js
 * mounts it (that half is covered by tests/js/test_map_detail_popup_
 * exclusivity.js). Driving the event by hand here needs no map in the loop.
 *
 * map_shared.js is evaluated through loadMapBundle rather than stubbed: it
 * publishes window.snowdeskMapFormat.relativeTime, and the point of reading
 * the formatter off that channel is that the favourite and observation popups
 * age their timestamps through ONE implementation — a stub would assert the
 * wiring while hiding a drift in the string.
 *
 * The module is an IIFE that returns early unless #favourite-add-btn,
 * #favourite-sheet and #favourite-create-template are all present, and it
 * captures those three elements once at load, so the fixture is built before
 * the dynamic import below and no test replaces it.
 *
 * SNOW-608: favourites.js now attaches its sheet through the shared
 * window.MapSheet, so map_sheet.js is imported first — home.html loads it
 * ahead of both surface partials for the same reason. Its own behaviour is
 * covered in tests/js/test_map_sheet.js.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';
import { loadMapBundle } from './_load_map_bundle.js';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="/favourites/create/"
          data-favourite-rename-url-template="/favourites/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/__UUID__/delete/"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-create-template"></template>
`;

// Publishes window.snowdeskMapFormat; nothing else in it runs at parse time.
loadMapBundle(['map_shared.js']);

// window.pwaRelativeTime — the words snowdeskMapFormat.relativeTime returns.
// home.html loads it alongside the map bundle; without it the popup renders
// no subheader at all, which is the guard, not the behaviour under test.
await import('../../static/js/relative_time.js');

await import('../../static/js/favourites.js');

/**
 * Open the pin popup for a favourite and return the container map.js would
 * have anchored — empty if favourites.js declined to fill it.
 *
 * @param {object} detail Event detail fields (uuid, name, created_at).
 * @returns {HTMLDivElement}
 */
function selectFavourite(detail) {
  const container = document.createElement('div');
  container.setAttribute('data-favourite-detail', '');
  document.dispatchEvent(
    new CustomEvent('snowdesk:favourite-selected', {
      detail: { container, ...detail },
    }),
  );
  return container;
}

/** An ISO timestamp the given number of minutes before now. */
function minutesAgo(minutes) {
  return new Date(Date.now() - minutes * 60000).toISOString();
}

describe('the saved-pin popup card', () => {
  beforeEach(() => {
    delete window.PlacePicker;
  });

  it('shows the pin name as its title', () => {
    const container = selectFavourite({
      uuid: 'f-1',
      name: 'Col des Gentianes',
      created_at: minutesAgo(5),
    });

    expect(container.textContent).toContain('Col des Gentianes');
  });

  it('shows the saved time as a relative subheader', () => {
    const container = selectFavourite({
      uuid: 'f-1',
      name: 'Verbier',
      created_at: minutesAgo(300),
    });

    // The same string map.js's observation popup and the field-observation
    // panel row both render: map_shared.js's formatRelativeTime delegates to
    // relative_time.js, so there is one wording for an age across the app.
    expect(container.textContent).toContain('5 hours ago');
  });

  it('renders the name as text, never as markup', () => {
    const container = selectFavourite({
      uuid: 'f-1',
      name: '<img src=x onerror="alert(1)">',
      created_at: minutesAgo(1),
    });

    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it('falls back to a placeholder title for an unnamed pin', () => {
    const container = selectFavourite({
      uuid: 'f-1',
      name: '',
      created_at: minutesAgo(1),
    });

    expect(container.textContent).toContain('Unnamed pin');
  });

  it('offers no rename field and no remove control', () => {
    const container = selectFavourite({
      uuid: 'f-1',
      name: 'Verbier',
      created_at: minutesAgo(1),
    });

    expect(container.querySelector('input')).toBeNull();
    expect(container.querySelector('form')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
    expect(container.textContent).not.toContain('Remove');
  });

  it('omits the subheader rather than showing a broken time', () => {
    const container = selectFavourite({ uuid: 'f-1', name: 'Verbier' });

    expect(container.textContent.trim()).toBe('Verbier');
  });

  it('fills nothing when the event carries no uuid', () => {
    const container = selectFavourite({ name: 'Verbier', created_at: minutesAgo(1) });

    // map.js reads the container back and mounts no popup when it is empty.
    expect(container.childNodes).toHaveLength(0);
  });
});
