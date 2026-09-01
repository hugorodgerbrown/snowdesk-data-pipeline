/*
 * tests/js/test_hourly_chart_legend.js — Vitest unit tests for
 * static/js/hourly_chart_legend.js (SNOW-790).
 *
 * The module shipped with SNOW-723 and had no client-side coverage. SNOW-790
 * rewrites the markup it drives — the key drops from ten prose rows to eight
 * labelled ones and the panel is resized — without touching the script, which
 * is exactly the change that can break an open/close contract silently: every
 * selector here is a data attribute in a template that was just edited.
 *
 * The handlers are delegated from `document` and the module is an IIFE with no
 * exports, so each test re-imports it (`vi.resetModules()` + `await import`)
 * against its own fixture rather than trying to reset listeners.
 *
 * The fixture mirrors the shape templates/includes/_hourly_chart.html and
 * _hourly_chart_legend.html render together: the `role="group"` chart root is
 * load-bearing, because `setOpen` walks up to it from whichever control was
 * clicked and does nothing at all without it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

/** Build one chart root carrying a trigger, a backdrop and a panel. */
function buildFixture() {
  document.body.innerHTML = `
    <div role="group" aria-label="chart">
      <button type="button" data-hourly-chart-legend-open aria-expanded="false">i</button>
      <div data-hourly-chart-legend-backdrop class="hidden" hidden></div>
      <div role="dialog" data-hourly-chart-legend class="hidden" hidden>
        <button type="button" data-hourly-chart-legend-close>close</button>
      </div>
    </div>
  `;
}

/** Re-import the module fresh against the current fixture. */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/hourly_chart_legend.js');
}

const trigger = () => document.querySelector('[data-hourly-chart-legend-open]');
const panel = () => document.querySelector('[data-hourly-chart-legend]');
const backdrop = () => document.querySelector('[data-hourly-chart-legend-backdrop]');
const closer = () => document.querySelector('[data-hourly-chart-legend-close]');

beforeEach(async () => {
  buildFixture();
  await loadModule();
});

describe('opening the key', () => {
  it('shows the panel and its backdrop together', () => {
    trigger().click();

    expect(panel().hidden).toBe(false);
    expect(panel().classList.contains('hidden')).toBe(false);
    expect(backdrop().hidden).toBe(false);
  });

  it('reports the open state on the trigger', () => {
    trigger().click();

    expect(trigger().getAttribute('aria-expanded')).toBe('true');
  });

  it('moves focus into the panel', () => {
    trigger().click();

    expect(document.activeElement).toBe(closer());
  });
});

describe('closing the key', () => {
  beforeEach(() => trigger().click());

  it('closes on the close button', () => {
    closer().click();

    expect(panel().hidden).toBe(true);
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on a backdrop click', () => {
    backdrop().click();

    expect(panel().hidden).toBe(true);
  });

  it('closes on Escape', () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(panel().hidden).toBe(true);
  });

  it('returns focus to the trigger that opened it', () => {
    closer().click();

    expect(document.activeElement).toBe(trigger());
  });
});

describe('the chart without a key', () => {
  it('does nothing rather than throwing', async () => {
    // A chart rendered with JS present but no legend markup — the module
    // returns early on a missing panel, and must not take the page with it.
    document.body.innerHTML = `
      <div role="group" aria-label="chart">
        <button type="button" data-hourly-chart-legend-open aria-expanded="false">i</button>
      </div>
    `;
    await loadModule();

    expect(() => trigger().click()).not.toThrow();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });
});
