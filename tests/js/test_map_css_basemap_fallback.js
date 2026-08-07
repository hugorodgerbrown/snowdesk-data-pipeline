/*
 * tests/js/test_map_css_basemap_fallback.js — source-text guard for
 * static/css/map.css's per-basemap roundel-fill rules (SNOW-645 review).
 *
 * static/css/map.css is NOT compiled by Tailwind — its --color-basemap-*
 * custom properties only resolve because static/css/output.css (a
 * gitignored build artefact) happens to define them. A checkout with a
 * STALE output.css — the default for anyone who hasn't just re-run the
 * Tailwind build — leaves the attribute selector in place but the token
 * undefined, so a BARE var(--color-basemap-…) is invalid at
 * computed-value time and `background` resolves to transparent. It does
 * NOT fall through to the base ::before rule's --color-sync-ok, because
 * the more-specific per-key selector has already won the cascade — see
 * the block comment directly above these rules in map.css for the full
 * explanation. Every one of the five rules must therefore nest a
 * var(--color-sync-ok) fallback so a stale build degrades to today's
 * solid green disc rather than a hollow, "your download was wiped"-
 * looking ring.
 *
 * This is a source-text assertion, not a runtime one, because the failure
 * mode is a BUILD-ARTEFACT desync (a missing custom property at paint
 * time), which jsdom's non-rendering DOM cannot reproduce — there is no
 * computed style to assert against. Reading the raw CSS and checking for
 * the fallback syntax is the whole test, mirroring
 * tests/js/test_offline_page_reset.js's own raw-source assertions against
 * static/offline.html.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

// Resolved off process.cwd() rather than import.meta.url, matching
// tests/js/test_sw.js's own SW_SOURCE — Vitest's root is the repo root.
const MAP_CSS = readFileSync(join(process.cwd(), 'static', 'css', 'map.css'), 'utf8');

describe('per-basemap roundel fill fallback (SNOW-645 review)', () => {
  it('never references a --color-basemap-* token without a var(--color-sync-ok) fallback', () => {
    // Matches var(--color-basemap-<key>) with NO comma before the closing
    // paren — the bare, unguarded form. A correctly-guarded reference
    // reads var(--color-basemap-<key>, var(--color-sync-ok)) instead, so
    // it never matches this pattern at all.
    const bare = MAP_CSS.match(/var\(--color-basemap-[a-z-]+\)/g) || [];
    expect(bare).toEqual([]);
  });

  it('carries all five per-key fallback rules, so the guard above is not vacuous', () => {
    // A regex asserting an empty list also passes if the source dropped
    // every basemap rule outright — this pins the count so that failure
    // mode is caught too.
    const guarded = MAP_CSS.match(/var\(--color-basemap-[a-z-]+, var\(--color-sync-ok\)\)/g) || [];
    expect(guarded).toHaveLength(5);
  });
});
