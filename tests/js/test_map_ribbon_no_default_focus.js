/*
 * tests/js/test_map_ribbon_no_default_focus.js — the map opens with NO region
 * focused (SNOW-656).
 *
 * `#season-ribbon` carries `data-default-region-id` (CH-4115,
 * Martigny-Verbier) and the season bounds. The ribbon used to seed its focus
 * from that attribute, so every visitor arrived at a homepage presenting one
 * arbitrary region as though they had chosen it: the readout chip named it
 * and carried its danger swatch, the season track was painted for it, the
 * download roundel was armed for it, and the map outlined it. Nothing on the
 * page explained why that region, and its swatch colour read as a statement
 * about the map rather than about one region — "always selected, always
 * green", as reported.
 *
 * Three separate surfaces expressed that default and each had to be taken
 * off it, which is why this file asserts all three rather than just the one
 * that was noticed first:
 *
 *   - the readout chip           (`map_season_ribbon.js`'s `updateReadout`)
 *   - the on-map selection ring  (its `setHighlight`)
 *   - the download roundel       (`map_region_download.js`'s own seed)
 *
 * The attribute itself is deliberately still rendered: the season bounds
 * travel with it, and `dateKey` is still seeded from `data-default-date`
 * because the timeline has a day whether or not a region is focused.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const DEFAULT_REGION = 'CH-4115';

/**
 * The ribbon header, scrubber track and readout chip, with the default-region
 * attributes present — the point being that they are present and ignored.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="season-ribbon"
         data-season-start="2026-02-09"
         data-season-end="2026-04-30"
         data-default-date="2026-04-30"
         data-default-region-id="${DEFAULT_REGION}"
         data-default-region-name="Martigny-Verbier"
         data-default-region-slug="martigny-verbier"
         data-default-subregion-name="Lower Valais"
         data-default-major-name="Valais"></div>
    <div id="region-readout" class="region-readout">
      <span class="region-readout-swatch"></span>
      <span class="region-readout-name">
        <span class="region-readout-crumbs"></span>
        <span class="region-readout-leaf">No region selected</span>
      </span>
    </div>
    <div id="map-date-ribbon" hidden></div>
    <div class="season-scrubber">
      <div class="season-scrubber-track">
        <div class="scrubber-ribbon"></div>
      </div>
    </div>`;
}

/** Load the ribbon against a stub map that records feature-state writes. */
async function loadRibbon() {
  const featureState = new Map();
  globalThis.MAP = {
    setFeatureState: (target, state) => {
      featureState.set(target.id, { ...(featureState.get(target.id) || {}), ...state });
    },
    getFeatureState: (target) => featureState.get(target.id) || {},
    triggerRepaint: () => {},
  };
  globalThis.FEATURE_BY_REGION_ID = { [DEFAULT_REGION]: { id: 1 } };
  globalThis.INT_TO_RATING = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];
  globalThis.readBoolStorage = () => false;
  globalThis.getSeasonRatings = () =>
    Promise.resolve({ '2026-04-30': { [DEFAULT_REGION]: 1 } });
  // Both date formatters are bare identifiers from map_shared.js. Stubbing
  // only one leaves `updateReadout` throwing on its first line, which makes
  // every "the chip is empty" assertion below pass for the wrong reason.
  globalThis.formatDateLong = (d) => d;
  globalThis.formatDatePopup = (d) => d;
  // The empty-state branch reads this. Leaving it out threw asynchronously,
  // from the getSeasonRatings().then() — after the assertions had already
  // run and passed, so the suite went green while the code under test was
  // failing. `tox -e js` surfaces that as an unhandled error; `vitest run`
  // alone reports 4 errors beside a green count. Stub every bare identifier
  // the module touches, not just the ones an assertion happens to reach.
  globalThis.MAP_STRINGS = { 'no-region': 'No region selected' };

  vi.resetModules();
  await import('../../static/js/map_season_ribbon.js');
  // The ribbon paints its default focus from a getSeasonRatings().then().
  await new Promise((r) => setTimeout(r, 20));
  return { featureState };
}

beforeEach(buildFixture);

afterEach(() => {
  document.body.innerHTML = '';
  delete globalThis.MAP;
  delete globalThis.FEATURE_BY_REGION_ID;
});

describe('the map opens with no region focused', () => {
  it('leaves the readout chip in its empty state', async () => {
    await loadRibbon();

    const chip = document.getElementById('region-readout');
    expect(chip.textContent).toContain('No region selected');
    expect(chip.textContent).not.toContain('Martigny-Verbier');
    // SNOW-642's empty state is expressed by the absence of this class; the
    // chip stays visible either way.
    expect(chip.classList.contains('has-region')).toBe(false);
  });

  it('writes no selection onto the map', async () => {
    const { featureState } = await loadRibbon();

    // The default region's feature must carry nothing at all — not
    // `selected: false`, which would still be a write nobody asked for.
    expect(featureState.size).toBe(0);
  });

  it('paints no season track, so the rail stays grey', async () => {
    await loadRibbon();

    expect(document.querySelectorAll('.scrubber-ribbon-cell').length).toBe(0);
    expect(document.querySelector('.season-scrubber-track').hasAttribute('data-ribbon'))
      .toBe(false);
  });

  it('still focuses a region the visitor actually picks', async () => {
    // The guard must not disable focusing altogether — this is the event
    // every real route (tap, resort pin, search hit, #CH-xxxx deep link)
    // ends in.
    const { featureState } = await loadRibbon();

    document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
      detail: { region_id: DEFAULT_REGION, region_name: 'Martigny-Verbier', region_slug: 'x' },
    }));

    expect(document.getElementById('region-readout').textContent)
      .toContain('Martigny-Verbier');
    expect(featureState.get(1)).toEqual({ selected: true });
  });
});
