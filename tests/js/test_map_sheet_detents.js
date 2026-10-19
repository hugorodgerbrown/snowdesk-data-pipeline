/*
 * tests/js/test_map_sheet_detents.js — the phone detents map_sheet.js binds
 * to a sheet that declares them (SNOW-1097).
 *
 * The rules (nearest detent, flick, close threshold) are covered in
 * test_sheet_detents_core.js; this file covers the binding: a sheet opens at
 * its initial detent, the handle steps through the declared detents, a drag
 * settles or closes, the handle survives a content swap, content can narrow
 * the detents in force (`data-sheet-detents`), the map camera is padded
 * (and not while it is moving), the root is marked while the sheet is open,
 * nothing publishes a lift for the furniture (it is covered, not moved), and
 * a sheet with no detents is left exactly as it was, and half and full fit
 * their content (SNOW-1099).
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
  it('pads the camera by the sheet height and marks the root, then clears both', () => {
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
    // The furniture is covered, not moved: nothing publishes a lift.
    expect(document.documentElement.style.getPropertyValue('--map-sheet-lift')).toBe('');

    // A fit that brings its own padding is undone when the camera rests.
    padding = { top: 60, left: 40, right: 40, bottom: 40 };
    listeners.moveend.forEach((fn) => fn());
    expect(padding.bottom).toBe(384);

    expect(document.documentElement.getAttribute('data-map-sheet')).toBe('half');

    controller.close();
    expect(padding.bottom).toBe(0);
    expect(document.documentElement.hasAttribute('data-map-sheet')).toBe(false);
  });
});

describe('two sheets on one map (2026-10-19)', () => {
  /**
   * Two detent sheets in one document, each attached, plus a MAP whose
   * jumpTo ends a move synchronously — as MapLibre's does — so a watcher
   * that answers its own moveend would recurse here as it did on a phone.
   */
  function twoSheets() {
    document.body.innerHTML = `
      <div id="map"></div>
      <div id="sheet-a" data-overlay hidden tabindex="-1" data-detents="peek,half" data-detent-initial="half"></div>
      <div id="sheet-b" data-overlay hidden tabindex="-1" data-detents="peek,half" data-detent-initial="half"></div>
    `;
    const a = /** @type {HTMLElement} */ (document.getElementById('sheet-a'));
    const b = /** @type {HTMLElement} */ (document.getElementById('sheet-b'));
    stubHeight(a, 400);
    stubHeight(b, 600);
    const listeners = { moveend: [] };
    let padding = { top: 0, bottom: 0, left: 0, right: 0 };
    let depth = 0;
    let maxDepth = 0;
    globalThis.MAP = {
      getPadding: () => padding,
      setPadding: (next) => {
        padding = next;
      },
      getContainer: () => ({ clientWidth: 400, clientHeight: 768 }),
      unproject: ([x, y]) => ({ x, y }),
      on: (name, fn) => listeners[name].push(fn),
      jumpTo: (options) => {
        padding = options.padding;
        depth += 1;
        maxDepth = Math.max(maxDepth, depth);
        if (depth < 50) listeners.moveend.forEach((fn) => fn());
        depth -= 1;
      },
    };
    return {
      a,
      b,
      ca: window.MapSheet.attach(a),
      cb: window.MapSheet.attach(b),
      padding: () => padding,
      moveend: () => listeners.moveend.forEach((fn) => fn()),
      maxDepth: () => maxDepth,
    };
  }

  it('opening one closes the other, so they never stack', () => {
    const t = twoSheets();
    t.ca.open();
    t.cb.open();
    expect(t.a.hasAttribute('hidden')).toBe(true);
    expect(t.b.hasAttribute('hidden')).toBe(false);
  });

  it('a camera move does not set the sheets fighting over the padding', () => {
    const t = twoSheets();
    t.ca.open();
    t.ca.close();
    t.cb.open();
    t.moveend();
    // One jumpTo answers a move, and its own moveend finds nothing to do.
    expect(t.maxDepth()).toBeLessThan(3);
    expect(t.padding().bottom).toBe(384);
  });
});

describe('detents the content allows', () => {
  it('skips a detent the content does not allow', async () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'peek');
    sheet.insertAdjacentHTML('beforeend', '<div data-sheet-detents="peek,half"></div>');
    controller.open();
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('peek');
  });

  it('brings the sheet down when new content drops the detent it rests at', async () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'full');
    controller.open();
    expect(sheet.getAttribute('data-detent')).toBe('full');
    sheet.innerHTML = '<div data-sheet-detents="peek,half"></div>';
    await Promise.resolve();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    expect(handle).not.toBeNull();
  });

  it('falls back to the declared detents when the content allows none of them', () => {
    const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
    sheet.insertAdjacentHTML('beforeend', '<div data-sheet-detents="bogus"></div>');
    controller.open();
    handle.click();
    expect(sheet.getAttribute('data-detent')).toBe('full');
  });
});

describe('a camera in flight', () => {
  it('defers the padding until the camera rests, so a fly-to is not cancelled', () => {
    const jumpTo = vi.fn();
    const listeners = { moveend: [] };
    let padding = { top: 0, bottom: 0, left: 0, right: 0 };
    let moving = true;
    globalThis.MAP = {
      getPadding: () => padding,
      setPadding: (next) => {
        padding = next;
      },
      getContainer: () => ({ clientWidth: 400, clientHeight: 768 }),
      unproject: ([x, y]) => ({ x, y }),
      isMoving: () => moving,
      on: (name, fn) => listeners[name].push(fn),
      jumpTo: (options) => {
        jumpTo(options);
        padding = options.padding;
      },
    };
    const { controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    expect(jumpTo).not.toHaveBeenCalled();

    moving = false;
    listeners.moveend.forEach((fn) => fn());
    expect(padding.bottom).toBe(384);

    // A close while a flight runs is cleared when it lands.
    moving = true;
    controller.close();
    expect(padding.bottom).toBe(384);
    moving = false;
    listeners.moveend.forEach((fn) => fn());
    expect(padding.bottom).toBe(0);
  });
});

/**
 * Append a child that pretends to end `bottom` px below the sheet's top.
 *
 * @param {HTMLElement} sheet
 * @param {number} bottom
 * @param {string} [attrs]
 * @returns {HTMLElement}
 */
function childEndingAt(sheet, bottom, attrs = '') {
  sheet.insertAdjacentHTML('beforeend', `<div ${attrs}></div>`);
  const child = /** @type {HTMLElement} */ (sheet.lastElementChild);
  // The stubbed sheet's top sits 400 px above jsdom's 768 px foot.
  child.getBoundingClientRect = () =>
    /** @type {DOMRect} */ ({ top: 368, bottom: 368 + bottom, height: bottom, left: 0, right: 0, width: 0 });
  return child;
}

describe('fitting the content (SNOW-1099)', () => {
  it('stops half under the marked element', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    childEndingAt(sheet, 150, 'data-sheet-half');
    childEndingAt(sheet, 300);
    controller.open();
    // 150 px to the marker's foot, plus the 16 px gap below it.
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('166px');
  });

  it('stops half at the end of the content when nothing is marked', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    childEndingAt(sheet, 120);
    childEndingAt(sheet, 200);
    controller.open();
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('216px');
  });

  it('never takes half above half the screen', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    childEndingAt(sheet, 600, 'data-sheet-half');
    controller.open();
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('384px');
  });

  it('lets a "fit" half marker run past half the screen, up to full', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    childEndingAt(sheet, 500, 'data-sheet-half="fit"');
    controller.open();
    // Past the 384 px half-screen cap: 500 px plus the 16 px gap.
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('516px');
  });

  it('stops full at the end of the content, past the half marker', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'full');
    childEndingAt(sheet, 150, 'data-sheet-half');
    childEndingAt(sheet, 500);
    controller.open();
    expect(sheet.style.getPropertyValue('--sheet-full-height')).toBe('516px');
  });

  it('re-fits the resting detent when content changes height in place', () => {
    /** @type {Array<function(): void>} */
    const callbacks = [];
    const observed = new Set();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        /** @param {function(): void} fn */
        constructor(fn) {
          callbacks.push(fn);
        }
        /** @param {Element} el */
        observe(el) {
          observed.add(el);
        }
        disconnect() {
          observed.clear();
        }
      },
    );
    try {
      const { sheet, controller } = attachSheet('peek,half,full', 'half');
      const header = childEndingAt(sheet, 100);
      controller.open();
      expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('116px');

      // A child added before attach is observed; the header grows by 40 px.
      header.getBoundingClientRect = () =>
        /** @type {DOMRect} */ ({ top: 368, bottom: 508, height: 140, left: 0, right: 0, width: 0 });
      callbacks.forEach((fn) => fn());
      expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('156px');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('holds the drag height while a finger is on the sheet', () => {
    /** @type {Array<function(): void>} */
    const callbacks = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        /** @param {function(): void} fn */
        constructor(fn) {
          callbacks.push(fn);
        }
        observe() {}
        disconnect() {}
      },
    );
    try {
      const { sheet, handle, controller } = attachSheet('peek,half,full', 'half');
      const header = childEndingAt(sheet, 100);
      controller.open();
      pointer(handle, 'pointerdown', 500, 0);
      pointer(handle, 'pointermove', 450, 100);
      header.getBoundingClientRect = () =>
        /** @type {DOMRect} */ ({ top: 368, bottom: 508, height: 140, left: 0, right: 0, width: 0 });
      callbacks.forEach((fn) => fn());
      expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('116px');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('clears the fitted heights on close', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    childEndingAt(sheet, 150);
    controller.open();
    controller.close();
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('');
    expect(sheet.style.getPropertyValue('--sheet-full-height')).toBe('');
  });
});
