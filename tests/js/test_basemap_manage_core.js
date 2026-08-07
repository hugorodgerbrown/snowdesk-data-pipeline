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
 *   - ``DEFAULT_BUDGET_MB`` matches ``basemap_download_core.js``'s
 *     ``DOWNLOAD_BUDGET_MB`` (SNOW-615) — the standing 500 MB budget is
 *     declared in both, and a device that has never opened the sheet
 *     budgets on one while a device that has budgets on the other.
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
 *   - ``manageRows`` never drops a row whose record carries no name. A
 *     download the user cannot see is the bug this ticket is fixing, so a
 *     missing name has to degrade to a listed, deletable row rather than
 *     to nothing.
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

  it("agrees with basemap_download_core.js's DOWNLOAD_BUDGET_MB default", () => {
    // SNOW-615: the same 500 MB standing budget is declared twice — here as
    // `DEFAULT_BUDGET_MB` (what the manage sheet preselects) and in
    // `basemap_download_core.js` as `DOWNLOAD_BUDGET_MB` (what
    // `basemapDownloadBudgetBytes()` falls back to when no `basemap.budgetMb`
    // row exists). A device that has never opened the sheet budgets on the
    // second; one that has, on the first. If they drift, the sheet opens
    // showing a figure the planner has never used, and the first interaction
    // silently changes the budget by writing the row.
    expect(core.DEFAULT_BUDGET_MB).toBe(downloadCore.DOWNLOAD_BUDGET_MB);
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
  // Areas as map.js's basemapDownloadedAreas() hands them over: the union
  // of the basemap.regions array and (SNOW-635) the basemap.customAreas
  // array, each already keyed by the id that names its Cache Storage
  // bucket. SNOW-635 review: `name` is always populated for a non-orphan —
  // a region's is stored; a custom area's is either a rename or the
  // numbered default `basemapDownloadedAreas()` fills in from `ordinal` —
  // so `custom-b2` here (never renamed) already carries "Custom area 2",
  // exactly as that upstream reader would hand it over.
  const areas = [
    { id: 'region-CH-2101', name: 'Aletsch', bytes: 40 * MB, savedAt: '2026-08-01T10:00:00.000Z' },
    { id: 'custom-a1', name: 'Home run', bytes: 120 * MB, savedAt: '2026-08-02T10:00:00.000Z' },
    { id: 'region-CH-2102', name: 'Gotthard', bytes: 80 * MB, savedAt: '2026-07-30T10:00:00.000Z' },
    { id: 'custom-b2', name: 'Custom area 2', bytes: 90 * MB, savedAt: '2026-08-03T10:00:00.000Z' },
  ];
  const isCustomAreaId = (id) => id === 'custom' || String(id).startsWith('custom-');
  const options = { isCustomAreaId };

  it('orders largest first — the axis the user is deciding along', () => {
    const rows = core.manageRows(areas, options);
    expect(rows.map((r) => r.id)).toEqual([
      'custom-a1',
      'custom-b2',
      'region-CH-2102',
      'region-CH-2101',
    ]);
  });

  it('labels a region from the name the download recorded', () => {
    const rows = core.manageRows(areas, options);
    const aletsch = rows.find((r) => r.id === 'region-CH-2101');
    expect(aletsch.label).toBe('Aletsch');
    expect(aletsch.size).toBe('40.0 MB');
    expect(aletsch.kind).toBe('region');
    expect(aletsch.renameable).toBe(false);
  });

  it("uses a renamed custom area's stored name", () => {
    // SNOW-635: the custom branch used to ALWAYS render a generic
    // "Custom area" label, because the one custom area's stored name was
    // the literal 'custom'. Now that a rename writes a REAL name, that
    // name must win — uniformly with how a region's name already does.
    const rows = core.manageRows(areas, options);
    const renamed = rows.find((r) => r.id === 'custom-a1');
    expect(renamed.kind).toBe('custom');
    expect(renamed.label).toBe('Home run');
    expect(renamed.renameable).toBe(true);
  });

  it("shows an unrenamed custom area's numbered default, read straight off name", () => {
    // The "Custom area N" text itself is built upstream, by
    // basemapDownloadedAreas() (map.js), from `ordinal` — this module
    // never sees `ordinal` at all any more, only the already-defaulted
    // `name` (see manageRows' own docstring for why the defaulting moved).
    const rows = core.manageRows(areas, options);
    const unrenamed = rows.find((r) => r.id === 'custom-b2');
    expect(unrenamed.kind).toBe('custom');
    expect(unrenamed.label).toBe('Custom area 2');
    expect(unrenamed.renameable).toBe(true);
  });

  it('identifies a custom area via the predicate, not the id shape', () => {
    // A region whose id merely LOOKS custom-ish must not be mistaken for
    // it — only what isCustomAreaId itself accepts is.
    const rows = core.manageRows(
      [{ id: 'region-custom-valley', name: 'Custom Valley', bytes: MB }],
      options,
    );
    expect(rows[0].kind).toBe('region');
    expect(rows[0].label).toBe('Custom Valley');
    expect(rows[0].renameable).toBe(false);
  });

  it('marks an orphaned custom bucket as not renameable', () => {
    // SNOW-612: an orphan has no record entry left to write a name onto,
    // and reconcileAreas never fills a default name for one either — so
    // unlike a real custom area it falls all the way back to its id.
    const rows = core.manageRows(
      [{ id: 'custom-orphan', bytes: MB, orphaned: true }],
      options,
    );
    expect(rows[0].kind).toBe('custom');
    expect(rows[0].renameable).toBe(false);
    expect(rows[0].label).toBe('custom-orphan');
  });

  it('still lists a row whose record stored no name', () => {
    // A download the user cannot see is the bug this ticket is fixing, so
    // a missing name degrades to the id rather than to nothing.
    const rows = core.manageRows([{ id: 'region-CH-9999', bytes: MB }], options);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('region-CH-9999');
  });

  it('treats every area as a region when no predicate is supplied', () => {
    const rows = core.manageRows([{ id: 'custom-a1', name: 'Home run', bytes: MB }]);
    expect(rows[0].kind).toBe('region');
    expect(rows[0].renameable).toBe(false);
  });

  it('skips entries with no id, which nothing could delete', () => {
    const rows = core.manageRows([{ bytes: MB }, { id: 'custom-a1', bytes: MB }], options);
    expect(rows.map((r) => r.id)).toEqual(['custom-a1']);
  });

  it('orders equal-sized areas stably across renders', () => {
    const equal = [
      { id: 'region-b', bytes: MB, savedAt: '2026-08-01T00:00:00.000Z' },
      { id: 'region-a', bytes: MB, savedAt: '2026-08-01T00:00:00.000Z' },
      { id: 'region-c', bytes: MB, savedAt: '2026-08-02T00:00:00.000Z' },
    ];
    const first = core.manageRows(equal, options).map((r) => r.id);
    const second = core.manageRows([...equal].reverse(), options).map((r) => r.id);
    expect(first).toEqual(second);
    // Newest first, then id.
    expect(first).toEqual(['region-c', 'region-a', 'region-b']);
  });

  it('tolerates a non-array record', () => {
    expect(core.manageRows(undefined, options)).toEqual([]);
    expect(core.manageRows(null, options)).toEqual([]);
  });

  it("carries a record's basemapKey through to the row (SNOW-645)", () => {
    const rows = core.manageRows(
      [{ id: 'region-CH-2101', name: 'Aletsch', bytes: MB, basemapKey: 'openfreemap_liberty' }],
      options,
    );
    expect(rows[0].basemapKey).toBe('openfreemap_liberty');
  });

  it("yields '' rather than a wrong basemap for a record with none", () => {
    // A record written before SNOW-645 shipped, or an orphan — see
    // reconcileAreas below. Either way this is "unknown", never a guess.
    const rows = core.manageRows(
      [{ id: 'region-CH-2101', name: 'Aletsch', bytes: MB }],
      options,
    );
    expect(rows[0].basemapKey).toBe('');
  });
});

describe('reconcileAreas (SNOW-612)', () => {
  const recorded = [
    { id: 'region-ch-4115', name: 'Verbier', bytes: 40 * MB, savedAt: '2026-08-01T00:00:00.000Z' },
    { id: 'custom', name: 'custom', bytes: 12 * MB, savedAt: '2026-08-02T00:00:00.000Z' },
  ];

  it('surfaces a pinned bucket with no record as an orphan', () => {
    const areas = core.reconcileAreas(recorded, ['region-ch-4115', 'custom', 'region-ch-1000'], {
      'region-ch-1000': 9 * MB,
    });

    const orphan = areas.find((a) => a.id === 'region-ch-1000');
    // This is the whole point: a run that failed partway left a bucket the
    // budget never counted and the sheet could not delete.
    expect(orphan).toBeTruthy();
    expect(orphan.orphaned).toBe(true);
    expect(orphan.bytes).toBe(9 * MB);
  });

  it('does not double-count an area present in both sources', () => {
    const areas = core.reconcileAreas(recorded, ['region-ch-4115', 'custom'], {});

    expect(areas).toHaveLength(2);
    expect(areas.map((a) => a.id)).toEqual(['region-ch-4115', 'custom']);
    // The record wins — it carries the name and timestamp a bucket id cannot.
    expect(areas[0].name).toBe('Verbier');
    expect(areas[0].bytes).toBe(40 * MB);
    expect(areas[0].orphaned).toBe(false);
  });

  it('counts an orphan toward the standing budget', () => {
    const areas = core.reconcileAreas(recorded, ['region-ch-1000'], {
      'region-ch-1000': 9 * MB,
    });
    const summary = core.budgetSummary(areas, 500 * MB);

    // 40 + 12 + 9 — the stranded bucket is what SNOW-612 makes visible to
    // the planner, so eviction can act on it.
    expect(summary.usedBytes).toBe(61 * MB);
  });

  it('lists an unmeasurable orphan at zero rather than guessing', () => {
    const areas = core.reconcileAreas([], ['region-ch-1000'], {});
    expect(areas[0].bytes).toBe(0);
    // Zero bytes, but still a row — deletable is the point.
    expect(areas[0].orphaned).toBe(true);
  });

  it('gives an orphan no savedAt, so it cannot sort as the newest download', () => {
    const areas = core.reconcileAreas(recorded, ['region-ch-1000'], { 'region-ch-1000': MB });
    const orphan = areas.find((a) => a.id === 'region-ch-1000');
    expect(orphan.savedAt).toBeUndefined();
  });

  it("carries a recorded area's basemapKey through, and gives an orphan none (SNOW-645)", () => {
    const withKey = [{ ...recorded[0], basemapKey: 'openfreemap_liberty' }];
    const areas = core.reconcileAreas(withKey, ['region-ch-4115', 'region-ch-1000'], {
      'region-ch-1000': 9 * MB,
    });

    expect(areas.find((a) => a.id === 'region-ch-4115').basemapKey).toBe(
      'openfreemap_liberty',
    );
    // No record means no known basemap either.
    expect(areas.find((a) => a.id === 'region-ch-1000').basemapKey).toBeNull();
  });

  it('renders an orphan as a deletable row carrying its bucket id', () => {
    const areas = core.reconcileAreas([], ['region-ch-1000'], { 'region-ch-1000': 3 * MB });
    const rows = core.manageRows(areas, { isCustomAreaId: (id) => id === 'custom' });

    expect(rows).toHaveLength(1);
    expect(rows[0].orphaned).toBe(true);
    // No stored name, so the id is the label — and the id is what the
    // delete button needs to name the bucket.
    expect(rows[0].id).toBe('region-ch-1000');
    expect(rows[0].label).toBe('region-ch-1000');
  });

  it('orders orphans by id for a stable render', () => {
    const first = core.reconcileAreas([], ['region-c', 'region-a', 'region-b'], {});
    const second = core.reconcileAreas([], ['region-b', 'region-c', 'region-a'], {});
    expect(first.map((a) => a.id)).toEqual(['region-a', 'region-b', 'region-c']);
    expect(first).toEqual(second);
  });

  it('degrades to the records alone when nothing is stored', () => {
    expect(core.reconcileAreas(recorded, [], {})).toHaveLength(2);
    expect(core.reconcileAreas(recorded, undefined, undefined)).toHaveLength(2);
  });

  it('tolerates a non-array record', () => {
    expect(core.reconcileAreas(undefined, ['region-x'], {})).toHaveLength(1);
    expect(core.reconcileAreas(null, null, null)).toEqual([]);
  });
});
