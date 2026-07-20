---
name: worktrees
description: init-worktree worktree seed strategy, dev credentials, seed_test_data dataset coverage, reseed procedure
status: current
last-reviewed: 2026-07-18
---

# Worktrees and DB seeding

Every Claude worktree is bootstrapped by `bin/init-worktree`, which runs
automatically via the `PostToolUse:EnterWorktree` hook in
`.claude/settings.json`. The script is idempotent — re-running it on a
fully-configured worktree is a no-op.

## Seed recipe

When `db.sqlite3` is absent the script runs three commands in order:

```bash
uv run python manage.py migrate --noinput
uv run python manage.py loaddata eaws_CH resorts
uv run python manage.py seed_test_data --all --commit
```

The dataset is built from the FactoryBoy factories in `tests/factories.py` by
`seed_test_data` (the factory-based path that replaced the old
`loaddata test_data` JSON fixture). It covers the CH region/resort reference
data, the map-coverage and CH-4115 detail bulletin layer, a small
`ForecastPoint`/`ForecastPointWeather`/`Favourite` set, and the two named dev
accounts (see [Dev credentials](#dev-credentials) — folded in from the former
`seed_dev_users` command via `SeedModel.USER`, so `--all` creates them and
`seed_test_data --include user` seeds just the accounts). `seed_test_data`
refuses to run when `DEBUG=False`; worktrees use development settings, so it is
safe.

**Why this instead of copying the main repo's DB?**

Copying the live dev DB produces a worktree whose data differs from CI's
seed environment. That divergence repeatedly broke the SNOW-13
query-count baseline (home=8/map=7 under CI fixtures, lower off a copied
dev DB), causing churn on SNOW-341 and SNOW-342. Seeding deterministically
from the factories guarantees every worktree is identical to CI's data
environment.

## Compiled CSS

When `static/css/output.css` is absent the script builds it too:

```bash
npm install
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --minify
```

`output.css` is a gitignored Tailwind build artifact, so it is not shared
across worktrees — each fresh worktree has to build its own. Without it the
page 404s the stylesheet and renders unstyled: any browser preview of a
fresh worktree shows a collapsed layout until the CSS is built.

The current Playwright e2e suite (`tox -e e2e`) passes with or without
`output.css` — those tests capture uncaught JS (`pageerror`), not layout,
so a missing stylesheet is harmless to them (see the "Known limitations"
note in [`docs/client-side-tests.md`](client-side-tests.md)). Neither CI
(`.github/workflows/e2e.yml`) nor the `tox -e e2e` env compiles the CSS.
Building it at init-time is about correct browser preview today, and about
not tripping up any future viewport-dependent e2e test — an unstyled,
collapsed page makes elements report as "outside the viewport" or as
intercepting clicks, producing failures whose message doesn't point at the
missing CSS.

To rebuild after editing `src/css/main.css`, either re-run the command above
or use the Tailwind watcher under "Running locally" in
[`CLAUDE.md`](../CLAUDE.md).

## Dev credentials

`seed_test_data` creates two well-known accounts (the `SeedModel.USER` layer).
They are anonymous-surface neutral — home/map are unauthenticated and do not
query users, so they do not shift the query-count baseline. The normal user also
owns the sample favourites.

| Role | Email | Password | Notes |
|------|-------|----------|-------|
| Superuser | `admin@snowdesk.dev` | `snowdesk` | Full `/admin/` access |
| Subscribed user | `dev@snowdesk.dev` | `snowdesk` | Active subscriber, subscribed to CH-4115 (Martigny-Verbier); owns the seeded favourites |

The constants are defined in
`bulletins/management/commands/seed_test_data.py` (`SUPERUSER_EMAIL`,
`NORMAL_USER_EMAIL`, `DEV_USER_PASSWORD`, `SUBSCRIBED_REGION_ID`).

## Force-reseed procedure

To wipe and rebuild the worktree DB from scratch:

```bash
rm db.sqlite3 && bin/init-worktree
```

Existing worktrees that were set up before SNOW-345 (when the script
copied `db.sqlite3`) keep their copied DB until manually reseeded using
the command above.

## Seeded dataset coverage

`seed_test_data --all --commit` (run after `loaddata eaws_CH resorts`) is the
dataset loaded by CI and by every fresh worktree. This section documents what it
contains so it can be relied on and extended safely.

### Record counts

| Model | Rows | Source |
|-------|------|--------|
| `regions.majorregion` | 9 | `eaws_CH` fixture |
| `regions.subregion` | 21 | `eaws_CH` fixture |
| `regions.microregion` | 149 | `eaws_CH` fixture |
| `regions.resort` | 148 | `resorts` fixture |
| `bulletins.bulletin` | 178 | `seed_test_data` |
| `bulletins.regionbulletin` | 178 | `seed_test_data` |
| `bulletins.regiondayrating` | 178 | `seed_test_data` |
| `bulletins.weathersnapshot` | 178 | `seed_test_data` |
| `bulletins.forecastpoint` | 5 | `seed_test_data` |
| `bulletins.forecastpointweather` | 150 | `seed_test_data` |
| `favourites.favourite` | 5 | `seed_test_data` (owned by the normal dev user) |
| `auth.user` | 2 | `seed_test_data` (superuser + subscribed normal user) |
| `accounts.subscriber` | 1 | `seed_test_data` (the normal dev user) |
| `accounts.subscription` | 1 | `seed_test_data` (normal dev user → CH-4115) |

### Data coverage

- **Provider:** SLF-shaped payloads only. ALBINA and Météo-France are not
  represented.
- **Date span:** 1 April – 30 April 2026 (30 days). All dates fall in the
  spring off-season for Alpine snowpack; no mid-winter elevated-danger days
  are present.
- **Regions:** all 149 CH micro-regions have exactly one bulletin covering
  a single date (2026-04-08, the "map reference date") — enough to render
  the choropleth map.
- **CH-4115 (Martigny-Verbier):** 30 bulletins, one per day of April 2026.
  This is the canonical bulletin detail URL
  (`/ch-4115/martigny-verbier/2026-04-08/`) used in manual testing.
- **Danger levels:** map-coverage bulletins are `moderate`; the CH-4115 detail
  month cycles `low`→`considerable` so the calendar shows a colour gradient.
- **Day structure:** all bulletins are `all_day` single-period. No split-day
  (am/pm separate) or banded (ALBINA-style elevation-band) bulletins.
- **Multi-problem days:** each bulletin carries a single `persistent_weak_layers`
  problem; wet-snow and dry/wet mixed days are absent.
- **Point weather / favourites:** 5 `ForecastPoint`s near Verbier, each with a
  `ForecastPointWeather` per April date, and one `Favourite` per point owned by
  the seeded normal dev user (`dev@snowdesk.dev`).
- **Accounts:** the superuser and the subscribed normal dev user (see
  [Dev credentials](#dev-credentials)).

### Known gaps

The following scenarios are not covered by the seed and require either a
locally fetched bulletin or additional setup:

- ALBINA provider (multi-region, banded elevation danger, split-day)
- Météo-France provider (massif-level bulletins)
- High danger ratings (3 Considerable / 4 High / 5 Very high)
- Wet-snow problem days
- Split-day (morning/afternoon) danger profiles
- Off-season "no bulletin" regions
- `regions.RegionAlias` rows (SNOW-409) — the seed is CH-only and does not
  include them; some curated aliases target AT/IT regions that don't exist in a
  fresh worktree DB at all. To exercise `mcp_server.resolvers.search_places`
  against the curated aliases (e.g. to reproduce a "Sitten" → CH-4121 style
  query locally), load the EAWS fixtures the alias rows' natural keys depend on
  first:

  ```bash
  uv run python manage.py loaddata \
      regions/fixtures/eaws_CH.json \
      regions/fixtures/eaws_FR.json \
      regions/fixtures/eaws_AT.json \
      regions/fixtures/eaws_IT.json \
      regions/fixtures/region_aliases.json
  ```

### Changing the dataset

The dataset shape lives in code, not a committed fixture:

- Bulletin/weather coverage, the CAAML payload template, and the danger
  gradient are the module-level helpers in
  `bulletins/management/commands/seed_test_data.py`.
- Row *values* come from the factories in `tests/factories.py`.

After changing either, re-run `monitor_query_counts` (read-only) to verify the
baseline (home=8, map=7) is unchanged. To refresh the region reference data
itself, use `dump_resorts_fixture --commit` (resort → MicroRegion mappings) or
`refresh_eaws_fixtures --commit` (EAWS MicroRegion boundaries).
