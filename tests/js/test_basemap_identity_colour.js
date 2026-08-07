/*
 * tests/js/test_basemap_identity_colour.js — Vitest unit tests for
 * basemapIdentityColour() in static/js/map_basemap_downloads.js (SNOW-645
 * review, item 2).
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
 * basemapIdentityColour and activeBasemapKey are plain function
 * declarations, not exported onto window the way
 * window.pwaBasemapDownloads is — so this evaluates map_basemap_downloads.js
 * standalone (it is a classic script with no top-level statement that
 * needs MAP/COUNTRY_STATE at PARSE time — see its own module-header
 * comment) and exposes just the one function under test, rather than
 * paying for the full 13-file map bundle _load_map_bundle.js boots.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const SOURCE = readFileSync(
  join(process.cwd(), 'static', 'js', 'map_basemap_downloads.js'),
  'utf8',
);

/** Evaluate map_basemap_downloads.js and expose basemapIdentityColour for the test to call. */
function loadHelper() {
  // eslint-disable-next-line no-new-func
  new Function(`${SOURCE}\nwindow.basemapIdentityColour = basemapIdentityColour;`)();
}

beforeEach(() => {
  document.documentElement.removeAttribute('style');
  loadHelper();
});

afterEach(() => {
  document.documentElement.removeAttribute('style');
  delete window.basemapIdentityColour;
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
