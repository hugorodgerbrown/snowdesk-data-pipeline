/*
 * tests/js/test_map_3d_notice.js — "3D is experimental" (SNOW-1470).
 *
 * static/js/map_3d_notice.js against a reduced copy of the banner, with
 * the view announced as map_view_3d.js announces it. What is pinned: the
 * banner shows at a 3D boot and on entering 3D, hides when the map goes
 * flat (by the switch or on its own), and once dismissed stays hidden for
 * the visit, while another banner's dismissal changes nothing.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const STATIC_JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js');
const SOURCE = readFileSync(path.join(STATIC_JS, 'map_3d_notice.js'), 'utf8');

let listeners = [];
const realAdd = document.addEventListener.bind(document);

const banner = () => document.getElementById('map-3d-notice');
const shown = () => !banner().classList.contains('hidden');

/** Boot the module, keeping its listeners so each test can drop them. */
function boot() {
  document.addEventListener = (type, fn, opts) => {
    listeners.push([type, fn, opts]);
    realAdd(type, fn, opts);
  };
  try {
    new Function(SOURCE)();
  } finally {
    document.addEventListener = realAdd;
  }
}

/** Announce a view change as map_view_3d.js does. */
function announce(view, reason) {
  document.documentElement.dataset.mapView = view;
  const detail = reason ? { view, reason } : { view };
  document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail }));
}

/** Dismiss `el` as overlays.js does. */
function dismiss(el) {
  el.classList.add('hidden');
  el.dispatchEvent(new CustomEvent('overlay:dismissed', { bubbles: true, detail: { overlay: el } }));
}

beforeEach(() => {
  delete document.documentElement.dataset.mapView;
  document.body.innerHTML = `
    <div id="map-3d-notice" class="hidden" data-overlay data-overlay-hide="class"></div>
    <div id="offmap-banner" class="hidden" data-overlay data-overlay-hide="class"></div>
  `;
});

afterEach(() => {
  for (const [type, fn, opts] of listeners) document.removeEventListener(type, fn, opts);
  listeners = [];
});

describe('map_3d_notice', () => {
  it('stays hidden on a flat boot', () => {
    document.documentElement.dataset.mapView = 'flat';
    boot();
    expect(shown()).toBe(false);
  });

  it('shows at a 3D boot', () => {
    document.documentElement.dataset.mapView = '3d';
    boot();
    expect(shown()).toBe(true);
  });

  it('shows on entering 3D and hides on going flat', () => {
    boot();
    announce('3d');
    expect(shown()).toBe(true);
    announce('flat');
    expect(shown()).toBe(false);
  });

  it('hides when the map leaves 3D on its own', () => {
    boot();
    announce('3d');
    announce('flat', 'slow');
    expect(shown()).toBe(false);
  });

  it('is not shown again after a dismiss', () => {
    boot();
    announce('3d');
    dismiss(banner());
    announce('flat');
    announce('3d');
    expect(shown()).toBe(false);
  });

  it('ignores another banner being dismissed', () => {
    boot();
    announce('3d');
    dismiss(document.getElementById('offmap-banner'));
    announce('flat');
    announce('3d');
    expect(shown()).toBe(true);
  });

  it('does nothing without the banner', () => {
    document.body.innerHTML = '';
    expect(() => boot()).not.toThrow();
  });
});
