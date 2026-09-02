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
 *
 *     SNOW-793 moved this state off the cold-boot path — a bare URL now
 *     means today, and the ribbon names it — but did not remove it. It is
 *     what a page with no readable `data-today` still falls back to, so it
 *     still has to read as a state rather than as a fault, and both halves
 *     are covered below.
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
// SNOW-793's default, and deliberately not DATED: a ribbon that showed the
// deep-linked day when it should show today, or vice versa, has to fail.
const TODAY = '2026-03-01';

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
  // SNOW-793: what the ribbon actually reads. map_shared.js owns the
  // precedence — `?d=` first, then the scrubber's `data-today` — so the
  // stub reproduces it rather than the ribbon growing its own copy. The
  // fixture below carries no `data-today`, which is why the default arm
  // reads null here; the cases that exercise the default set one.
  globalThis.readDisplayDate = () => {
    const el = document.getElementById('season-scrubber');
    const today = el && el.dataset.today;
    return globalThis.readUrlDateParam()
      || (today && /^\d{4}-\d{2}-\d{2}$/.test(today) ? today : null);
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

/**
 * Give the fixture's scrubber the server-rendered `data-today` a real page
 * carries, which is what `readDisplayDate` falls back to.
 *
 * The base fixture deliberately omits it: the "No date selected" state is
 * still a state the ribbon has to render, and leaving it as the default
 * keeps that arm honest rather than making every case opt out of today.
 */
function withScrubberToday(today) {
  const el = document.querySelector('.season-scrubber');
  el.id = 'season-scrubber';
  el.dataset.today = today;
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
  delete globalThis.readDisplayDate;
});

describe('#map-date-ribbon with no ?d=', () => {
  it('names today, the day the map is painted for', async () => {
    // SNOW-793. The ribbon is the only thing on screen that says WHICH day
    // the choropleth is showing, so the default is only defensible while
    // this reads it back. A coloured map beside "No date selected" would be
    // the SNOW-660 bug with the polarity flipped.
    withScrubberToday(TODAY);

    await loadRibbon('/');

    const ribbon = document.getElementById('map-date-ribbon');
    expect(ribbon.hidden).toBe(false);
    expect(ribbon.textContent).toBe(`formatted:${TODAY}`);
  });

  it('says "No date selected" when no day is knowable', async () => {
    // The fallback: no `?d=`, and no readable `data-today` to fall back to.
    // Hiding the readout instead would leave a blank choropleth beside a
    // gap, which reads as a broken page.
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

  it('lets ?d= win over today rather than blending the two', async () => {
    // Precedence, stated once here because it is the property a deep link
    // depends on: a shared `?d=` link must survive being opened on any
    // later day.
    withScrubberToday(TODAY);

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

});
