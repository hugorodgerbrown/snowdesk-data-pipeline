/*
 * tests/js/test_map_sheet.js — Vitest unit tests for static/js/map_sheet.js
 * (SNOW-608).
 *
 * The three pieces `favourites.js` and `report.js` used to implement twice:
 * the sheet's open/close/teardown cycle, the shared failure toast, and the
 * "can this device queue a mutation?" guard.
 *
 * `map_sheet.js` is a browser IIFE with no exports — importing it for side
 * effects publishes `window.MapSheet` and wires its two delegated
 * `document` listeners. Because those listeners are wired once at import
 * time, and `attach()` adds a further set per call, each test builds its
 * fixture fresh and attaches inside the test rather than in a shared
 * `beforeEach`; a stale controller left listening on `document` would
 * otherwise act on the next test's markup.
 *
 * The auto-dismiss timer is driven with Vitest's fake timers rather than a
 * real six-second wait.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_sheet.js';

/** The toast markup `includes/_toast.html` renders for `#map-sheet-toast`. */
const TOAST_HTML = `
  <div id="map-sheet-toast" role="alert" data-testid="toast" data-kind="warning"
       data-overlay data-overlay-hide="class"
       class="hidden fixed bottom-4">
    <span data-toast-body></span>
    <button type="button" data-action="dismiss" aria-label="Dismiss">×</button>
  </div>
`;

/**
 * Build a sheet fixture plus its trigger, and attach a controller to it.
 *
 * @param {{ triggerSelector?: string, onBeforeOpen?: function(): void }} [options]
 * @returns {{ sheet: HTMLElement, controller: any }}
 */
function attachSheet(options) {
  document.body.innerHTML = `
    <button id="sheet-trigger" type="button">open</button>
    <div id="map"><div id="map-canvas">map</div></div>
    <div id="outside">elsewhere</div>
    <div id="test-sheet" data-overlay hidden tabindex="-1"></div>
    ${TOAST_HTML}
  `;
  const sheet = document.getElementById('test-sheet');
  const controller = window.MapSheet.attach(
    sheet,
    options || { triggerSelector: '#sheet-trigger' },
  );
  return { sheet, controller };
}

beforeEach(() => {
  document.body.innerHTML = '';
  delete window.PlacePicker;
  delete window.pwaDb;
  delete window.pwaMutationQueue;
  delete window.pwaOverlayBounds;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('attach — open', () => {
  it('reveals the sheet and moves focus to it', () => {
    const { sheet, controller } = attachSheet();

    controller.open();

    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(document.activeElement).toBe(sheet);
    expect(controller.isOpen()).toBe(true);
  });

  it('runs onBeforeOpen before the sheet is revealed', () => {
    let hiddenWhenCalled = null;
    const { sheet, controller } = attachSheet({
      triggerSelector: '#sheet-trigger',
      onBeforeOpen: () => {
        hiddenWhenCalled = sheet.hasAttribute('hidden');
      },
    });

    controller.open();

    // The favourites sheet uses this to close the anchored detail popup, so
    // it must land before the sheet is on screen, not after.
    expect(hiddenWhenCalled).toBe(true);
  });

  it('places the sheet inside the map area on the way in (SNOW-658)', () => {
    // The geometry itself belongs to static/js/map_overlay_bounds.js and is
    // covered in tests/js/test_map_overlay_sheet_position.js. What this
    // controller owns is making the call, once the sheet is unhidden — a
    // hidden element has no box, and the sheets it drives would otherwise
    // open over the season scrubber and the roundel column.
    const positionSheet = vi.fn();
    window.pwaOverlayBounds = { positionSheet };
    const { sheet, controller } = attachSheet();

    controller.open();

    expect(positionSheet).toHaveBeenCalledWith(sheet);
  });

  it('opens without the bounds module on the page', () => {
    // Every other page that includes a sheet surface loads it, but a missing
    // module must not stop the sheet opening — an unplaced sheet still falls
    // back to the partial's own corner.
    const { sheet, controller } = attachSheet();

    controller.open();

    expect(sheet.hasAttribute('hidden')).toBe(false);
  });
});

describe('attach — close and teardown', () => {
  it('hides the sheet, empties it and deactivates the place-picker', () => {
    const deactivate = vi.fn();
    window.PlacePicker = { deactivate };
    const { sheet, controller } = attachSheet();
    controller.open();
    sheet.innerHTML = '<p>form</p>';

    controller.close();

    expect(sheet.hasAttribute('hidden')).toBe(true);
    expect(sheet.innerHTML).toBe('');
    expect(deactivate).toHaveBeenCalledTimes(1);
  });

  it('runs the teardown on overlay:dismissed without re-hiding', () => {
    const deactivate = vi.fn();
    window.PlacePicker = { deactivate };
    const { sheet, controller } = attachSheet();
    controller.open();
    sheet.innerHTML = '<p>form</p>';

    // overlays.js has already hidden the element by the time it dispatches.
    sheet.setAttribute('hidden', '');
    sheet.dispatchEvent(
      new CustomEvent('overlay:dismissed', {
        bubbles: true,
        detail: { overlay: sheet },
      }),
    );

    expect(sheet.innerHTML).toBe('');
    expect(deactivate).toHaveBeenCalledTimes(1);
  });

  it('ignores an overlay:dismissed for a different overlay', () => {
    const { sheet, controller } = attachSheet();
    controller.open();
    sheet.innerHTML = '<p>form</p>';

    const other = document.getElementById('outside');
    other.dispatchEvent(
      new CustomEvent('overlay:dismissed', {
        bubbles: true,
        detail: { overlay: other },
      }),
    );

    expect(sheet.innerHTML).toBe('<p>form</p>');
  });

  it('closes on Escape only while open', () => {
    const { sheet, controller } = attachSheet();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(sheet.hasAttribute('hidden')).toBe(true);

    controller.open();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('ignores other keys', () => {
    const { sheet, controller } = attachSheet();
    controller.open();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(sheet.hasAttribute('hidden')).toBe(false);
  });
});

describe('attach — click-outside', () => {
  /**
   * @param {string} id - Element to click.
   */
  function clickOn(id) {
    document
      .getElementById(id)
      .dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }

  it('closes on a click elsewhere on the page', () => {
    const { sheet, controller } = attachSheet();
    controller.open();

    clickOn('outside');

    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('stays open for a click inside the sheet', () => {
    const { sheet, controller } = attachSheet();
    controller.open();
    sheet.innerHTML = '<button id="in-sheet" type="button">save</button>';

    clickOn('in-sheet');

    expect(sheet.hasAttribute('hidden')).toBe(false);
  });

  it('stays open for a click on the map or the trigger', () => {
    const { sheet, controller } = attachSheet();
    controller.open();

    // The map: the user is panning to position a pin.
    clickOn('map-canvas');
    expect(sheet.hasAttribute('hidden')).toBe(false);

    // The trigger: its own handler owns the open, so treating this as an
    // outside click would close what was just opened.
    clickOn('sheet-trigger');
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });
});

describe('toast', () => {
  it('reveals the shared toast with the message and hides it after 6s', () => {
    vi.useFakeTimers();
    document.body.innerHTML = TOAST_HTML;
    const toast = document.getElementById('map-sheet-toast');

    window.MapSheet.toast('Could not save this pin on this device.');

    expect(toast.classList.contains('hidden')).toBe(false);
    expect(toast.querySelector('[data-toast-body]').textContent).toBe(
      'Could not save this pin on this device.',
    );

    vi.advanceTimersByTime(5999);
    expect(toast.classList.contains('hidden')).toBe(false);
    vi.advanceTimersByTime(1);
    expect(toast.classList.contains('hidden')).toBe(true);
  });

  it('restarts the timer for a second message shown while the first is up', () => {
    vi.useFakeTimers();
    document.body.innerHTML = TOAST_HTML;
    const toast = document.getElementById('map-sheet-toast');

    window.MapSheet.toast('first');
    vi.advanceTimersByTime(5000);
    window.MapSheet.toast('second');

    // The second message gets its own full window — this is the case the
    // partial's own data-toast-timeout controller cannot cover, since a
    // text change fires no mutation it observes.
    vi.advanceTimersByTime(1000);
    expect(toast.classList.contains('hidden')).toBe(false);
    expect(toast.querySelector('[data-toast-body]').textContent).toBe('second');

    vi.advanceTimersByTime(5000);
    expect(toast.classList.contains('hidden')).toBe(true);
  });

  it('drops the pending timer when the toast is dismissed by hand', () => {
    vi.useFakeTimers();
    document.body.innerHTML = TOAST_HTML;
    const toast = document.getElementById('map-sheet-toast');

    window.MapSheet.toast('first');
    // overlays.js hides it and announces the dismissal.
    toast.classList.add('hidden');
    toast.dispatchEvent(
      new CustomEvent('overlay:dismissed', {
        bubbles: true,
        detail: { overlay: toast },
      }),
    );

    window.MapSheet.toast('second');
    vi.advanceTimersByTime(3000);

    // Without the cancel, the first message's timer would fire here and cut
    // the second message short.
    expect(toast.classList.contains('hidden')).toBe(false);
  });

  it('is a no-op when the partial is absent', () => {
    document.body.innerHTML = '<div id="test-sheet"></div>';

    expect(() => window.MapSheet.toast('nothing to show it in')).not.toThrow();
  });
});

describe('canQueueMutations', () => {
  it('is false with no mutation queue', () => {
    window.pwaDb = { isResetRequired: () => false };

    expect(window.MapSheet.canQueueMutations()).toBe(false);
  });

  it('is false with no IndexedDB wrapper', () => {
    window.pwaMutationQueue = { enqueue: vi.fn() };

    expect(window.MapSheet.canQueueMutations()).toBe(false);
  });

  it('is false in the terminal Reset-Required state', () => {
    window.pwaMutationQueue = { enqueue: vi.fn() };
    window.pwaDb = { isResetRequired: () => true };

    expect(window.MapSheet.canQueueMutations()).toBe(false);
  });

  it('is true when both are present and the DB is usable', () => {
    window.pwaMutationQueue = { enqueue: vi.fn() };
    window.pwaDb = { isResetRequired: () => false };

    expect(window.MapSheet.canQueueMutations()).toBe(true);
  });

  it('is true when the wrapper predates isResetRequired', () => {
    window.pwaMutationQueue = { enqueue: vi.fn() };
    window.pwaDb = {};

    expect(window.MapSheet.canQueueMutations()).toBe(true);
  });
});
