// vitest.config.mjs — Vitest configuration for the JS unit-test harness (SNOW-495).
//
// Sub-issue 1 of the SNOW-494 epic. Runner is pinned to Vitest (jsdom
// environment, fake-indexeddb) by the ticket — see docs/client-side-tests.md
// for when to reach for this harness vs Playwright (tests/e2e/).
//
// `include` overrides Vitest's default glob (which requires a `.test.` or
// `.spec.` infix) so tests can use the project's Python-style `test_*.js`
// naming, mirroring `tests/e2e/test_*.py`. No coverage config — JS coverage
// isn't a ticket requirement (the 90% target in CLAUDE.md is Python-only).

import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/js/setup.js'],
    include: ['tests/js/**/test_*.js'],
  },
});
