/*
 * tests/js/test_map_sheet_detents.js — the phone detents map_sheet.js binds
 * to a sheet that declares them (SNOW-1097).
 *
 * The rules (nearest detent, flick, close threshold) are covered in
 * test_sheet_detents_core.js; this file covers the binding: a sheet opens at
 * its initial detent, the handle steps through the declared detents, a drag
 * settles or closes, the handle survives a content swap, the map camera is
 * padded and the roundel column lifted, and a sheet with no detents is left
 * exactly as it was.
 *
 * jsdom lays nothing out, so every height the binding reads is stubbed on
 * the element (`getBoundingClientRect`) and jsdom's own `innerHeight` (768)
 * stands in for the phone's. `matchMedia` is absent in jsdom, which the
 * binding reads as a phone — the only width at which detents do anything.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/sheet_detents_core.js';
import '../../static/js/map_sheet.js';

/**
 * Build a sheet fixture and attach a controller to it.
 *
 * @param {string|null} detents The `data-detents` value, or null for none.
 * @param {string} [initial]
 * @returns {{ sheet: HTMLElement, handle: HTMLElement|null, controller: any }}
 */
function attachSheet(detents, initial = '') {
  const attrs = detents ? `data-detents="${detents}" data-detent-initial="${initial}"` : '';
  const handle = detents ? '<button type="button" data-sheet-handle aria-label="Resize panel"></button>' : '';
  document.body.innerHTML = `
    <div id="map"></div>
    <div id="test-sheet" data-overlay hidden tabindex="-1" ${attrs}>${handle}</div>
  `;
  const sheet = /** @type {HTMLElement} */ (document.getElementById('test-sheet'));
  stubHeight(sheet, 400);
  const controller = window.MapSheet.attach(sheet);
  return { sheet, handle: sheet.querySelector('[data-sheet-handle]'), controller };
}

/**
 * Pretend the sheet is `height` px tall, docked at the viewport's foot.
 *
 * @param {HTMLElement} el
 * @param {number} height
 */
function stubHeight(el, height) {
  el.getBoundingClientRect = () =>
    /** @type {DOMRect} */ ({ top: 768 - height, bottom: 768, height, left: 0, right: 0, width: 0 });
}

/**
 * Dispatch a pointer event jsdom will deliver whether or not it implements
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

beforeEach(() => {
  document.body.innerHTML = '';
  document.documentElement.style.removeProperty('--map-sheet-lift');
  delete globalThis.MAP;
});

afterEach(() => {
  delete globalThis.MAP;
});

describe('opening', () => {
  it('rests at the initial detent', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('rests at the lowest declared detent with no initial', () => {
    const { sheet, controller } = attachSheet('half,full');
    controller.open();
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('writes the peek height from the marked summary', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'peek');
    const summary = document.createElement('div');
    summary.setAttribute('data-sheet-peek', '');
    summary.getBoundingClientRect = () => /** @type {DOMRect} */ ({ bottom: 768 - 400 + 90 });
    sheet.appendChild(summary);
    controller.open();
    // 90 px to the summary's foot, plus the 16 px gap below it.
    expect(sheet.style.getPropertyValue('--sheet-peek-height')).toBe('106px');
  });

  it('leaves a sheet with no detents untouched', () => {
    const { sheet, controller } = attachSheet(null);
    controller.open();
    expect(sheet.hasAttribute('data-detent')).toBe(false);
    expect(window.MapSheet.bindDetents(sheet)).toBeNull();
  });
});

describe('the handle', () => {
  it('steps up through the declared detents and wraps', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'peek');
    controller.open();
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('full');
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('peek');
  });

  it('is put back after a content swap removes it', async () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'peek');
    controller.open();
    sheet.innerHTML = '<p>new content</p>';
    await Promise.resolve();
    expect(sheet.firstElementChild?.hasAttribute('data-sheet-handle')).toBe(true);
    expect(sheet.textContent).toContain('new content');
  });
});

describe('dragging', () => {
  it('settles a drag up at the nearest detent', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    // From 400 px, a slow 300 px drag up ends at 700: nearest full.
    pointer(handle, 'pointerdown', 500, 0);
    pointer(handle, 'pointermove', 350, 500);
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(true);
    pointer(handle, 'pointermove', 200, 1000);
    pointer(handle, 'pointerup', 200, 1000);
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('full');
  });

  it('closes the sheet on a drag below the lowest detent', () => {
    const { sheet, handle, controller } = attachSheet('half,full', 'half');
    controller.open();
    pointer(handle, 'pointerdown', 400, 0);
    pointer(handle, 'pointermove', 700, 500);
    pointer(handle, 'pointerup', 700, 500);
    expect(sheet.hasAttribute('hidden')).toBe(true);
    expect(sheet.hasAttribute('data-detent')).toBe(false);
  });

  it('does not also step the detent from the click a drag ends with', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    pointer(handle, 'pointerdown', 500, 0);
    pointer(handle, 'pointermove', 480, 500);
    pointer(handle, 'pointerup', 480, 500);
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('leaves the handle live after a drag by a header', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    const header = document.createElement('div');
    header.setAttribute('data-sheet-drag', '');
    sheet.appendChild(header);
    pointer(header, 'pointerdown', 500, 0);
    pointer(header, 'pointermove', 480, 500);
    pointer(header, 'pointerup', 480, 500);
    pointer(handle, 'pointerdown', 300, 2000);
    pointer(handle, 'pointerup', 300, 2000);
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('full');
  });

  it('ignores a pointer that never moves past the slop', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    pointer(handle, 'pointerdown', 500, 0);
    pointer(handle, 'pointermove', 498, 10);
    pointer(handle, 'pointerup', 498, 10);
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });
});

describe('the map around the sheet', () => {
  it('pads the camera and lifts the roundels by the sheet height, then clears both', () => {
    const jumpTo = vi.fn();
    const listeners = { moveend: [] };
    let padding = { top: 0, bottom: 0, left: 0, right: 0 };
    globalThis.MAP = {
      getPadding: () => padding,
      setPadding: (next) => {
        padding = next;
      },
      getContainer: () => ({ clientWidth: 400, clientHeight: 768 }),
      unproject: ([x, y]) => ({ x, y }),
      on: (name, fn) => listeners[name].push(fn),
      jumpTo: (options) => {
        jumpTo(options);
        padding = options.padding;
      },
    };
    const { controller } = attachSheet('peek,half,full', 'half');
    controller.open();

    // Half is 384 px of jsdom's 768, docked flush to the foot.
    expect(jumpTo).toHaveBeenCalledWith({
      center: { x: 200, y: 192 },
      padding: { top: 0, left: 0, right: 0, bottom: 384 },
    });
    expect(document.documentElement.style.getPropertyValue('--map-sheet-lift')).toBe('384px');

    // A fit that brings its own padding is undone when the camera rests.
    padding = { top: 60, left: 40, right: 40, bottom: 40 };
    listeners.moveend.forEach((fn) => fn());
    expect(padding.bottom).toBe(384);

    expect(document.documentElement.getAttribute('data-map-sheet')).toBe('half');

    controller.close();
    expect(padding.bottom).toBe(0);
    expect(document.documentElement.hasAttribute('data-map-sheet')).toBe(false);
    expect(document.documentElement.style.getPropertyValue('--map-sheet-lift')).toBe('0px');
  });
});

describe('the roundel column', () => {
  it('stays behind a sheet it would not fit above', () => {
    document.body.insertAdjacentHTML('beforeend', '<div id="map-controls-br"></div>');
    const controls = document.getElementById('map-controls-br');
    // 400 px of roundels cannot fit above a 384 px half sheet in 768 px.
    controls.getBoundingClientRect = () => /** @type {DOMRect} */ ({ height: 400 });
    const { controller } = attachSheet('peek,half,full', 'half');
    document.body.appendChild(controls);
    controller.open();
    expect(document.documentElement.style.getPropertyValue('--map-sheet-lift')).toBe('0px');
  });
});
