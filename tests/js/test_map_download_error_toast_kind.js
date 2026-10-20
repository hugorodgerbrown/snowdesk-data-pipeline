/*
 * tests/js/test_map_download_error_toast_kind.js — a REGION run's
 * quota/budget refusal reads the region copy (SNOW-1137).
 *
 * The quota and budget toasts used to tell every user to "frame a smaller
 * area" — advice a region download cannot take, because a region's
 * outline is the provider's. _map_embed.html now carries a region variant
 * of each, and `revealBasemapDownloadError` picks it when the run's
 * `kind` is 'region'. `runPinnedDownload` carries that kind into the
 * runner's `revealError` dependency, which is the path a budget refusal
 * actually takes.
 *
 * Boots only the two declaration files the functions live in, in one
 * shared scope (see _load_map_bundle.js for why a plain import cannot),
 * and hands the two functions out through a test-only global.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';

const STATIC_JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js');

const TOAST_IDS = [
  'map-download-error-toast',
  'map-download-error-toast-quota',
  'map-download-error-toast-budget',
  'map-download-error-toast-quota-region',
  'map-download-error-toast-budget-region',
  'map-download-error-toast-no-worker',
];

/** The toast ids currently shown. */
function shown() {
  return TOAST_IDS.filter((id) => !document.getElementById(id).classList.contains('hidden'));
}

let api;

beforeEach(() => {
  document.body.innerHTML = TOAST_IDS.map((id) => `<div id="${id}" class="hidden"></div>`).join('');
  const source = ['map_state.js', 'map_basemap_downloads.js']
    .map((name) => readFileSync(path.join(STATIC_JS, name), 'utf8'))
    .join('\n;\n');
  // eslint-disable-next-line no-new-func
  new Function(
    `${source}\n;window.__downloadErrorApi = { reveal: revealBasemapDownloadError, run: runPinnedDownload };`,
  )();
  api = window.__downloadErrorApi;
});

describe('revealBasemapDownloadError', () => {
  it.each(['quota', 'budget'])('shows the region %s copy for a region run', (reason) => {
    api.reveal(reason, 'region');
    expect(shown()).toEqual([`map-download-error-toast-${reason}-region`]);
  });

  it.each(['quota', 'budget'])('keeps the frame-a-smaller-area %s copy otherwise', (reason) => {
    api.reveal(reason);
    expect(shown()).toEqual([`map-download-error-toast-${reason}`]);
  });

  it('falls back to the generic toast for a region run with no reason of its own', () => {
    api.reveal(null, 'region');
    expect(shown()).toEqual(['map-download-error-toast']);
  });
});

describe('runPinnedDownload', () => {
  it("hands a region run's kind to the runner's revealError", async () => {
    self.pwaBasemapDownloadRunner = {
      run: async (deps) => deps.revealError('budget'),
    };
    await api.run({ areaId: 'region-CH-4115', kind: 'region', paint: () => {} });
    expect(shown()).toEqual(['map-download-error-toast-budget-region']);
  });

  it('leaves a custom-area run on the custom copy', async () => {
    self.pwaBasemapDownloadRunner = {
      run: async (deps) => deps.revealError('budget'),
    };
    await api.run({ areaId: 'custom-abc', paint: () => {} });
    expect(shown()).toEqual(['map-download-error-toast-budget']);
  });
});
