/*
 * tests/js/test_map_ribbon_no_date.js — the ribbon's readouts with no day
 * chosen (SNOW-660).
 *
 * The map no longer paints a day nobody asked for, which makes the two
 * readouts either side of the scrubber the only things that can explain an
 * uncoloured map. So both are pinned here:
 *
 *   - #map-date-ribbon says "No date selected" rather than hiding. Hiding
 *     was the obvious option and the wrong one: a blank choropleth beside a
 *     readout that has silently disappeared reads as a broken page, which is
 *     the same class of bug as the invented date this ticket removed.
 *   - #region-readout still names a region the visitor taps. Region and date
 *     are independent — the chip answers "which region", and making that
 *     answer wait on a date they have not given would report "No region
 *     selected" about a region they just tapped. Its swatch and its bulletin
 *     roundel DO wait, because a danger rating and a bulletin URL are both
 *     one region on one day.
 *
 * The sibling file test_map_ribbon_no_default_focus.js covers the same
 * surface with no REGION chosen (SNOW-656); this one is the date axis.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const REGION = 'CH-4115';
const DATED = '2026-04-30';

function buildFixture() {
  document.body.innerHTML = `
    <div id="season-ribbon"
         data-season-start="2026-02-09"
         data-season-end="2026-04-30"
         data-default-region-id="${REGION}"></div>
    <div id="region-readout" class="region-readout">
      <span class="region-readout-swatch"></span>
      <span class="region-readout-name">
        <span class="region-readout-crumbs"></span>
        <span class="region-readout-leaf">No region selected</span>
      </span>
    </div>
    <a id="region-readout-action" href="#"></a>
    <div id="map-date-ribbon" hidden></div>
    <div class="season-scrubber">
      <div class="season-scrubber-track">
        <div class="scrubber-ribbon"></div>
      </div>
    </div>`;
}

/** Load the ribbon at `url`, with every cross-file binding it reads stubbed. */
async function loadRibbon(url) {
  history.replaceState(null, '', url);
  globalThis.MAP = null;
  globalThis.FEATURE_BY_REGION_ID = { [REGION]: { id: 1 } };
  globalThis.INT_TO_RATING = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];
  globalThis.readBoolStorage = () => false;
  globalThis.readUrlDateParam = () => {
    const d = new URL(location.href).searchParams.get('d');
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  };
  globalThis.getSeasonRatings = () => Promise.resolve({ [DATED]: { [REGION]: 3 } });
  globalThis.formatDateLong = (d) => d;
  globalThis.formatDatePopup = (d) => `formatted:${d}`;
  globalThis.MAP_STRINGS = {
    'no-region': 'No region selected',
    'map-date-none': 'No date selected',
  };

  vi.resetModules();
  await import('../../static/js/map_season_ribbon.js');
  await new Promise((r) => setTimeout(r, 20));
}

/** Tap a region, the way every real selection route ends. */
function selectRegion() {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: REGION, region_name: 'Martigny-Verbier', region_slug: 'martigny-verbier' },
  }));
}

beforeEach(buildFixture);

afterEach(() => {
  document.body.innerHTML = '';
  history.replaceState(null, '', '/');
  delete globalThis.MAP;
  delete globalThis.FEATURE_BY_REGION_ID;
  delete globalThis.readUrlDateParam;
});

describe('#map-date-ribbon with no ?d=', () => {
  it('says "No date selected" instead of hiding', async () => {
    await loadRibbon('/');

    const ribbon = document.getElementById('map-date-ribbon');
    expect(ribbon.hidden).toBe(false);
    expect(ribbon.textContent).toBe('No date selected');
  });

  it('shows the date a ?d= asked for', async () => {
    await loadRibbon(`/?d=${DATED}`);

    expect(document.getElementById('map-date-ribbon').textContent)
      .toBe(`formatted:${DATED}`);
  });

  it('returns to the prompt when a commit unchooses the day', async () => {
    // The scrubber's popstate handler dispatches `date: null` on a back step
    // onto a dateless URL. The readout has to follow it back, not keep
    // naming the day the map has just stopped painting.
    await loadRibbon(`/?d=${DATED}`);

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: null, source: 'scrubber' },
    }));

    expect(document.getElementById('map-date-ribbon').textContent)
      .toBe('No date selected');
  });
});

describe('#region-readout with no ?d=', () => {
  it('names a region the visitor taps', async () => {
    await loadRibbon('/');

    selectRegion();

    expect(document.getElementById('region-readout').textContent)
      .toContain('Martigny-Verbier');
  });

  it('shows no danger swatch, having no day to read one for', async () => {
    await loadRibbon('/');

    selectRegion();

    expect(document.querySelector('.region-readout-swatch').style.background)
      .toBe('transparent');
  });

  it('leaves the bulletin roundel disabled, there being no dated bulletin', async () => {
    await loadRibbon('/');

    selectRegion();

    const action = document.getElementById('region-readout-action');
    expect(action.hasAttribute('href')).toBe(false);
    expect(action.getAttribute('aria-disabled')).toBe('true');
  });

  it('arms the roundel as soon as a day is chosen', async () => {
    // The disabled state must be the missing DATE, not a roundel that has
    // stopped working — so pin the transition, not just the resting state.
    await loadRibbon('/');
    selectRegion();

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: DATED, source: 'scrubber' },
    }));

    const action = document.getElementById('region-readout-action');
    expect(action.getAttribute('href'))
      .toBe(`/ch-4115/martigny-verbier/${DATED}/`);
    expect(action.hasAttribute('aria-disabled')).toBe(false);
  });
});
