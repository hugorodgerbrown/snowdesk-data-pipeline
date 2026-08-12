---
name: lighthouse
description: Lighthouse CI budgets and the npm run lh local audit under config.settings.perf; checklist for new public pages
status: current
last-reviewed: 2026-06-10
---

# Lighthouse CI — accessibility, SEO, performance, best-practices

Lighthouse audits the public site on every PR and blocks merge on
regressions. Both local and CI invocations read
[`lighthouserc.json`](../lighthouserc.json) for URLs, thresholds, and
assertions — keep it the single source of truth.

**Budgets** (error = blocks merge, warn = report only):
- `categories:accessibility` ≥ 0.95 — error
- `categories:seo` ≥ 0.95 — error
- `categories:performance` ≥ 0.85 — warn
- `categories:best-practices` ≥ 0.9 — warn

Mobile preset by default (no desktop override), 3 runs per URL.

## Run locally — `npm run lh`

Requires Chrome/Chromium on the host. The script:

1. Runs `bin/minify-js` (SNOW-622), which minifies `static/js/*.js` **in
   place**. Before this, the audit measured ~23,000 lines of unminified
   first-party JS that production does not serve — a budget checked against
   assets nobody receives is measuring the wrong thing. `bin/build.sh` runs
   the same step on deploy, so the two agree.

   It rewrites tracked files, so `git checkout -- static/js` after a local
   run if you intend to keep working in the tree. `sw.js` and `sw-kill.js`
   are deliberately excluded — see the script header for why.
2. Runs `bin/minify-css`, the same idea for the hand-written stylesheets.
   `map.css` was the one file in the critical path no build step touched:
   ~3,000 commented lines, render-blocking on the map homepage, and larger
   compressed than the whole compiled Tailwind output. Minifying it takes
   it from ~35 KB to ~6 KB on the wire.

   Also rewrites tracked files — `git checkout -- static/css` after a local
   run. `output.css` (Tailwind already emits it with `--minify`) and the
   vendored `maplibre-gl.css` are excluded.
3. Runs `collectstatic --noinput` under `DJANGO_SETTINGS_MODULE=config.settings.perf`
   so the ManifestStaticFilesStorage manifest is populated.
4. Starts a Django server on `:8765` using `config.settings.perf` — the
   same WhiteNoise + `CompressedManifestStaticFilesStorage` + `GZipMiddleware`
   stack as production, so hashed filenames, pre-compressed assets, and
   cache headers match reality.
5. Audits the URLs in `lighthouserc.json` and writes HTML + JSON reports
   to `.lighthouseci/` (gitignored).

```bash
npm run lh          # full audit — ~90s
npm run lh:open     # opens the representative HTML report per URL (macOS)
```

**`config/settings/perf.py` is Lighthouse-only** — extends `development`,
flips `DEBUG=False`, adds WhiteNoise + GZip. Not a deploy target;
`production.py` remains the production source of truth.

## CI

[`.github/workflows/lighthouse.yml`](../.github/workflows/lighthouse.yml)
runs on every PR: loads regions/resorts/bulletin fixtures, rebuilds
render models, runs `collectstatic` under perf settings, then
`lhci autorun` with the CH-4115 bulletin URL added on top of the
config URLs. Reports upload as a 14-day GitHub Actions artifact.

## When adding a new public page

Check all of:

- `<meta name="description" content="…">` — fail-fast for SEO.
- `<link rel="icon" type="image/svg+xml" href="{% static 'favicon.svg' %}">` —
  otherwise browsers probe `/favicon.ico` and log a 404 to the console.
- Use `text-text-1`, `text-text-2`, or the `--color-eaws-*-text` tokens
  when contrast matters; `text-text-3` sits on the WCAG AA boundary
  (4.67:1 on `--color-bg`) — never dim it further with `opacity-*`.
- Keep heading order sequential (`h1 → h2 → h3`); do not skip levels.
  The reviewer agent will run `npm run lh` and flag regressions.

**Before opening a PR**: run `npm run lh` alongside `uv run tox`
and clear both. The reviewer agent runs lh as part of its checklist.
