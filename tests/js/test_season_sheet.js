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
      <div data-season-readout hidden>
        <span data-readout-tile data-level="no_rating">
          <span data-readout-level="no_rating" hidden>–</span>
          <span data-readout-level="moderate" hidden>2</span>
          <span data-readout-level="considerable" hidden>3</span>
          <span data-readout-level="high" hidden>4</span>
        </span>
        <span data-readout-level="no_rating" hidden>No bulletin</span>
        <span data-readout-level="moderate" hidden>Moderate</span>
        <span data-readout-level="considerable" hidden>Considerable</span>
        <span data-readout-level="high" hidden>High</span>
        <div data-readout-date>Thursday 15 January 2026</div>
        <a data-readout-link href="/day/2026-01-15/" hidden>Read the bulletin for <span data-readout-day>15 January</span></a>
      </div>
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
  const readout = document.querySelector('[data-season-readout]');
  const link = readout.querySelector('[data-readout-link]');

  /** @param {string} html */
  function swap(html) {
    grid.innerHTML = html;
    document.body.dispatchEvent(
      new CustomEvent('htmx:afterSwap', {
        bubbles: true,
        detail: { target: grid },
      }),
    );
  }

  /** @returns {string[]} the readout levels left visible */
  function shownLevels() {
    return Array.from(readout.querySelectorAll('[data-readout-level]'))
      .filter((el) => !el.hidden)
      .map((el) => el.getAttribute('data-readout-level'));
  }

  /**
   * A rated day cell as the fragment draws it.
   *
   * @param {string} date
   * @param {string} level
   * @param {string} long
   * @param {string} short
   * @returns {string}
   */
  function day(date, level, long, short) {
    return (
      `<a class="season-day" href="/day/${date}/" data-date="${date}" ` +
      `data-level="${level}" data-day-long="${long}" data-day-short="${short}"></a>`
    );
  }

  beforeEach(() => {
    readout.hidden = true;
    link.hidden = true;
  });

  it('rings the selected day and is a no-op for another target', () => {
    swap(
      day('2026-01-15', 'considerable', 'Thursday 15 January 2026', '15 January') +
        day('2026-01-16', 'moderate', 'Friday 16 January 2026', '16 January'),
    );

    expect(
      grid.querySelector('[data-date="2026-01-15"]').classList.contains(
        'season-day-ring',
      ),
    ).toBe(true);
    expect(grid.querySelectorAll('.season-day-ring')).toHaveLength(1);
    expect(
      grid.querySelector('[data-date="2026-01-15"]').getAttribute('aria-current'),
    ).toBe('date');

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

  it("fills the readout with the page's day, its peak level, and no link", () => {
    // A two-level day: data-state is split, data-level the peak.
    swap(
      '<a class="season-day" href="/day/2026-01-15/" data-date="2026-01-15" ' +
        'data-state="split" data-lo="moderate" data-hi="considerable" ' +
        'data-level="considerable" data-day-long="Thursday 15 January 2026" ' +
        'data-day-short="15 January"></a>',
    );

    expect(readout.hidden).toBe(false);
    expect(
      readout.querySelector('[data-readout-tile]').getAttribute('data-level'),
    ).toBe('considerable');
    expect(shownLevels()).toEqual(['considerable', 'considerable']);
    // The page's own day: the link would point at this page.
    expect(link.hidden).toBe(true);
  });

  it('reads a day with no bulletin as such, with no link', () => {
    swap(
      '<span class="season-day" data-date="2026-01-15" data-state="none" ' +
        'data-level="no_rating" data-day-long="Thursday 15 January 2026" ' +
        'data-day-short="15 January"></span>',
    );

    expect(readout.hidden).toBe(false);
    expect(shownLevels()).toEqual(['no_rating', 'no_rating']);
    expect(link.hidden).toBe(true);
  });

  it('leaves the readout hidden when the selected day is not drawn', () => {
    swap(day('2026-02-01', 'moderate', 'Sunday 1 February 2026', '1 February'));

    expect(readout.hidden).toBe(true);
    expect(grid.querySelectorAll('.season-day-ring')).toHaveLength(0);
  });

  describe('a click on a rated day', () => {
    beforeEach(() => {
      swap(
        day('2026-01-15', 'considerable', 'Thursday 15 January 2026', '15 January') +
          day('2026-01-09', 'high', 'Friday 9 January 2026', '9 January') +
          '<span class="season-day season-key-swatch season-day-ring" data-state="high"></span>',
      );
    });

    /**
     * @param {string} date
     * @param {MouseEventInit} [init]
     * @returns {MouseEvent}
     */
    function clickDay(date, init = {}) {
      const event = new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        ...init,
      });
      grid.querySelector(`[data-date="${date}"]`).dispatchEvent(event);
      return event;
    }

    it('selects the day instead of navigating', () => {
      const event = clickDay('2026-01-09');

      expect(event.defaultPrevented).toBe(true);
      const ringed = grid.querySelectorAll('[data-date].season-day-ring');
      expect(ringed).toHaveLength(1);
      expect(ringed[0].getAttribute('data-date')).toBe('2026-01-09');
      // aria-current moves with the ring: on the new day, off the old one.
      const current = grid.querySelectorAll('[aria-current="date"]');
      expect(current).toHaveLength(1);
      expect(current[0].getAttribute('data-date')).toBe('2026-01-09');
      // The key's "Selected day" swatch keeps its ring.
      expect(
        grid.querySelector('.season-key-swatch').classList.contains('season-day-ring'),
      ).toBe(true);
    });

    it("moves the readout to the day: level, dates and the day's own link", () => {
      clickDay('2026-01-09');

      expect(shownLevels()).toEqual(['high', 'high']);
      expect(
        readout.querySelector('[data-readout-tile]').getAttribute('data-level'),
      ).toBe('high');
      expect(readout.querySelector('[data-readout-date]').textContent).toBe(
        'Friday 9 January 2026',
      );
      expect(readout.querySelector('[data-readout-day]').textContent).toBe(
        '9 January',
      );
      expect(link.getAttribute('href')).toBe('/day/2026-01-09/');
      expect(link.hidden).toBe(false);
    });

    it("hides the link again on returning to the page's own day", () => {
      clickDay('2026-01-09');
      clickDay('2026-01-15');

      expect(link.hidden).toBe(true);
      expect(link.getAttribute('href')).toBe('/day/2026-01-15/');
      expect(readout.querySelector('[data-readout-day]').textContent).toBe(
        '15 January',
      );
    });

    it('leaves a modified click to the browser (open in a new tab)', () => {
      const event = clickDay('2026-01-09', { metaKey: true });

      expect(event.defaultPrevented).toBe(false);
      expect(
        grid.querySelector('[data-date="2026-01-15"]').classList.contains(
          'season-day-ring',
        ),
      ).toBe(true);
    });
  });
});
