/*
 * tests/js/test_overlays.js — Vitest unit tests for static/js/overlays.js
 * (SNOW-486, SNOW-496).
 *
 * Ports tests/e2e/test_overlay_dismiss.py's single case onto the Vitest +
 * jsdom harness — the shared dismiss primitive needs no live MapLibre map,
 * login session, or geolocation grant; a bare `[data-overlay]` fixture with
 * a nested `[data-action="dismiss"]` control is enough to exercise the
 * delegated click handler, the two hide idioms, the `data-overlay-persist`
 * write, and the bubbling `overlay:dismissed` event.
 *
 * `overlays.js` is a browser IIFE with no exports — importing it for side
 * effects (it self-wires a delegated `document` click listener at import
 * time) is enough.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/overlays.js';

/**
 * Build a `[data-overlay]` fixture using the ATTRIBUTE hide idiom (the
 * default — map-internal overlays/sheets: #home-intro, #map-help-overlay,
 * #favourite-sheet, #report-sheet).
 */
function attributeOverlayFixture() {
  document.body.innerHTML = `
    <div id="report-sheet" data-overlay data-overlay-persist="snowdesk.report.dismissed=1">
      <button type="button" data-action="dismiss">×</button>
    </div>
  `;
  return document.getElementById('report-sheet');
}

/**
 * Build a `[data-overlay]` fixture using the CLASS hide idiom
 * (`data-overlay-hide="class"` — shell-chrome banners/toasts/modals).
 */
function classOverlayFixture() {
  document.body.innerHTML = `
    <div id="sw-update-banner" data-overlay data-overlay-hide="class">
      <button type="button" data-action="dismiss">×</button>
    </div>
  `;
  return document.getElementById('sw-update-banner');
}

beforeEach(() => {
  document.body.innerHTML = '';
  try {
    localStorage.clear();
  } catch (_e) {
    // Ignore.
  }
});

describe('dismiss click — attribute idiom', () => {
  it('hides the overlay via the hidden attribute and fires overlay:dismissed', () => {
    const overlay = attributeOverlayFixture();
    let detailOverlay = null;
    document.addEventListener('overlay:dismissed', (e) => {
      detailOverlay = e.detail.overlay;
    });

    overlay.querySelector('[data-action="dismiss"]').click();

    expect(overlay.hasAttribute('hidden')).toBe(true);
    expect(detailOverlay).toBe(overlay);
  });

  it('persists the data-overlay-persist declaration to localStorage', () => {
    const overlay = attributeOverlayFixture();
    overlay.querySelector('[data-action="dismiss"]').click();

    expect(localStorage.getItem('snowdesk.report.dismissed')).toBe('1');
  });
});

describe('dismiss click — class idiom', () => {
  it('hides the overlay via the Tailwind hidden class', () => {
    const overlay = classOverlayFixture();
    overlay.querySelector('[data-action="dismiss"]').click();

    expect(overlay.classList.contains('hidden')).toBe(true);
    expect(overlay.hasAttribute('hidden')).toBe(false);
  });
});

describe('dismiss click — no [data-overlay] ancestor', () => {
  it('is a silent no-op', () => {
    document.body.innerHTML = '<button type="button" data-action="dismiss">×</button>';
    expect(() => document.querySelector('[data-action="dismiss"]').click()).not.toThrow();
  });
});

// Note: the module's toast auto-dismiss controller (`data-toast-timeout`) is
// not covered here — it only wires up via a `querySelectorAll` that runs
// once at IIFE-load time (before any test fixture exists), and the module
// header itself documents it as inert today ("No current template passes
// timeout" — it exists for a future toast to opt into without new JS). Not
// a regression: the retired Playwright file never covered it either.
