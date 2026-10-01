---
name: calendar
description: RegionDayRating denormalisation and the day_rating v8 aggregation policy feeding the season heatmap season_calendar_partial HTMX fragment
status: current
last-reviewed: 2026-10-01
---

# Calendar and RegionDayRating

The bulletin page hosts a season-long heatmap grid, opened from the
season trigger in the top nav (`includes/nav.html` with
`season_trigger`; see
[`nav_implementation_spec.md`](nav_implementation_spec.md)). The grid is
a server-rendered HTMX fragment backed by a denormalised per-(region,
date) rating table — no JSON API, no per-day render-model reads at
request time.

**Model**: `apps.bulletins.models.RegionDayRating` — one row per
`(region, calendar day)` with:
- `min_rating` / `max_rating` — `Rating` `TextChoices`
  (`no_rating`, `low`, `moderate`, `considerable`, `high`, `very_high`).
  Equal on uniform days, unequal on variable days — the calendar tile
  renders a diagonal split fill when they differ.
- `min_subdivision` / `max_subdivision` — the `+` / `-` / `=` suffix
  from the source bulletin's aggregate `danger.subdivision`, carried
  through as stored, or `""` when the bulletin has none.
- `am_rating` / `pm_rating` (+ `am_subdivision` / `pm_subdivision`,
  SNOW-291) — the morning and afternoon peaks when the bulletin carries
  both `all_day`/`earlier` and `later` traits; `None` on uniform days.
- `source` — the chosen bulletin's `render_model["source"]`, and
  `bands` — the per-elevation-band breakdown for ALBINA bulletins
  (`[{band_id, label, rating_key, time_period}, …]`, SNOW-292), else
  `None`.
- `source_bulletin` — FK to the chosen `Bulletin` (nullable on
  `no_rating` days).
- `version` — `DAY_RATING_VERSION` at compute time (currently 9); bump
  the service constant when the aggregation policy changes. v9 changed no
  policy: it fixed the subdivision columns, which v8 and earlier stored as
  `""` on every row (SNOW-1054).
- `unique_together = (region, date)`; ordering `["-date", "region__region_id"]`.

**Aggregation policy** (v8 — see the module docstring of
`apps/bulletins/services/day_rating.py`):
- For day X, pick the single bulletin whose `target_date` equals X with
  the latest `valid_from`. Morning-of-X (hour < 12) naturally wins over
  prior-evening-of-(X−1) (hour ≥ 12) because its `valid_from` is later.
  Evening-of-X (hour ≥ 12) targets X+1 and is excluded. `target_date` is
  a stored column populated at ingest by `target_day_for_valid_from`.
- `min_rating` / `max_rating` are then resolved in this precedence
  order, within that one bulletin:
  1. **Elevation-band split** (SNOW-293): two or more
     `render_model["danger"]["ratings"]` entries with `period="all_day"`
     and distinct `key` values → lowest band key / highest band key.
  2. **Afternoon-elevated time split**: `morning_levels` from traits
     with `time_period in ("all_day", "earlier")`, `afternoon_levels`
     from `later` traits; if `max(afternoon) > max(morning)` →
     `min = max(morning)`, `max = max(afternoon)`.
  3. **Headline fallback**: both equal the bulletin's aggregate
     `render_model["danger"]["key"]`.
- `am_rating` / `pm_rating` are set whenever both trait buckets are
  non-empty, regardless of whether the afternoon is higher.
- Empty traits (quiet day) → the band-split check still runs (it reads
  `danger.ratings`, not traits); otherwise both fall back to the headline
  key.
- Malformed render model (empty dict; neither `danger` nor `traits`) →
  `no_rating`.
- Only qualifying bulletins are considered: `render_model_version >=
  RENDER_MODEL_VERSION` (v0 error sentinels excluded).

**Ingest hook**: `upsert_bulletin` calls
`apply_bulletin_day_ratings(bulletin)` inline after the render model is
built — never via `post_save`. Failures are logged and ingest continues
(the bulletin is still stored; the calendar tile picks up on the next
rebuild). The same call deletes the `season_calendar` fragment-cache key
for each affected region so the next open re-renders.

**Rebuild**: `rebuild_render_models` recomputes day ratings for every
`(region, day)` covered by the rebuilt bulletins as a trailing step.
Pass `--skip-day-ratings` to suppress that step when you only want to
refresh the render models (e.g. debugging a render-model bug without
touching the calendar). `recompute_day_ratings` recomputes the table on
its own.

**Season partial**: `apps.public.views.season_calendar_partial` at
`/partials/season/<region_id>/` (name: `public:season_partial`). The
sheet shell (`public/partials/_season_sheet.html`) is included on the
bulletin page; its empty `#season-grid` placeholder fires one HTMX GET on
first open (`hx-trigger="snowdesk:load once"`, SNOW-170) and the grid
(`public/partials/_season_calendar.html`, built by
`apps.public.season_calendar.build_season_grid`) is swapped in with
`hx-swap="innerHTML"`. The rendered body is cached for 25 hours under
`make_template_fragment_key("season_calendar", [canonical_region_id,
today])`, so a cache hit issues zero DB queries. The grid runs from
`settings.SEASON_START_DATE` to `today + 1` (the afternoon bulletin
targets tomorrow); the selected tile is highlighted client-side from
`data-selected-date`.

**Route ordering**: `partials/season/...` is registered before
`<region_id:region_id>/` in [`apps/public/urls.py`](../apps/public/urls.py). Same
top-to-bottom concern as `/map/` — don't reorder.
