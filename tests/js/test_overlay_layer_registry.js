/*
 * tests/js/test_overlay_layer_registry.js — one overlay-to-layer table, and
 * the invariants the two-table version could not state (SNOW-897).
 *
 * Until this ticket the mapping lived twice: `OVERLAY_LAYER_IDS_MAIN` in
 * map.js and `OVERLAY_LAYER_IDS` in map_basemap_picker.js. Five entries were
 * duplicated verbatim, four existed only in one and one only in the other —
 * so the pair was neither a clean copy nor a clean partition, and nothing
 * could notice if the duplicated five drifted apart.
 *
 * Merging them makes two properties checkable that were not before:
 *
 *   1. Every switchable overlay has layer ids. A key in OVERLAY_STORAGE_KEY
 *      with no entry here is a switch wired to nothing.
 *   2. `regions-fill` is in NO entry. That is the regression this file
 *      exists for — see below.
 */

import { describe, expect, it } from 'vitest';

// map_state.js alone, not the whole bundle: this file asserts a data table,
// and booting map.js would need a DOM fixture and a MapLibre stub to say
// nothing extra. Importing it as an ES module module-scopes the bare
// bindings, but `window.snowdeskMapState`'s accessors close over them, so the
// published channel reads the same values a browser would.
import '../../static/js/i18n_strings.js';
await import('../../static/js/map_state.js');

const { overlayLayers, overlayStorageKey } = window.snowdeskMapState;

describe('the overlay layer registry', () => {
  it('gives every overlay at least one layer id', () => {
    for (const [key, layers] of Object.entries(overlayLayers)) {
      expect(Array.isArray(layers), `${key} must map to an array`).toBe(true);
      expect(layers.length, `${key} must name at least one layer`).toBeGreaterThan(0);
    }
  });

  it('names no layer twice within one overlay', () => {
    for (const [key, layers] of Object.entries(overlayLayers)) {
      expect(new Set(layers).size, `${key} repeats a layer id`).toBe(layers.length);
    }
  });

  it('gives no layer to two different overlays', () => {
    // Two overlays owning one layer means two switches fighting over its
    // visibility, and the losing one silently does nothing.
    const owner = new Map();
    for (const [key, layers] of Object.entries(overlayLayers)) {
      for (const id of layers) {
        expect(owner.has(id), `${id} is claimed by both ${owner.get(id)} and ${key}`)
          .toBe(false);
        owner.set(id, key);
      }
    }
  });

  it('never claims regions-fill, which is driven by opacity', () => {
    // The one overlay layer NOT driven by visibility. It is the map's
    // hit-test target, and `queryRenderedFeatures` returns nothing from a
    // layer at `visibility: none` — so a well-meaning tidy-up that added it
    // to the `bulletins` entry would make the whole map untappable at fill
    // step 0, with no error anywhere. `applyBulletinsVisibility` in map.js is
    // its single writer, and this assertion is what keeps it that way.
    const claimed = Object.values(overlayLayers).flat();
    expect(claimed).not.toContain('regions-fill');
  });

  it('covers every overlay that has a persisted switch', () => {
    // A key the user can toggle but that names no layers is a switch wired
    // to nothing — the failure SNOW-897's merge makes visible. Three keys
    // are deliberately exempt, each for a stated reason.
    const NO_LAYERS_OF_THEIR_OWN = new Set([
      // regions-fill + bulletin-groupings-line, driven by
      // applyBulletinsVisibility rather than a visibility loop.
      'bulletins',
      // The downloads panel's "Display on the map" switch drives the
      // cached-tiles layers through window.pwaDownloadedOverlay.
      'downloads',
    ]);
    const switchable = Object.keys(overlayStorageKey);
    // Guard against a vacuous pass: an empty table would satisfy the loop
    // below without checking anything.
    expect(switchable.length).toBeGreaterThan(5);
    for (const key of switchable) {
      if (NO_LAYERS_OF_THEIR_OWN.has(key)) continue;
      expect(overlayLayers, `${key} has a switch but no layers`).toHaveProperty(key);
    }
  });
});
