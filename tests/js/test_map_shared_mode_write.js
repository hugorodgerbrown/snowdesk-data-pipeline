/*
 * tests/js/test_map_shared_mode_write.js — the shared date write carries
 * ?mode=terrain only behind the map_modes flag (SNOW-1394).
 *
 * map_mode_core.js loads for everyone (every core map.js reads is loaded on
 * /map/, tests/public/test_home_scripts.py), so the flag, not the core's
 * presence, decides whether a date write keeps ?mode=. Off the flag a
 * pasted ?mode=terrain is dropped on the first day change, as every other
 * parameter is.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/map_mode_core.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(path.join(here, '../../static/js/map_shared.js'), 'utf8');

/** A fresh copy of map_shared.js's date writer, in its own scope. */
const writer = () => new Function(`${SOURCE}\nreturn writeUrlDateParam;`)();

/** Put #map on the page with the flag's attribute as given. */
function mapWithFlag(on) {
  document.body.innerHTML = `<div id="map" data-map-modes-enabled="${on}"></div>`;
}

beforeEach(() => {
  history.replaceState(null, '', '/map/?d=2026-02-10&mode=terrain');
});

describe('writeUrlDateParam', () => {
  it('keeps ?mode=terrain through a date write behind the flag', () => {
    mapWithFlag(true);
    writer()('2026-02-11');
    expect(location.search).toBe('?d=2026-02-11&mode=terrain');
  });

  it('writes ?d= alone without the flag, though the core is loaded', () => {
    mapWithFlag(false);
    writer()('2026-02-11');
    expect(location.search).toBe('?d=2026-02-11');
  });
});
