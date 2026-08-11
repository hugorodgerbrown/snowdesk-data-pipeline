/*
 * tests/js/_load_map_bundle.js — boot the split map.js under jsdom.
 *
 * SNOW-610 split static/js/map.js into thirteen classic scripts. In the
 * browser they share ONE global lexical scope, so `map_basemap_downloads.js`
 * reads `MAP` and `COUNTRY_STATE` — declared in `map_state.js` — as bare
 * identifiers. That is the whole load-order contract home.html encodes.
 *
 * `await import()` cannot reproduce it. Vitest evaluates each file as an ES
 * module, and a top-level `let`/`const` in an ES module is MODULE-scoped:
 * every cross-file bare identifier would be a ReferenceError, even though
 * the same files work perfectly when the browser loads them as classic
 * scripts. The tests were not wrong to import map.js before the split —
 * back then every reference was intra-file, so module scope was invisible.
 *
 * So this reads the files and evaluates them together in one function
 * scope, which is the closest faithful model of the browser's shared
 * lexical scope: cross-file bare identifiers resolve, `window.X = …`
 * assignments still escape to the global, and each call gets a fresh scope
 * so a suite can re-boot per test without "Identifier already declared"
 * (which is exactly what indirect `eval` into the real global scope would
 * have hit on the second `beforeEach`).
 *
 * Order is not cosmetic — it is home.html's order, and the reason the
 * declaration files come before map.js is that map.js's boot IIFE runs at
 * parse time and would otherwise read a binding still in its temporal dead
 * zone.
 *
 * SNOW-647: this list and the template's script tags have to agree, and
 * `tests/public/test_map_script_order.py` now enforces that rather than
 * asking. It renders the homepage, filters the scripts down to the entries
 * below, and fails if the two sequences differ — in either direction, and
 * including a new map module tagged in the template but never added here.
 * So adding a module means editing exactly two places, and forgetting one
 * of them is a red test rather than a map that silently stops booting.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC_JS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../static/js',
);

/*
 * SNOW-656: the `*_core.js` modules `map.js` reads AT PARSE TIME, evaluated
 * ahead of the bundle exactly as home.html loads them ahead of it.
 *
 * Most cores are reached from a call site — `choropleth_core.js` from
 * `installRegionsLayers`, `search_core.js` from the search box — so a suite
 * that never exercises that path boots fine without them, and the ones that
 * do import them explicitly. `layer_visibility_core.js` is different:
 * `map.js`'s boot IIFE resolves the Bulletins preference through it while
 * seeding `overlayState`, so a bundle booted without it throws before any
 * test has run. That is a property of the bundle, not of any one suite, so
 * it belongs here rather than in an import every future suite has to
 * remember.
 *
 * `hatch_core.js` is here for the near-miss version of the same reason. It
 * IS reached from a call site — `installRegionsLayers`, which registers the
 * downloaded-overlay hatch image before adding the layer that names it —
 * but that call site is on the path of every suite that fires the `load`
 * handler, which is most of them, and the failure it produces without this
 * (`hatchPixels` of undefined, thrown from inside a MapLibre event handler)
 * names nothing a reader could act on.
 *
 * Deliberately declared BEFORE the bundle array below: SNOW-647's
 * `tests/public/test_map_script_order.py` parses that array out of this
 * file's text by splitting on its export statement, so anything above it is
 * invisible to that check — which is right, since these are not bundle
 * members and home.html loads them outside the bundle's contiguous run.
 * (That same parse is why this paragraph paraphrases the export rather than
 * quoting it: a second occurrence of the literal it splits on lands the
 * parse in the wrong half of the file and yields nothing.)
 */
const PARSE_TIME_CORES = ['layer_visibility_core.js', 'hatch_core.js'];

/** home.html's script order for the map bundle. */
export const MAP_BUNDLE = [
  // Declarations — must precede map.js.
  'map_state.js',
  'map_basemap_downloads.js',
  'map_shared.js',
  // The boot IIFE.
  'map.js',
  // Surfaces, in the order they held inside the single file.
  'map_scrubber.js',
  'map_legend.js',
  'map_timelapse.js',
  'map_basemap_picker.js',
  'map_region_download.js',
  'map_custom_download.js',
  'map_autozoom.js',
  'map_geolocate.js',
  'map_season_ribbon.js',
];

/**
 * Evaluate the map bundle in one shared scope.
 *
 * Call it where a suite used to `await import('../../static/js/map.js')`,
 * AFTER the `*_core.js` modules it depends on (those are real modules and
 * publish themselves on `window`, so they keep their own `import`).
 *
 * @param {string[]} files Filenames under static/js, in load order.
 * @returns {void}
 */
export function loadMapBundle(files = MAP_BUNDLE) {
  const source = PARSE_TIME_CORES.concat(files)
    .map((name) => `//# ${name}\n${readFileSync(path.join(STATIC_JS, name), 'utf8')}`)
    .join('\n;\n');
  // eslint-disable-next-line no-new-func
  new Function(source)();
}
