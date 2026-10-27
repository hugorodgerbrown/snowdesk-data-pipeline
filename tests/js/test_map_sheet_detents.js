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

  it('re-reads the initial detent at every open', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'peek');
    sheet.setAttribute('data-detent-initial', 'half');
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

  it('a tap on a draggable header steps up too (2026-10-19)', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'peek');
    controller.open();
    const header = document.createElement('div');
    header.setAttribute('data-sheet-drag', '');
    header.innerHTML = '<span>Region name</span><button type="button">Pin</button>';
    sheet.appendChild(header);
    /** @type {HTMLElement} */ (header.querySelector('span')).click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    // A control in the header keeps its own press.
    /** @type {HTMLElement} */ (header.querySelector('button')).click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('a tap on content outside a draggable header does nothing', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'peek');
    controller.open();
    const body = document.createElement('p');
    sheet.appendChild(body);
    body.click();
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

  it('does not step the detent from the click a header drag ends with', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    controller.open();
    const header = document.createElement('div');
    header.setAttribute('data-sheet-drag', '');
    sheet.appendChild(header);
    pointer(header, 'pointerdown', 500, 0);
    pointer(header, 'pointermove', 480, 500);
    pointer(header, 'pointerup', 480, 500);
    header.click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
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

/**
 * Dispatch a pointer event with both axes and a pointer type, as a finger
 * on the sheet's body sends it.
 *
 * @param {Element} target
 * @param {string} type
 * @param {{ x?: number, y: number, t: number, kind?: string }} at
 */
function finger(target, type, { x = 100, y, t, kind = 'touch' }) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientX: { value: x },
    clientY: { value: y },
    pointerId: { value: 1 },
    pointerType: { value: kind },
    timeStamp: { value: t },
  });
  target.dispatchEvent(event);
}

/**
 * Dispatch a cancelable touchmove at one point.
 *
 * @param {Element} target
 * @param {number} x
 * @param {number} y
 * @returns {Event}
 */
function touchmove(target, x, y) {
  const event = new Event('touchmove', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'touches', { value: [{ clientX: x, clientY: y }] });
  target.dispatchEvent(event);
  return event;
}

/**
 * Make an element a scroller of `content` px in a `box` px window,
 * scrolled to `top`.
 *
 * @param {HTMLElement} el
 * @param {{ box: number, content: number, top: number }} size
 */
function stubScroller(el, { box, content, top }) {
  el.style.overflowY = 'auto';
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: box });
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: content });
  el.scrollTop = top;
  Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: top });
}

describe('dragging by the body (Hugo, 25 Oct 2026)', () => {
  /**
   * @param {string} detents
   * @param {string} initial
   */
  function withBody(detents, initial) {
    const fixture = attachSheet(detents, initial);
    const body = document.createElement('p');
    body.textContent = 'Danger level 3';
    fixture.sheet.appendChild(body);
    fixture.controller.open();
    return { ...fixture, body };
  }

  it('a swipe up anywhere on the body grows the sheet', () => {
    const { sheet, body } = withBody('peek,half,full', 'half');
    finger(body, 'pointerdown', { y: 500, t: 0 });
    finger(body, 'pointermove', { y: 350, t: 500 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(true);
    finger(body, 'pointermove', { y: 200, t: 1000 });
    finger(body, 'pointerup', { y: 200, t: 1000 });
    expect(sheet.getAttribute('data-detent')).toBe('full');
  });

  it('a swipe down on the body closes from the lowest detent', () => {
    const { sheet, body } = withBody('half,full', 'half');
    finger(body, 'pointerdown', { y: 400, t: 0 });
    finger(body, 'pointermove', { y: 700, t: 500 });
    finger(body, 'pointerup', { y: 700, t: 500 });
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('waits for more than the handle’s slop before deciding', () => {
    const { sheet, body } = withBody('peek,half,full', 'half');
    finger(body, 'pointerdown', { y: 500, t: 0 });
    finger(body, 'pointermove', { y: 494, t: 10 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
  });

  it('leaves a sideways swipe to the content', () => {
    const { sheet, body } = withBody('peek,half,full', 'half');
    finger(body, 'pointerdown', { x: 100, y: 500, t: 0 });
    finger(body, 'pointermove', { x: 160, y: 480, t: 100 });
    finger(body, 'pointermove', { x: 220, y: 300, t: 200 });
    finger(body, 'pointerup', { x: 220, y: 300, t: 200 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('a mouse does not drag by the body', () => {
    const { sheet, body } = withBody('peek,half,full', 'half');
    finger(body, 'pointerdown', { y: 500, t: 0, kind: 'mouse' });
    finger(body, 'pointermove', { y: 300, t: 100, kind: 'mouse' });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
  });

  it('a field, or content marked data-sheet-no-drag, keeps its press', () => {
    const { sheet } = withBody('peek,half,full', 'half');
    const field = document.createElement('input');
    const track = document.createElement('div');
    track.setAttribute('data-sheet-no-drag', '');
    track.appendChild(document.createElement('span'));
    sheet.append(field, track);
    for (const target of [field, /** @type {Element} */ (track.firstChild)]) {
      finger(target, 'pointerdown', { y: 500, t: 0 });
      finger(target, 'pointermove', { y: 300, t: 100 });
      expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
      finger(target, 'pointerup', { y: 300, t: 100 });
    }
  });

  it('at full, content scrolled down scrolls rather than drags', () => {
    const { sheet, body } = withBody('peek,half,full', 'full');
    stubScroller(sheet, { box: 600, content: 1200, top: 120 });
    finger(body, 'pointerdown', { y: 300, t: 0 });
    finger(body, 'pointermove', { y: 500, t: 100 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
  });

  it('at full, a pull down from the top of the scroll takes the sheet down', () => {
    const { sheet, body } = withBody('peek,half,full', 'full');
    stubScroller(sheet, { box: 600, content: 1200, top: 0 });
    finger(body, 'pointerdown', { y: 300, t: 0 });
    // The pull is cancelled before the slop, so no overscroll starts.
    expect(touchmove(body, 100, 304).defaultPrevented).toBe(true);
    finger(body, 'pointermove', { y: 400, t: 500 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(true);
    expect(touchmove(body, 100, 400).defaultPrevented).toBe(true);
    finger(body, 'pointermove', { y: 450, t: 1000 });
    finger(body, 'pointerup', { y: 450, t: 1000 });
    expect(sheet.getAttribute('data-detent')).not.toBe('full');
  });

  it('at full, a push up over more content scrolls it', () => {
    const { sheet, body } = withBody('peek,half,full', 'full');
    stubScroller(sheet, { box: 600, content: 1200, top: 0 });
    finger(body, 'pointerdown', { y: 500, t: 0 });
    expect(touchmove(body, 100, 496).defaultPrevented).toBe(false);
    finger(body, 'pointermove', { y: 400, t: 100 });
    expect(sheet.hasAttribute('data-sheet-dragging')).toBe(false);
    expect(touchmove(body, 100, 400).defaultPrevented).toBe(false);
  });

  it('a drag that lifts over a link does not follow it', () => {
    const { sheet } = withBody('peek,half,full', 'half');
    const link = document.createElement('a');
    link.href = '#resort';
    sheet.appendChild(link);
    const followed = vi.fn();
    link.addEventListener('click', followed);
    finger(link, 'pointerdown', { y: 500, t: 0 });
    finger(link, 'pointermove', { y: 300, t: 100 });
    finger(link, 'pointerup', { y: 300, t: 100 });
    link.click();
    expect(followed).not.toHaveBeenCalled();
    // The next tap is a tap.
    link.click();
    expect(followed).toHaveBeenCalledTimes(1);
  });

  it('a tap on a link in the body still follows it', () => {
    const { sheet } = withBody('peek,half,full', 'half');
    const link = document.createElement('a');
    link.href = '#resort';
    sheet.appendChild(link);
    const followed = vi.fn();
    link.addEventListener('click', followed);
    finger(link, 'pointerdown', { y: 500, t: 0 });
    finger(link, 'pointerup', { y: 500, t: 50 });
    link.click();
    expect(followed).toHaveBeenCalledTimes(1);
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

  it('opening one closes the other at a desktop width too (2026-10-21)', () => {
    // From 640px the detent sheets share the left column under the search
    // pill, so they would stack there as they do on a phone's foot.
    window.matchMedia = /** @type {any} */ ((query) => ({ matches: query.includes('min-width') }));
    try {
      const t = twoSheets();
      t.ca.open();
      t.cb.open();
      expect(t.a.hasAttribute('hidden')).toBe(true);
      expect(t.b.hasAttribute('hidden')).toBe(false);
    } finally {
      // @ts-ignore jsdom has none; the binding reads its absence as a phone.
      delete window.matchMedia;
    }
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

  it('skips a half marker with no box for the first shown one', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    // A hidden alternative (an unchosen forecast point's pane) comes first.
    childEndingAt(sheet, 0, 'data-sheet-half');
    childEndingAt(sheet, 180, 'data-sheet-half');
    childEndingAt(sheet, 300);
    controller.open();
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('196px');
  });

  it('fits the content when every half marker is hidden', () => {
    const { sheet, controller } = attachSheet('peek,half,full', 'half');
    // The chosen point has no forecast: only hidden panes carry markers.
    childEndingAt(sheet, 0, 'data-sheet-half="fit"');
    childEndingAt(sheet, 200);
    controller.open();
    expect(sheet.style.getPropertyValue('--sheet-half-height')).toBe('216px');
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

describe('handing the height over to the next panel (Hugo, 25 Oct 2026)', () => {
  /**
   * Two sheets that open at PEEK, each with a handle that steps it up.
   *
   * @param {string} [detentsB] What the second sheet declares.
   */
  function twoPanels(detentsB = 'peek,half,full') {
    const handle = '<button type="button" data-sheet-handle aria-label="Resize panel"></button>';
    document.body.innerHTML = `
      <div id="map"></div>
      <div id="sheet-a" data-overlay hidden tabindex="-1" data-detents="peek,half,full" data-detent-initial="peek">${handle}</div>
      <div id="sheet-b" data-overlay hidden tabindex="-1" data-detents="${detentsB}" data-detent-initial="peek">${handle}</div>
    `;
    const a = /** @type {HTMLElement} */ (document.getElementById('sheet-a'));
    const b = /** @type {HTMLElement} */ (document.getElementById('sheet-b'));
    stubHeight(a, 400);
    stubHeight(b, 400);
    const ca = window.MapSheet.attach(a);
    const cb = window.MapSheet.attach(b);
    ca.open();
    // The handle steps PEEK → HALF → FULL.
    a.querySelector('[data-sheet-handle]').click();
    a.querySelector('[data-sheet-handle]').click();
    return { a, b, cb };
  }

  it('opens the next panel at FULL when the reader was at FULL', () => {
    const t = twoPanels();
    expect(t.a.getAttribute('data-detent')).toBe('full');
    window.MapSheet.handOff();
    t.cb.open();
    expect(t.b.getAttribute('data-detent')).toBe('full');
  });

  it('opens at the panel’s own initial detent without a hand-off', () => {
    const t = twoPanels();
    t.cb.open();
    expect(t.b.getAttribute('data-detent')).toBe('peek');
  });

  it('takes a hand-off once', () => {
    const t = twoPanels();
    window.MapSheet.handOff();
    t.cb.open();
    t.cb.close();
    t.cb.open();
    expect(t.b.getAttribute('data-detent')).toBe('peek');
  });

  it('drops a hand-off nothing opened for in time', () => {
    vi.useFakeTimers();
    try {
      const t = twoPanels();
      window.MapSheet.handOff();
      vi.advanceTimersByTime(6000);
      t.cb.open();
      expect(t.b.getAttribute('data-detent')).toBe('peek');
    } finally {
      vi.useRealTimers();
    }
  });

  it('falls back to the initial detent when the next panel has no such height', () => {
    const t = twoPanels('peek,half');
    window.MapSheet.handOff();
    t.cb.open();
    expect(t.b.getAttribute('data-detent')).toBe('peek');
  });
});
