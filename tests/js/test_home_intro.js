/*
 * tests/js/test_home_intro.js — Vitest unit tests for static/js/home_intro.js
 * (SNOW-314, SNOW-496).
 *
 * Ports tests/e2e/test_home_intro.py's three cases onto the Vitest + jsdom
 * harness. home_intro.js's own dismiss handling is thin — the actual hide
 * + localStorage-persist for the "×" click is done by overlays.js's shared
 * delegated handler (SNOW-486), so both modules are loaded here, matching
 * production load order (overlays.js loads on every public page before
 * home_intro.js).
 *
 * home_intro.js reads `#home-intro` and returns early if it's absent, so
 * the fixture (mirroring the shape rendered by
 * apps/public/templates/public/partials/_map_embed.html) must exist BEFORE the
 * module is imported. Both modules are dynamically imported per test (via
 * `vi.resetModules()` + `await import(...)`) so each test gets a fresh
 * IIFE run against its own fixture/location/localStorage state.
 *
 * SNOW-535 added a second dismiss control (#home-intro-close, the "×") and
 * wired the existing "Explore the map" CTA (#home-intro-dismiss) to also
 * dispatch 'snowdesk:map-help-requested' — the event static/js/map_help.js
 * listens for to open the coachmark tour. Both are covered below; the
 * fixture carries both buttons.
 *
 * The panel is now re-openable at any time from the map's "?" roundel,
 * which dispatches 'snowdesk:home-intro-requested' (map_help.js) rather
 * than starting that tour. Two consequences are covered here: the re-open
 * itself, and the registry membership it forced — a panel that can appear
 * over a map the visitor has been using has to close whatever else is
 * open, and be closed by it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'snowdesk.home.intro';
const DISMISSED_VALUE = 'dismissed';

function buildFixture() {
  document.body.innerHTML = `
    <div id="home-intro" data-overlay data-overlay-persist="snowdesk.home.intro=dismissed">
      <div class="home-intro-card">
        <button id="home-intro-close" type="button" data-action="dismiss" aria-label="Dismiss">
          ×
        </button>
        <button id="home-intro-dismiss" type="button" data-action="dismiss">
          Explore the map
        </button>
      </div>
    </div>
  `;
}

/**
 * Re-import the registry + overlays.js + home_intro.js fresh against the
 * current fixture/location, in home.html's own load order
 * (map_overlay_exclusivity.js is loaded there ahead of every registering
 * module). The registry global is deleted first because it is frozen once
 * assigned, so a second IIFE run has to start from nothing.
 */
async function loadModules() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  await import('../../static/js/overlays.js');
  await import('../../static/js/home_intro.js');
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch (_e) {
    // Ignore.
  }
  window.location.hash = '';
  history.replaceState(null, '', '/');
});

afterEach(() => {
  window.location.hash = '';
  history.replaceState(null, '', '/');
});

describe('dismiss', () => {
  it('hides the overlay and persists the dismissed flag to localStorage', async () => {
    buildFixture();
    await loadModules();
    const overlay = document.getElementById('home-intro');

    expect(overlay.hasAttribute('hidden')).toBe(false);

    document.getElementById('home-intro-dismiss').click();

    expect(overlay.hasAttribute('hidden')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });

  it('the "×" also hides the overlay and persists the dismissed flag', async () => {
    // SNOW-535: #home-intro-close carries the same data-action="dismiss"
    // idiom as the CTA, so it goes through the identical shared handler.
    buildFixture();
    await loadModules();
    const overlay = document.getElementById('home-intro');

    document.getElementById('home-intro-close').click();

    expect(overlay.hasAttribute('hidden')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });

  it('the "×" does NOT request the map-help tour', async () => {
    // SNOW-535: only the "Explore the map" CTA below opens the tour — a
    // plain close must not have that side effect.
    buildFixture();
    await loadModules();
    const requested = vi.fn();
    document.addEventListener('snowdesk:map-help-requested', requested);

    document.getElementById('home-intro-close').click();

    expect(requested).not.toHaveBeenCalled();
  });
});

describe('"Explore the map" also requests the map-help tour', () => {
  it('dispatches snowdesk:map-help-requested alongside the shared dismiss', async () => {
    // SNOW-535: home_intro.js binds its own listener on the CTA, in
    // addition to the shared data-action="dismiss" handling, so map_help.js
    // can open the tour without home_intro.js reaching into its internals.
    buildFixture();
    await loadModules();
    const requested = vi.fn();
    document.addEventListener('snowdesk:map-help-requested', requested);
    const overlay = document.getElementById('home-intro');

    document.getElementById('home-intro-dismiss').click();

    expect(requested).toHaveBeenCalledTimes(1);
    expect(overlay.hasAttribute('hidden')).toBe(true);
  });
});

describe('reload with the dismissed flag set', () => {
  it('starts hidden without needing another dismiss', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
  });
});

describe('/#about hash restore', () => {
  it('reopens the overlay on load regardless of the persisted dismissed state', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    window.location.hash = '#about';
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });

  it('reopens the overlay on a later hashchange to #about', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('home-intro-dismiss').click();
    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);

    window.location.hash = '#about';
    window.dispatchEvent(new Event('hashchange'));

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });
});

describe('?intro=1 force-open parameter', () => {
  it('reopens the overlay on load regardless of the persisted dismissed state', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    history.replaceState(null, '', '/?intro=1');
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });

  it('accepts ?intro=true, case-insensitively', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    history.replaceState(null, '', '/?intro=TRUE');
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });

  it('ignores other values, so ?intro=0 leaves a dismissed overlay hidden', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    history.replaceState(null, '', '/?intro=0');
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
  });

  it('is stripped on dismissal so a reload does not force the panel back open', async () => {
    history.replaceState(null, '', '/?intro=1');
    buildFixture();
    await loadModules();

    document.getElementById('home-intro-close').click();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
    expect(new URLSearchParams(location.search).has('intro')).toBe(false);
  });

  it('preserves other query parameters when stripping', async () => {
    history.replaceState(null, '', '/?d=2026-01-15&intro=1&country=ch');
    buildFixture();
    await loadModules();

    document.getElementById('home-intro-close').click();

    const params = new URLSearchParams(location.search);
    expect(params.has('intro')).toBe(false);
    expect(params.get('d')).toBe('2026-01-15');
    expect(params.get('country')).toBe('ch');
  });
});

describe('Escape key', () => {
  it('hides the overlay and persists the dismissed flag', async () => {
    buildFixture();
    await loadModules();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });

  it('runs the same teardown as a click, so ?intro=1 is stripped', async () => {
    // The teardown (hash clear + force-param strip) hangs off
    // 'overlay:dismissed', which only overlays.js's delegated CLICK handler
    // used to dispatch — so the keyboard path left ?intro=1 in the address
    // bar and the next reload forced the panel straight back open.
    history.replaceState(null, '', '/?d=2026-01-15&intro=1');
    buildFixture();
    await loadModules();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    const params = new URLSearchParams(location.search);
    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
    expect(params.has('intro')).toBe(false);
    expect(params.get('d')).toBe('2026-01-15');
  });

  it('clears an #about hash so back/forward does not re-open the panel', async () => {
    window.location.hash = '#about';
    buildFixture();
    await loadModules();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(location.hash).toBe('');
  });
});

describe('snowdesk:home-intro-requested (the "?" roundel)', () => {
  it('re-opens a dismissed panel without clearing the dismissed flag', async () => {
    // A one-shot restore, exactly as the /#about deep link is: the visitor
    // asked to see the panel again, not to be shown it on every load.
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();
    const overlay = document.getElementById('home-intro');
    expect(overlay.hasAttribute('hidden')).toBe(true);

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));

    expect(overlay.hasAttribute('hidden')).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });

  it('focuses the CTA, so the tour is one Enter away', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));

    expect(document.activeElement).toBe(
      document.getElementById('home-intro-dismiss'),
    );
  });

  it('the "×" hands focus back to whatever opened the panel', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    document.body.insertAdjacentHTML(
      'beforeend', '<button id="map-help-toggle" type="button">?</button>',
    );
    await loadModules();
    const toggle = document.getElementById('map-help-toggle');
    toggle.focus();

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));
    document.getElementById('home-intro-close').click();

    expect(document.activeElement).toBe(toggle);
  });

  it('falls back to the roundel when the recorded element is gone', async () => {
    // Opening this panel closes whatever else was up — so a roundel click
    // during the coachmark tour records the tour's tooltip and then hides
    // it. Focusing a display:none element leaves focus on <body>, which is
    // nowhere; the roundel is where a keyboard user would look next.
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    document.body.insertAdjacentHTML(
      'beforeend',
      '<button id="map-help-toggle" type="button">?</button>'
      + '<div id="displaced"><button id="displaced-btn"></button></div>',
    );
    await loadModules();
    const displacedBtn = document.getElementById('displaced-btn');
    displacedBtn.focus();

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));
    // display:none on the element itself, not on the #displaced wrapper:
    // jsdom has no checkVisibility(), so the module's fallback reads the
    // recorded element's OWN computed display and an ancestor's would go
    // unseen here. A real browser answers for both.
    displacedBtn.style.display = 'none';
    document.getElementById('home-intro-close').click();

    expect(document.activeElement).toBe(
      document.getElementById('map-help-toggle'),
    );
  });

  it('does NOT pull focus back when the CTA opens the tour', async () => {
    // The tour focuses its own tooltip on open; the dismissal that same
    // click triggers must not drag focus back to the roundel behind it.
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    document.body.insertAdjacentHTML(
      'beforeend', '<button id="map-help-toggle" type="button">?</button>',
    );
    await loadModules();
    const toggle = document.getElementById('map-help-toggle');
    toggle.focus();

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));
    document.getElementById('home-intro-dismiss').click();

    expect(document.activeElement).not.toBe(toggle);
  });
});

describe('map-overlay exclusivity', () => {
  it('registers itself, so another overlay opening closes it', async () => {
    buildFixture();
    await loadModules();
    const overlay = document.getElementById('home-intro');
    expect(window.pwaMapOverlays.names()).toContain('home-intro');
    expect(overlay.hasAttribute('hidden')).toBe(false);

    window.pwaMapOverlays.opening('map-help-overlay');

    expect(overlay.hasAttribute('hidden')).toBe(true);
    // Displaced, not dismissed: the next load still shows the panel.
    expect(localStorage.getItem(STORAGE_KEY)).toBe(null);
  });

  it('closes every other overlay when it opens', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();
    const other = { open: true };
    window.pwaMapOverlays.register('map-legend', {
      isOpen: () => other.open,
      close: () => {
        other.open = false;
      },
    });

    document.dispatchEvent(new CustomEvent('snowdesk:home-intro-requested'));

    expect(other.open).toBe(false);
  });
});
