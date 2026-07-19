---
name: worktrees
description: init-worktree worktree seed strategy, dev credentials, test_data fixture coverage, reseed procedure
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
uv run python manage.py loaddata test_data
uv run python manage.py seed_dev_users
```

**Why this instead of copying the main repo's DB?**

Copying the live dev DB produces a worktree whose data differs from CI's
fixture environment. That divergence repeatedly broke the SNOW-13
query-count baseline (home=8/map=7 under CI fixtures, lower off a copied
dev DB), causing churn on SNOW-341 and SNOW-342. Seeding from the
committed fixture guarantees every worktree is identical to CI's data
environment.

### Factory-based alternative: `seed_test_data`

`loaddata test_data` remains the bootstrap path (identical to CI). For an
ad-hoc dev DB you can instead build the same dataset from the FactoryBoy
factories, which also covers `ForecastPoint`/`ForecastPointWeather`/`Favourite`
rows the JSON fixture omits:

```bash
uv run python manage.py loaddata eaws_CH resorts   # region reference data first
uv run python manage.py seed_test_data --all --commit
```

It expects an empty/migrated DB and is not part of `bin/init-worktree`. See
[`management-commands.md`](management-commands.md) for the flag reference.

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

`seed_dev_users` creates two well-known accounts. Neither appears in
`test_data.json` so the fixture remains auth-free and CI's query-count
surface is unaffected.

| Role | Email | Password | Notes |
|------|-------|----------|-------|
| Superuser | `admin@snowdesk.dev` | `snowdesk` | Full `/admin/` access |
| Subscribed user | `dev@snowdesk.dev` | `snowdesk` | Active subscriber, subscribed to CH-4115 (Martigny-Verbier) |

The constants are defined in
`accounts/management/commands/seed_dev_users.py` (`SUPERUSER_EMAIL`,
`NORMAL_USER_EMAIL`, `PASSWORD`).

## Force-reseed procedure

To wipe and rebuild the worktree DB from scratch:

```bash
rm db.sqlite3 && bin/init-worktree
```

Existing worktrees that were set up before SNOW-345 (when the script
copied `db.sqlite3`) keep their copied DB until manually reseeded using
the command above.

## `test_data` fixture coverage

`bulletins/fixtures/test_data.json` is the single fixture loaded by CI
and by every fresh worktree. This section documents what it actually
contains so the fixture can be extended safely.

### Record counts

| Model | Rows |
|-------|------|
| `bulletins.bulletin` | 178 |
| `bulletins.regionbulletin` | 178 |
| `bulletins.regiondayrating` | 178 |
| `bulletins.weathersnapshot` | 178 |
| `regions.microregion` | 149 |
| `regions.subregion` | 21 |
| `regions.majorregion` | 9 |
| `regions.resort` | 148 |
| `auth.user` | **0** |
| `bulletins.pipelinerun` | **0** |

Total: 1,039 rows.

### Data coverage

- **Provider:** SLF only. The `render_model.source` field is `"slf"` on
  every bulletin. ALBINA and Météo-France are not represented.
- **Date span:** 1 April – 30 April 2026 (30 days). All dates fall in the
  spring off-season for Alpine snowpack; no mid-winter elevated-danger days
  are present.
- **Regions:** all 149 CH micro-regions have exactly one bulletin covering
  a single date (2026-04-08, the "map reference date") — enough to render
  the choropleth map.
- **CH-4115 (Martigny-Verbier):** 30 bulletins, one per day of April 2026.
  This is the canonical bulletin detail URL
  (`/ch-4115/martigny-verbier/2026-04-08/`) used in manual testing.
- **Danger levels:** all fixtures use `low` (1). No high-danger or
  `considerable`+ days exist.
- **Day structure:** all bulletins are `all_day` single-period. No
  split-day (am/pm separate) bulletins and no banded (ALBINA-style
  elevation-band) bulletins are present.
- **Multi-problem days:** no bulletin has more than one trait. Wet-snow
  problems and dry/wet mixed days are absent.

### Known gaps

The following scenarios cannot be tested with `test_data` alone and
require either a locally fetched bulletin or an extended fixture:

- ALBINA provider (multi-region, banded elevation danger, split-day)
- Météo-France provider (massif-level bulletins)
- High danger ratings (3 Considerable / 4 High / 5 Very high)
- Wet-snow problem days
- Split-day (morning/afternoon) danger profiles
- Off-season "no bulletin" regions
- `regions.RegionAlias` rows (SNOW-409) — `test_data.json` is CH-only and
  doesn't include them; some curated aliases target AT/IT regions that
  don't exist in a fresh worktree DB at all. To exercise
  `mcp_server.resolvers.search_places` against the curated aliases
  (e.g. to reproduce a "Sitten" → CH-4121 style query locally), load the
  EAWS fixtures the alias rows' natural keys depend on first:

  ```bash
  uv run python manage.py loaddata \
      regions/fixtures/eaws_CH.json \
      regions/fixtures/eaws_FR.json \
      regions/fixtures/eaws_AT.json \
      regions/fixtures/eaws_IT.json \
      regions/fixtures/region_aliases.json
  ```

### Extending the fixture

Run these commands (all support `--commit` to write; dry-run by default):

```bash
# Regenerate test_data from scratch (bulletins + weather + region ratings):
uv run python manage.py build_test_data --commit

# Regenerate resort → MicroRegion mappings after region changes:
uv run python manage.py dump_resorts_fixture --commit

# Re-fetch the canonical EAWS MicroRegion boundaries from the EAWS API:
uv run python manage.py refresh_eaws_fixtures --commit
```

If you need to snapshot additional models not covered by `build_test_data`,
use Django's `dumpdata`:

```bash
uv run python manage.py dumpdata <app_label.ModelName> \
    --natural-foreign --natural-primary \
    --indent 2 >> bulletins/fixtures/test_data.json
```

Commit the updated fixture alongside the PR that requires the new data.
Always re-run `monitor_query_counts` (read-only) afterwards to verify the
baseline (home=8, map=7) is unchanged.
