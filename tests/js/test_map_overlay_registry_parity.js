/*
 * tests/js/test_map_overlay_registry_parity.js — every layers-menu row the
 * picker can be asked to toggle must have an entry in the overlay registry.
 *
 * SNOW-761 shipped a Weather row in the layers menu whose key was absent
 * from the picker's layer table. The row was therefore in the DOM,
 * clickable, and wired to a storage key, but the click handler's non-lazy
 * branch does
 *
 *     for (const layerId of OVERLAY_LAYERS[overlayKey])
 *
 * which threw ``... is not iterable`` on an undefined entry. The failure is
 * a nasty shape: the row still flipped aria-checked, so it LOOKED toggled,
 * while the throw happened before any fetch or layer install. Nothing
 * server-side could see it, every Python test passed, and the boot-time
 * restore path — which does not go through this handler — kept working, so
 * a developer who already had the overlay enabled saw a working map.
 *
 * SNOW-897: this was a SOURCE-TEXT assertion, because "the registry is a
 * module-private literal inside an IIFE, so there is nothing to import".
 * That is no longer true — the two near-identical tables became one
 * ``OVERLAY_LAYERS`` in map_state.js, published on ``snowdeskMapState`` —
 * so the check is now a runtime one against the real object. The regexes it
 * used to need are gone, and with them the risk that a reformat of the
 * literal silently made the whole file vacuous.
 *
 * Only the template is still parsed, because ``data-overlay-key`` genuinely
 * is source text: the contract is a correspondence between what the menu
 * renders and what the registry knows.
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

import '../../static/js/i18n_strings.js';

await import('../../static/js/map_state.js');

const { overlayLayers } = window.snowdeskMapState;

const ROOT = join(import.meta.dirname, '..', '..');
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

describe('layers-menu overlay registry parity', () => {
  it('every non-country row has an entry in OVERLAY_LAYERS', () => {
    const rows = [...templateOverlayKeys()].filter(
      (k) => !k.startsWith('country.'),
    );

    expect(rows.length).toBeGreaterThan(0);
    const missing = rows.filter((k) => !(k in overlayLayers));
    expect(
      missing,
      `layers-menu rows with no OVERLAY_LAYERS entry: ${missing.join(', ')}`,
    ).toEqual([]);
  });

  it('reads a plausible template rather than silently matching nothing', () => {
    // Guards the regex above: a parser that quietly returns an empty set
    // would make the assertion vacuous and the whole file worthless. The
    // registry side needs no such guard any more — it is a real object.
    expect(templateOverlayKeys().size).toBeGreaterThanOrEqual(5);
    expect(Object.keys(overlayLayers).length).toBeGreaterThanOrEqual(5);
    expect(overlayLayers).toHaveProperty('resorts');
  });
});
