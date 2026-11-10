/*
 * tests/js/test_map_help_menu.js — Vitest unit tests for
 * static/js/map_help_menu.js (SNOW-1230).
 *
 * The "?" roundel opens a small menu now: show the welcome again, turn help
 * mode on, test 3D on this device (SNOW-1456), or go to the help pages. help mode hands the roundel's press to
 * this module as 'snowdesk:map-help-menu-requested'; these cases dispatch
 * that event directly and pin what each item asks for. SNOW-1486: on a
 * phone its header's × closes it, and so does a drag down on its handle
 * past the distance a Saved sheet closes at; a short one springs back.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function buildFixture() {
  document.body.innerHTML = `
    <button id="map-help-toggle" type="button">?</button>
    <div id="map-help-menu" hidden>
     <div class="map-tour-offer-card">
      <div data-help-menu-drag>
        <div data-sheet-handle aria-hidden="true"></div>
        <span>Help</span>
        <button type="button" data-action="help-menu-close">×</button>
      </div>
      <button type="button" data-help-menu-close>×</button>
      <button type="button" data-help-menu="welcome">Show me around again</button>
      <button type="button" data-help-menu="help-mode">What is everything?</button>
      <button type="button" data-help-menu="test-3d">Test 3D on this device</button>
      <button type="button" data-help-menu="relief-tuning">Tune the relief</button>
      <button type="button" data-help-menu="key">Map key</button>
      <a href="#help" data-help-menu="pages">Help pages</a>
     </div>
    </div>
    <button id="basemap-toggle" type="button">Layers</button>
  `;
}

const $ = (id) => document.getElementById(id);
const menu = () => $('map-help-menu');
const request = () => document.dispatchEvent(new CustomEvent('snowdesk:map-help-menu-requested'));

let added = [];
const realAdd = document.addEventListener.bind(document);

beforeEach(async () => {
  buildFixture();
  window.pwaTelemetry = { emit: vi.fn() };
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  document.addEventListener = (...args) => {
    added.push(args);
    realAdd(...args);
  };
  await import('../../static/js/map_help_menu.js');
  document.addEventListener = realAdd;
});

afterEach(() => {
  added.forEach((args) => document.removeEventListener(...args));
  added = [];
  delete window.pwaTelemetry;
});

describe('opening and closing', () => {
  it('is closed at first, and says what it controls', () => {
    expect(menu().hidden).toBe(true);
    expect($('map-help-toggle').getAttribute('aria-controls')).toBe('map-help-menu');
    expect($('map-help-toggle').getAttribute('aria-expanded')).toBe('false');
  });

  it('toggles on each press of the "?"', () => {
    request();
    expect(menu().hidden).toBe(false);
    expect($('map-help-toggle').getAttribute('aria-expanded')).toBe('true');
    request();
    expect(menu().hidden).toBe(true);
  });

  it('closes on its ×, and on Escape', () => {
    request();
    menu().querySelector('[data-help-menu-close]').click();
    expect(menu().hidden).toBe(true);

    request();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(menu().hidden).toBe(true);
  });

  it('closes when another overlay opens', () => {
    request();
    window.pwaMapOverlays.opening('basemap-menu');
    expect(menu().hidden).toBe(true);
  });
});

describe('the items', () => {
  it('"Show me around again" asks for the welcome card', () => {
    const welcome = vi.fn();
    document.addEventListener('snowdesk:map-welcome-requested', welcome);
    request();

    menu().querySelector('[data-help-menu="welcome"]').click();

    expect(welcome).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'welcome' });
    document.removeEventListener('snowdesk:map-welcome-requested', welcome);
  });

  it('"What is everything" asks for help mode', () => {
    const helpMode = vi.fn();
    document.addEventListener('snowdesk:map-help-mode-requested', helpMode);
    request();

    menu().querySelector('[data-help-menu="help-mode"]').click();

    expect(helpMode).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:map-help-mode-requested', helpMode);
  });

  it('"Test 3D on this device" asks for the 3D test\'s panel', () => {
    const test3d = vi.fn();
    document.addEventListener('snowdesk:map-3d-test-requested', test3d);
    request();

    menu().querySelector('[data-help-menu="test-3d"]').click();

    expect(test3d).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'test-3d' });
    document.removeEventListener('snowdesk:map-3d-test-requested', test3d);
  });

  it('"Tune the relief" asks for the staff tuning panel', () => {
    const tune = vi.fn();
    document.addEventListener('snowdesk:map-relief-tuning-requested', tune);
    request();

    menu().querySelector('[data-help-menu="relief-tuning"]').click();

    expect(tune).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    document.removeEventListener('snowdesk:map-relief-tuning-requested', tune);
  });

  it('the help pages link is recorded and closes the menu', () => {
    request();

    menu().querySelector('[data-help-menu="pages"]').click();

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'pages' });
    expect(menu().hidden).toBe(true);
  });
});

describe("the phone app's Help tab", () => {
  it('"Map key" opens the (i) card once the press has finished', async () => {
    window.pwaMapLegend = { open: vi.fn() };
    request();

    menu().querySelector('[data-help-menu="key"]').click();

    expect(menu().hidden).toBe(true);
    expect(window.pwaMapLegend.open).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 0));
    expect(window.pwaMapLegend.open).toHaveBeenCalledTimes(1);
    delete window.pwaMapLegend;
  });

  it('opens and closes the menu through pwaMapHelpMenu', () => {
    window.pwaMapHelpMenu.open();
    expect(window.pwaMapHelpMenu.isOpen()).toBe(true);
    window.pwaMapHelpMenu.close();
    expect(menu().hidden).toBe(true);
  });
});

/**
 * Dispatch a pointer event jsdom delivers whether or not it implements
 * PointerEvent.
 *
 * @param {Element} target
 * @param {string} type
 * @param {number} clientY
 * @param {number} timeStamp
 */
function pointer(target, type, clientY, timeStamp) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientY: { value: clientY },
    pointerId: { value: 1 },
    timeStamp: { value: timeStamp },
  });
  target.dispatchEvent(event);
}

describe('the phone card (SNOW-1486)', () => {
  /** Open the menu with its card 300 px tall. */
  function openAt300() {
    request();
    const card = menu().querySelector('.map-tour-offer-card');
    card.getBoundingClientRect = () => /** @type {DOMRect} */ ({ top: 400, bottom: 700, height: 300 });
    return { card, handle: menu().querySelector('[data-sheet-handle]') };
  }

  it("closes on the header's ×", () => {
    request();
    menu().querySelector('[data-action="help-menu-close"]').click();
    expect(menu().hidden).toBe(true);
  });

  it('closes on a drag down the handle past half its height', () => {
    const { card, handle } = openAt300();
    pointer(handle, 'pointerdown', 400, 0);
    pointer(handle, 'pointermove', 500, 500);
    expect(card.style.transform).toBe('translateY(100px)');
    pointer(handle, 'pointermove', 580, 1000);
    pointer(handle, 'pointerup', 580, 1000);
    expect(menu().hidden).toBe(true);
    expect(card.style.transform).toBe('');
  });

  it('springs back from a short drag', () => {
    const { card, handle } = openAt300();
    pointer(handle, 'pointerdown', 400, 0);
    pointer(handle, 'pointermove', 440, 500);
    pointer(handle, 'pointermove', 480, 1000);
    pointer(handle, 'pointerup', 480, 1000);
    expect(menu().hidden).toBe(false);
    expect(card.style.transform).toBe('');
  });

  it('closes on a short fast flick down, as a sheet does', async () => {
    await import('../../static/js/sheet_detents_core.js');
    try {
      const { handle } = openAt300();
      pointer(handle, 'pointerdown', 400, 0);
      pointer(handle, 'pointermove', 410, 10);
      pointer(handle, 'pointermove', 500, 50);
      pointer(handle, 'pointerup', 500, 50);
      expect(menu().hidden).toBe(true);
    } finally {
      delete window.pwaSheetDetentsCore;
    }
  });

  it('ignores a drag that starts on the menu rows', () => {
    openAt300();
    const row = menu().querySelector('[data-help-menu="welcome"]');
    pointer(row, 'pointerdown', 400, 0);
    pointer(row, 'pointermove', 700, 500);
    pointer(row, 'pointerup', 700, 500);
    expect(menu().hidden).toBe(false);
  });
});
