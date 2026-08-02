/*
 * tests/js/test_basemap_manage_core.js — Vitest unit tests for
 * static/js/basemap_manage_core.js (SNOW-588).
 *
 * The pure half of the "Manage downloads" sheet: the budget arithmetic,
 * the MB conversion and its display form, and the row model the sheet
 * renders. The DOM half is covered by test_map_downloads_manager.js and
 * tests/e2e/test_manage_downloads.py.
 *
 * Three of these assertions are load-bearing rather than incidental:
 *
 *   - ``MIN_BUDGET_MB`` matches ``basemap_download_core.js``'s
 *     ``DOWNLOAD_CEILING_MB``. The two modules are separate page-scope
 *     IIFEs with no module system between them, so the constant is
 *     re-declared and this round-trip is the only thing holding them
 *     together. A budget below the per-run ceiling would let a user
 *     choose a setting under which some single downloads can never be
 *     kept — the "two limits that do not talk to each other" bug
 *     SNOW-586 exists to fix, reintroduced through the front door.
 *   - ``budgetSummary`` reports ``overBudget`` independently of its
 *     clamped ``pct``. A total over budget is exactly the state the
 *     surface must be able to say out loud, and it is the state in which
 *     the percentage has run out of room to say it.
 *   - ``manageRows`` never drops a row whose name cannot be resolved. A
 *     download the user cannot see is the bug this ticket is fixing, so
 *     an unresolvable region id has to degrade to a listed, deletable row
 *     rather than to nothing.
 *
 * Both modules are plain IIFEs assigning a frozen global; jsdom's
 * `window` is also `self`, so importing for side effects is enough.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';
import '../../static/js/basemap_manage_core.js';

const core = self.pwaBasemapManageCore;
const downloadCore = self.pwaBasemapDownloadCore;

const MB = 1024 * 1024;

describe('constants', () => {
  it('pins the minimum budget to the per-run download ceiling', () => {
    // See the file header: the only link between the two re-declared
    // constants. If DOWNLOAD_CEILING_MB moves, this fails and the budget
    // floor must move with it.
    expect(core.MIN_BUDGET_MB).toBe(downloadCore.DOWNLOAD_CEILING_MB);
  });

  it('offers the minimum as the smallest choice and 500 as the default', () => {
    expect(core.BUDGET_CHOICES_MB[0]).toBe(core.MIN_BUDGET_MB);
    expect(core.BUDGET_CHOICES_MB).toContain(core.DEFAULT_BUDGET_MB);
    // Ascending, so a <select> can render it in order without sorting.
    const sorted = [...core.BUDGET_CHOICES_MB].sort((a, b) => a - b);
    expect([...core.BUDGET_CHOICES_MB]).toEqual(sorted);
  });

  it('uses 1024-based megabytes, matching the download core', () => {
    // Every size the user has been shown for a download is computed this
    // way (buildBlob / basemap_tiles.py). Decimal MB here would report a
    // different number for the same download.
    expect(core.BYTES_PER_MB).toBe(MB);
    expect(core.megabytesToBytes(200)).toBe(200 * MB);
  });
});

describe('megabytesToBytes', () => {
  it('converts megabytes to bytes', () => {
    expect(core.megabytesToBytes(1)).toBe(MB);
    expect(core.megabytesToBytes(0.5)).toBe(MB / 2);
  });

  it('returns 0 rather than NaN for unusable input', () => {
    // A NaN budget would make every comparison against it false, which
    // reads as "always under budget" — the silent-overrun failure mode.
    for (const bad of [undefined, null, 'abc', NaN, -5]) {
      expect(core.megabytesToBytes(bad)).toBe(0);
    }
  });
});

describe('formatMegabytes', () => {
  it('shows one decimal below 100 MB', () => {
    expect(core.formatMegabytes(12.34 * MB)).toBe('12.3 MB');
    expect(core.formatMegabytes(99.94 * MB)).toBe('99.9 MB');
  });

  it('drops the decimal at and above 100 MB', () => {
    expect(core.formatMegabytes(100 * MB)).toBe('100 MB');
    expect(core.formatMegabytes(487.6 * MB)).toBe('488 MB');
  });

  it('never renders a real download as costing nothing', () => {
    // A sub-0.1 MB area is still an area; "0.0 MB" would read as free.
    expect(core.formatMegabytes(1024)).toBe('< 0.1 MB');
    expect(core.formatMegabytes(0.09 * MB)).toBe('< 0.1 MB');
  });

  it('renders genuinely-empty and unusable sizes as 0 MB', () => {
    expect(core.formatMegabytes(0)).toBe('0 MB');
    expect(core.formatMegabytes(undefined)).toBe('0 MB');
    expect(core.formatMegabytes(NaN)).toBe('0 MB');
  });
});

describe('clampBudgetMb', () => {
  it('returns an offered choice unchanged', () => {
    for (const choice of core.BUDGET_CHOICES_MB) {
      expect(core.clampBudgetMb(choice)).toBe(choice);
    }
  });

  it('snaps an unoffered value to the nearest choice', () => {
    expect(core.clampBudgetMb(510)).toBe(500);
    expect(core.clampBudgetMb(1900)).toBe(2000);
  });

  it('snaps out-of-range values to the ends', () => {
    expect(core.clampBudgetMb(1)).toBe(core.MIN_BUDGET_MB);
    expect(core.clampBudgetMb(999999)).toBe(2000);
  });

  it('breaks a tie towards the larger choice', () => {
    // Rounding a budget DOWN unasked is the direction that silently
    // evicts downloads, so a midpoint must not do it.
    expect(core.clampBudgetMb(750)).toBe(1000);
  });

  it('falls back to the default for an unset or unusable value', () => {
    // null and '' are the trap: both coerce to a perfectly finite 0, which
    // would snap to the SMALLEST budget. An absent row must land on the
    // default, not quietly shrink the user's budget to the minimum.
    for (const bad of [undefined, null, '', 'abc', NaN]) {
      expect(core.clampBudgetMb(bad)).toBe(core.DEFAULT_BUDGET_MB);
    }
  });
});

describe('budgetSummary', () => {
  const budget = 500 * MB;

  it('sums stored bytes and reports the remainder', () => {
    const summary = core.budgetSummary(
      [{ bytes: 100 * MB }, { bytes: 150 * MB }],
      budget,
    );
    expect(summary.usedBytes).toBe(250 * MB);
    expect(summary.freeBytes).toBe(250 * MB);
    expect(summary.pct).toBe(50);
    expect(summary.overBudget).toBe(false);
    expect(summary.count).toBe(2);
  });

  it('reports an empty device as zero rather than as unknown', () => {
    const summary = core.budgetSummary([], budget);
    expect(summary.usedBytes).toBe(0);
    expect(summary.pct).toBe(0);
    expect(summary.count).toBe(0);
    expect(summary.overBudget).toBe(false);
  });

  it('says overBudget even though pct has run out of room to', () => {
    // The load-bearing case: pct is clamped so it can drive a bar's width
    // directly, so the boolean is the only thing left that can carry the
    // truth. A user who has just lowered their budget below what they
    // already hold lands here.
    const summary = core.budgetSummary([{ bytes: 900 * MB }], budget);
    expect(summary.usedBytes).toBe(900 * MB);
    expect(summary.pct).toBe(100);
    expect(summary.overBudget).toBe(true);
    expect(summary.freeBytes).toBe(0);
  });

  it('treats malformed entries as zero rather than poisoning the total', () => {
    const summary = core.budgetSummary(
      [{ bytes: 100 * MB }, { bytes: 'nonsense' }, {}, null],
      budget,
    );
    expect(summary.usedBytes).toBe(100 * MB);
    expect(Number.isNaN(summary.usedBytes)).toBe(false);
  });

  it('survives a missing or zero budget without dividing by it', () => {
    const summary = core.budgetSummary([{ bytes: 10 * MB }], 0);
    expect(summary.pct).toBe(0);
    expect(Number.isFinite(summary.pct)).toBe(true);
    expect(summary.overBudget).toBe(true);
  });

  it('tolerates a non-array record', () => {
    expect(core.budgetSummary(undefined, budget).usedBytes).toBe(0);
    expect(core.budgetSummary(null, budget).count).toBe(0);
  });
});

describe('manageRows', () => {
  const areas = [
    { id: 'region-CH-2101', kind: 'region', region_id: 'CH-2101', bytes: 40 * MB, savedAt: '2026-08-01T10:00:00.000Z' },
    { id: 'custom', kind: 'custom', bytes: 120 * MB, savedAt: '2026-08-02T10:00:00.000Z' },
    { id: 'region-CH-2102', kind: 'region', region_id: 'CH-2102', bytes: 80 * MB, savedAt: '2026-07-30T10:00:00.000Z' },
  ];

  const labelFor = (area) =>
    ({ 'CH-2101': 'Aletsch', 'CH-2102': 'Gotthard' })[area.region_id] || '';

  it('orders largest first — the axis the user is deciding along', () => {
    const rows = core.manageRows(areas, labelFor);
    expect(rows.map((r) => r.id)).toEqual([
      'custom',
      'region-CH-2102',
      'region-CH-2101',
    ]);
  });

  it('labels regions from the injected resolver and formats each size', () => {
    const rows = core.manageRows(areas, labelFor);
    const aletsch = rows.find((r) => r.id === 'region-CH-2101');
    expect(aletsch.label).toBe('Aletsch');
    expect(aletsch.size).toBe('40.0 MB');
    expect(aletsch.kind).toBe('region');
  });

  it('carries the custom area through as its own kind', () => {
    const rows = core.manageRows(areas, labelFor);
    expect(rows.find((r) => r.id === 'custom').kind).toBe('custom');
  });

  it('still lists a row whose name cannot be resolved', () => {
    // Offline with an uncached regions.geojson, or a region retired
    // upstream. A download the user cannot see is the bug being fixed
    // here, so this must degrade to a listed, deletable row.
    const rows = core.manageRows(
      [{ id: 'region-CH-9999', kind: 'region', region_id: 'CH-9999', bytes: MB }],
      () => '',
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('CH-9999');
    expect(rows[0].id).toBe('region-CH-9999');
  });

  it('falls back to the area id when there is no region id either', () => {
    const rows = core.manageRows([{ id: 'custom', kind: 'custom', bytes: MB }]);
    expect(rows[0].label).toBe('custom');
  });

  it('treats a throwing resolver as an unresolved name, not a dropped row', () => {
    const rows = core.manageRows(
      [{ id: 'region-CH-2101', region_id: 'CH-2101', bytes: MB }],
      () => {
        throw new Error('FEATURE_BY_REGION_ID not ready');
      },
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('CH-2101');
  });

  it('skips entries with no id, which nothing could delete', () => {
    const rows = core.manageRows([{ bytes: MB }, { id: 'custom', bytes: MB }]);
    expect(rows.map((r) => r.id)).toEqual(['custom']);
  });

  it('orders equal-sized areas stably across renders', () => {
    const equal = [
      { id: 'region-b', bytes: MB, savedAt: '2026-08-01T00:00:00.000Z' },
      { id: 'region-a', bytes: MB, savedAt: '2026-08-01T00:00:00.000Z' },
      { id: 'region-c', bytes: MB, savedAt: '2026-08-02T00:00:00.000Z' },
    ];
    const first = core.manageRows(equal).map((r) => r.id);
    const second = core.manageRows([...equal].reverse()).map((r) => r.id);
    expect(first).toEqual(second);
    // Newest first, then id.
    expect(first).toEqual(['region-c', 'region-a', 'region-b']);
  });

  it('tolerates a non-array record', () => {
    expect(core.manageRows(undefined)).toEqual([]);
    expect(core.manageRows(null)).toEqual([]);
  });
});
