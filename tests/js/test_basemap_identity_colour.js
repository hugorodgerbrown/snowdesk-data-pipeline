/*
 * tests/js/test_basemap_identity_colour.js — Vitest unit tests for
 * basemapIdentityColour() and basemapLabel() in
 * static/js/map_basemap_downloads.js (SNOW-645 review, items 2 and the
 * "shared basemapLabel helper" follow-up).
 *
 * MapLibre paint values can't reference a CSS custom property, so every
 * consumer that needs a basemap's identity colour for a paint property —
 * the download progress grid and (item 3) the downloaded-areas overlay —
 * has to read it off the live document instead. This is the one place
 * that resolution logic lives, in three tiers:
 *
 *   1. --color-basemap-<key, underscores to dashes> off document.documentElement
 *      — the SAME token src/css/main.css's .basemap-swatch and
 *      static/css/map.css's roundel fill read.
 *   2. --color-sync-ok — no key, an unrecognised key, or (matching those
 *      two files' own var(…, var(--color-sync-ok)) fallback) a stale build
 *      where tier 1's token isn't defined.
 *   3. The hard-coded hex floor — tier 2 itself came back empty.
 *
 * basemapLabel is the picker-label lookup the region roundel's
 * "downloaded under another basemap" state and the Manage downloads
 * sheet both need — extracted here from what used to be
 * map_downloads_manager.js's own private copy, so there is exactly ONE
 * implementation. The "single implementation" describe block below is a
 * source-text check, not a behavioural one: it asserts
 * map_downloads_manager.js carries no `basemapLabel`/`basemapLabelsByKey`
 * declaration of its own, which a behavioural test alone couldn't rule
 * out (two implementations that happen to agree would still pass one).
 *
 * basemapIdentityColour, activeBasemapKey and basemapLabel are plain
 * function declarations, not exported onto window the way
 * window.pwaBasemapDownloads is — so this evaluates map_basemap_downloads.js
 * standalone (it is a classic script with no top-level statement that
 * needs MAP/COUNTRY_STATE at PARSE time — see its own module-header
 * comment) and exposes just the functions under test, rather than paying
 * for the full 13-file map bundle _load_map_bundle.js boots.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

// basemapLabel calls self.pwaStrings.collapse — needed for the
// basemapLabel describe blocks below (basemapIdentityColour itself
// doesn't touch it).
import '../../static/js/i18n_strings.js';

const SOURCE = readFileSync(
  join(process.cwd(), 'static', 'js', 'map_basemap_downloads.js'),
  'utf8',
);

/** Evaluate map_basemap_downloads.js and expose its helpers for the test to call. */
function loadHelper() {
  // eslint-disable-next-line no-new-func
  new Function(
    `${SOURCE}\n` +
      'window.basemapIdentityColour = basemapIdentityColour;\n' +
      'window.basemapLabel = basemapLabel;',
  )();
}

beforeEach(() => {
  document.documentElement.removeAttribute('style');
  loadHelper();
});

afterEach(() => {
  document.documentElement.removeAttribute('style');
  delete window.basemapIdentityColour;
  delete window.basemapLabel;
  // The file's own top-level `window.pwaBasemapDownloads = Object.freeze(…)`
  // runs as a side effect of loadHelper(); tidy it up so it can't leak into
  // an unrelated test file sharing this jsdom instance.
  delete window.pwaBasemapDownloads;
});

describe('basemapIdentityColour (SNOW-645 review)', () => {
  it("resolves a known key to its own --color-basemap-* token", () => {
    document.documentElement.style.setProperty(
      '--color-basemap-openfreemap-liberty',
      'rgb(1, 2, 3)',
    );
    document.documentElement.style.setProperty('--color-sync-ok', 'rgb(4, 5, 6)');

    expect(window.basemapIdentityColour('openfreemap_liberty')).toBe('rgb(1, 2, 3)');
  });

  it('falls back to --color-sync-ok for an unrecognised key', () => {
    document.documentElement.style.setProperty('--color-sync-ok', 'rgb(4, 5, 6)');

    expect(window.basemapIdentityColour('no_such_basemap')).toBe('rgb(4, 5, 6)');
  });

  it('falls back to --color-sync-ok for a null/absent key', () => {
    document.documentElement.style.setProperty('--color-sync-ok', 'rgb(4, 5, 6)');

    expect(window.basemapIdentityColour(null)).toBe('rgb(4, 5, 6)');
    expect(window.basemapIdentityColour(undefined)).toBe('rgb(4, 5, 6)');
  });

  it('falls back to --color-sync-ok when the key token is a stale-build blank (defined but empty)', () => {
    document.documentElement.style.setProperty('--color-basemap-openfreemap-liberty', '');
    document.documentElement.style.setProperty('--color-sync-ok', 'rgb(4, 5, 6)');

    expect(window.basemapIdentityColour('openfreemap_liberty')).toBe('rgb(4, 5, 6)');
  });

  it('falls back to the hex floor when neither token is defined at all', () => {
    // Nothing set on documentElement — the pathological "no stylesheet
    // loaded" case tier 3 exists for.
    expect(window.basemapIdentityColour('openfreemap_liberty')).toBe('#16a34a');
    expect(window.basemapIdentityColour(null)).toBe('#16a34a');
  });
});

describe('basemapLabel (SNOW-645 follow-up — one shared implementation)', () => {
  function buildMenu() {
    document.body.innerHTML = `
      <ul id="basemap-menu">
        <li role="none">
          <button type="button" data-basemap-key="openfreemap_liberty">Standard</button>
        </li>
        <li role="none">
          <button type="button" data-basemap-key="swisstopo_winter">Swisstopo (CH)</button>
        </li>
      </ul>`;
  }

  it("reads the picker's own translated label for a known key", () => {
    buildMenu();
    expect(window.basemapLabel('swisstopo_winter')).toBe('Swisstopo (CH)');
  });

  it('returns an empty string for a key the picker has no row for', () => {
    buildMenu();
    expect(window.basemapLabel('no_such_basemap')).toBe('');
  });

  it('returns an empty string with no #basemap-menu in the document at all', () => {
    document.body.innerHTML = '';
    expect(window.basemapLabel('openfreemap_liberty')).toBe('');
  });
});

describe('basemapLabel has exactly one implementation (SNOW-645 follow-up)', () => {
  // Source-text, not behavioural: two implementations that happened to
  // agree would still pass every test above. This is what actually rules
  // a second copy out — map_downloads_manager.js reaches the SAME
  // function through window.pwaBasemapDownloads.basemapLabel (see its own
  // buildRow comment for why it's the bridge and not a bare identifier),
  // and map_region_download.js calls it bare (it's inside the map
  // bundle's load-order contract, same as activeBasemapKey already is).
  const DOWNLOADS_MANAGER_SOURCE = readFileSync(
    join(process.cwd(), 'static', 'js', 'map_downloads_manager.js'),
    'utf8',
  );
  const REGION_DOWNLOAD_SOURCE = readFileSync(
    join(process.cwd(), 'static', 'js', 'map_region_download.js'),
    'utf8',
  );

  it('is declared exactly once, in map_basemap_downloads.js', () => {
    expect(SOURCE.match(/\bfunction basemapLabel\(/g)).toHaveLength(1);
  });

  it('map_downloads_manager.js has no basemapLabel/basemapLabelsByKey declaration of its own', () => {
    expect(DOWNLOADS_MANAGER_SOURCE).not.toMatch(/function basemapLabel\(/);
    expect(DOWNLOADS_MANAGER_SOURCE).not.toMatch(/\bbasemapLabelsByKey\b/);
  });

  it("map_downloads_manager.js reaches it through window.pwaBasemapDownloads, map_region_download.js calls it bare", () => {
    expect(DOWNLOADS_MANAGER_SOURCE).toMatch(/window\.pwaBasemapDownloads\?\.basemapLabel\?\.\(/);
    expect(REGION_DOWNLOAD_SOURCE).toMatch(/(?<!\.)\bbasemapLabel\(basemapKey\)/);
  });
});
