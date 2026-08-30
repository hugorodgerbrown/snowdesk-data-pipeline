/*
 * tests/js/test_map_overlay_registry_parity.js — every layers-menu row the
 * picker can be asked to toggle must have an OVERLAY_LAYER_IDS entry.
 *
 * SNOW-761 shipped a Weather row in the layers menu whose key was absent
 * from static/js/map_basemap_picker.js's OVERLAY_LAYER_IDS. The row was
 * therefore in the DOM, clickable, and wired to a storage key, but the
 * click handler's non-lazy branch does
 *
 *     for (const layerId of OVERLAY_LAYER_IDS[overlayKey])
 *
 * which threw ``... is not iterable`` on an undefined entry. The failure is
 * a nasty shape: the row still flipped aria-checked, so it LOOKED toggled,
 * while the throw happened before any fetch or layer install. Nothing
 * server-side could see it, every Python test passed, and the boot-time
 * restore path — which does not go through this handler — kept working, so
 * a developer who already had the overlay enabled saw a working map.
 *
 * A source-text assertion rather than a runtime one, mirroring
 * test_map_css_basemap_fallback.js: the registry is a module-private
 * literal inside an IIFE, so there is nothing to import, and the contract
 * being checked is a correspondence between two files rather than a
 * behaviour jsdom can drive.
 *
 * The ``country.*`` keys are deliberately excluded: the handler returns for
 * them before reaching the loop (they dispatch snowdesk:country-toggle
 * instead), so they need no entry. Every other key does — a lazy overlay
 * needs it for its toggle-OFF path even though the layer is installed by
 * the main IIFE.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = join(import.meta.dirname, '..', '..');
const PICKER = readFileSync(
  join(ROOT, 'static', 'js', 'map_basemap_picker.js'),
  'utf8',
);
const MAP_EMBED = readFileSync(
  join(
    ROOT, 'apps', 'public', 'templates', 'public', 'partials', '_map_embed.html',
  ),
  'utf8',
);

/** Return every data-overlay-key rendered by the layers menu. */
function templateOverlayKeys() {
  const keys = new Set();
  for (const m of MAP_EMBED.matchAll(/data-overlay-key="([^"]+)"/g)) {
    keys.add(m[1]);
  }
  return keys;
}

/** Return the keys declared in the picker's OVERLAY_LAYER_IDS literal. */
function registryKeys() {
  const start = PICKER.indexOf('const OVERLAY_LAYER_IDS = {');
  expect(start, 'OVERLAY_LAYER_IDS literal not found').toBeGreaterThan(-1);
  const body = PICKER.slice(start, PICKER.indexOf('\n  };', start));
  const keys = new Set();
  // Key at the start of a line, ignoring the comment lines between entries.
  for (const m of body.matchAll(/^\s{4}([a-z0-9_]+):\s*\[/gm)) {
    keys.add(m[1]);
  }
  return keys;
}

describe('layers-menu overlay registry parity', () => {
  it('every non-country row has an OVERLAY_LAYER_IDS entry', () => {
    const rows = [...templateOverlayKeys()].filter(
      (k) => !k.startsWith('country.'),
    );
    const registry = registryKeys();

    expect(rows.length).toBeGreaterThan(0);
    const missing = rows.filter((k) => !registry.has(k));
    expect(
      missing,
      `layers-menu rows with no OVERLAY_LAYER_IDS entry: ${missing.join(', ')}`,
    ).toEqual([]);
  });

  it('parses a plausible registry rather than silently matching nothing', () => {
    // Guards the regexes above: a parser that quietly returns empty sets
    // would make the assertion vacuous and the whole file worthless.
    const registry = registryKeys();

    expect(registry.size).toBeGreaterThanOrEqual(5);
    expect(registry.has('resorts')).toBe(true);
    expect(templateOverlayKeys().size).toBeGreaterThanOrEqual(5);
  });
});
