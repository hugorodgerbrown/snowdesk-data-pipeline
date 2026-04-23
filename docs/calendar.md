# Calendar and RegionDayRating

The bulletin page hosts a month-grid calendar, opened from the calendar
glyph in the top nav (see
[`nav_implementation_spec.md`](nav_implementation_spec.md)). The calendar
is a server-rendered HTMX fragment backed by a denormalised per-(region,
date) rating table — no JSON API, no per-day render-model reads at
request time.

**Model**: `pipeline.models.RegionDayRating` — one row per
`(region, calendar day)` with:
- `min_rating` / `max_rating` — `Rating` `TextChoices`
  (`no_rating`, `low`, `moderate`, `considerable`, `high`, `very_high`).
  Equal on uniform days, unequal on variable days — the calendar tile
  renders a diagonal split fill when they differ.
- `min_subdivision` / `max_subdivision` — the `+` / `-` / `=` suffix
  from the source bulletin's aggregate `danger.subdivision`, or `""`.
- `source_bulletin` — FK to the chosen `Bulletin` (nullable on
  `no_rating` days).
- `version` — `DAY_RATING_VERSION` at compute time; bump the service
  constant when the aggregation policy changes.
- `unique_together = (region, date)`; ordering `["-date", "region__region_id"]`.

**Aggregation policy** (see `pipeline/services/day_rating.py`):
- For day X, pick the single bulletin whose `_target_day` equals X with
  the latest `valid_from`. Morning-of-X (hour < 12) naturally wins over
  prior-evening-of-(X−1) (hour ≥ 12) because its `valid_from` is later.
  Evening-of-X (hour ≥ 12) targets X+1 and is excluded.
- Aggregate *within* that bulletin's `render_model["traits"]`: map each
  trait's `danger_level` (1–5) to a rating key; `max_rating` is the
  highest, `min_rating` the lowest.
- Empty traits (quiet day) → both fall back to
  `render_model["danger"]["key"]`.
- Malformed render model (empty dict; neither `danger` nor `traits`) →
  `no_rating`.
- Only qualifying bulletins are considered: `render_model_version >=
  RENDER_MODEL_VERSION` (v0 error sentinels excluded).

**Ingest hook**: `upsert_bulletin` calls
`apply_bulletin_day_ratings(bulletin)` inline after the render model is
built — never via `post_save`. Failures are logged and ingest continues
(the bulletin is still stored; the calendar tile picks up on the next
rebuild).

**Rebuild**: `rebuild_render_models` recomputes day ratings for every
`(region, day)` covered by the rebuilt bulletins as a trailing step.
Pass `--skip-day-ratings` to suppress that step when you only want to
refresh the render models (e.g. debugging a render-model bug without
touching the calendar).

**Calendar partial**: `public.views.calendar_partial` at
`/partials/calendar/<region_id>/<year>/<month>/` (name:
`public:calendar_partial`). HTMX-only — non-HTMX requests get 400. The
fragment wraps itself in `<div id="bulletin-calendar">` so prev/next
navigation swaps the outer element with
`hx-target="#bulletin-calendar" hx-swap="outerHTML"`. Year/month are
clamped to `[SEASON_START_DATE, today]` — out-of-range navigations
degrade silently rather than 404. An optional `?date=YYYY-MM-DD`
selects a specific tile for highlight rendering.

**Route ordering**: `partials/calendar/...` is registered before
`<str:region_id>/` in [`public/urls.py`](../public/urls.py). Same
top-to-bottom concern as `/map/` — don't reorder.
