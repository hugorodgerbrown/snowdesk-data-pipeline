/*
 * tests/js/test_season_sheet.js — the bulletin page's season sheet
 * (SNOW-621).
 *
 * The two pieces with exact numeric behaviour, which is why the block was
 * extracted from `bulletin.html` at all:
 *
 *   - the touch threshold that separates a tap on the backdrop (dismiss)
 *     from a scroll that started on it (do not), and
 *   - the first-open latch that decides whether opening fetches the grid
 *     or scrolls it.
 *
 * Both are easy to break and hard to notice — a wrong threshold closes the
 * sheet under a user trying to scroll it, and a broken latch either fetches
 * the grid on every open or never scrolls to the selected day.
 *
 * Harness note
 * ------------
 * `season_sheet.js` captures its two elements at import time and returns
 * early without them, so the fixture is built before the dynamic import and
 * every test reuses that one module instance. `beforeEach` resets the
 * mutable state the module reads back out of the DOM (`data-season-sheet`,
 * `data-loaded`) rather than re-importing.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

document.body.innerHTML = `
  <button data-season-trigger type="button" aria-expanded="false">Season</button>
  <div data-season-sheet="closed">
    <div data-season-scroll>
      <div id="season-grid" data-selected-date="2026-01-15"></div>
    </div>
    <button data-season-dismiss type="button">&times;</button>
    <div data-season-dismiss id="backdrop"></div>
  </div>
`;

const trigger = document.querySelector('[data-season-trigger]');
const sheet = document.querySelector('[data-season-sheet]');
const backdrop = document.getElementById('backdrop');
const closeButton = document.querySelector('button[data-season-dismiss]');
const grid = document.getElementById('season-grid');

const htmxTrigger = vi.fn();
window.htmx = { trigger: htmxTrigger };

await import('../../static/js/season_sheet.js');

/**
 * Fire a touch that starts at (0, 0) and ends at (dx, dy).
 *
 * @param {number} dx
 * @param {number} dy
 */
function touchDrag(dx, dy) {
  backdrop.dispatchEvent(
    new (class extends Event {
      constructor() {
        super('touchstart', { bubbles: true });
        this.touches = [{ clientX: 0, clientY: 0 }];
      }
    })(),
  );
  backdrop.dispatchEvent(
    new (class extends Event {
      constructor() {
        super('touchend', { bubbles: true, cancelable: true });
        this.changedTouches = [{ clientX: dx, clientY: dy }];
      }
    })(),
  );
}

/** @returns {string} */
function state() {
  return sheet.dataset.seasonSheet;
}

beforeEach(() => {
  sheet.dataset.seasonSheet = 'closed';
  trigger.setAttribute('aria-expanded', 'false');
  delete grid.dataset.loaded;
  htmxTrigger.mockClear();
});

describe('open and close', () => {
  it('toggles on the trigger and mirrors the state into aria-expanded', () => {
    trigger.click();
    expect(state()).toBe('open');
    expect(trigger.getAttribute('aria-expanded')).toBe('true');

    trigger.click();
    expect(state()).toBe('closed');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on the ✕ button', () => {
    trigger.click();

    closeButton.click();

    expect(state()).toBe('closed');
  });

  it('closes on Escape only while open', () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(state()).toBe('closed');

    trigger.click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(state()).toBe('closed');
  });
});

describe('the first-open latch', () => {
  it('fetches the grid on the first open, not on later ones', () => {
    trigger.click();

    expect(htmxTrigger).toHaveBeenCalledTimes(1);
    expect(htmxTrigger).toHaveBeenCalledWith(grid, 'snowdesk:load');
    expect(grid.dataset.loaded).toBe('true');

    trigger.click(); // close
    trigger.click(); // open again

    // The grid is already in the DOM; re-fetching it on every open would
    // be a request per tap of a control users open repeatedly.
    expect(htmxTrigger).toHaveBeenCalledTimes(1);
  });

  it('latches before firing, so a double-tap cannot start two fetches', () => {
    // The latch is set BEFORE htmx.trigger, so a handler that re-enters
    // synchronously still sees it.
    htmxTrigger.mockImplementationOnce(() => {
      expect(grid.dataset.loaded).toBe('true');
    });

    trigger.click();

    expect(htmxTrigger).toHaveBeenCalledTimes(1);
  });

  it('survives a close — the latch is not reset by closing', () => {
    trigger.click();
    trigger.click();
    expect(grid.dataset.loaded).toBe('true');

    trigger.click();

    expect(htmxTrigger).toHaveBeenCalledTimes(1);
  });

  it('does not fetch when htmx is unavailable, but still latches', () => {
    const saved = window.htmx;
    delete window.htmx;

    trigger.click();

    // Latching regardless is deliberate: htmx being absent means the grid
    // is never coming, and un-latching would retry forever.
    expect(grid.dataset.loaded).toBe('true');
    window.htmx = saved;
  });
});

describe('the backdrop touch threshold', () => {
  it('dismisses a tap that barely moved', () => {
    trigger.click();

    touchDrag(1, 1);

    expect(state()).toBe('closed');
  });

  it('does not dismiss a scroll', () => {
    trigger.click();

    // A finger travelling 20px vertically is scrolling the season grid,
    // not tapping the backdrop. Closing here yanks the sheet out from
    // under the user mid-read.
    touchDrag(0, 20);

    expect(state()).toBe('open');
  });

  it('treats 8px as the boundary — inside dismisses, on it does not', () => {
    // The threshold is `dx*dx + dy*dy < 64`, i.e. strictly inside a
    // radius of 8. Asserted explicitly because an off-by-one here is
    // invisible in use and changes how the control feels.
    trigger.click();
    touchDrag(7, 0);
    expect(state()).toBe('closed');

    trigger.click();
    touchDrag(8, 0);
    expect(state()).toBe('open');
  });

  it('measures diagonal travel, not each axis alone', () => {
    trigger.click();

    // 6² + 6² = 72, outside the radius, even though neither axis reaches 8.
    touchDrag(6, 6);

    expect(state()).toBe('open');
  });

  it('suppresses the synthetic click so a scroll cannot leak one', () => {
    trigger.click();
    const end = new (class extends Event {
      constructor() {
        super('touchend', { bubbles: true, cancelable: true });
        this.changedTouches = [{ clientX: 40, clientY: 0 }];
      }
    })();
    backdrop.dispatchEvent(
      new (class extends Event {
        constructor() {
          super('touchstart', { bubbles: true });
          this.touches = [{ clientX: 0, clientY: 0 }];
        }
      })(),
    );

    backdrop.dispatchEvent(end);

    // Without preventDefault the browser follows touchend with a click,
    // and the desktop-fallback click listener closes the sheet anyway —
    // defeating the threshold entirely.
    expect(end.defaultPrevented).toBe(true);
  });

  it('tracks the pressing class across the gesture', () => {
    trigger.click();
    backdrop.dispatchEvent(
      new (class extends Event {
        constructor() {
          super('touchstart', { bubbles: true });
          this.touches = [{ clientX: 0, clientY: 0 }];
        }
      })(),
    );
    expect(backdrop.classList.contains('is-pressing')).toBe(true);

    backdrop.dispatchEvent(new Event('touchcancel', { bubbles: true }));

    expect(backdrop.classList.contains('is-pressing')).toBe(false);
  });
});

describe('the post-swap handler', () => {
  it('applies the selected-day ring and is a no-op for another target', () => {
    grid.innerHTML = '<span data-date="2026-01-15"></span>';

    document.body.dispatchEvent(
      new CustomEvent('htmx:afterSwap', {
        bubbles: true,
        detail: { target: grid },
      }),
    );

    expect(
      grid.querySelector('[data-date="2026-01-15"]').classList.contains(
        'calendar-cell-selected',
      ),
    ).toBe(true);

    const other = document.createElement('div');
    other.id = 'something-else';
    expect(() =>
      document.body.dispatchEvent(
        new CustomEvent('htmx:afterSwap', {
          bubbles: true,
          detail: { target: other },
        }),
      ),
    ).not.toThrow();
  });
});
