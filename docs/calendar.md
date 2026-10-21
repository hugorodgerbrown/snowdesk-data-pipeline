---
name: calendar
description: RegionDayRating, day_rating policy, the shared season calendar (_season_calendar.html, build_season_months), season_calendar_partial
status: current
last-reviewed: 2026-10-21
---

# Calendar and RegionDayRating

The bulletin page hosts a season-long calendar, opened from the season
trigger in the top nav (`includes/nav.html` with `season_trigger`; see
[`nav_implementation_spec.md`](nav_implementation_spec.md)). The calendar
is a server-rendered HTMX fragment backed by a denormalised per-(region,
date) rating table — no JSON API, no per-day render-model reads at
request time.

**One component** (SNOW-1158). Every season calendar — the resort page's
season section, the bulletin page's season sheet, the /help/ illustration
and the component library (`season-calendar` and `season-cell`) — is
drawn by `public/partials/_season_calendar.html`, from the design
system's SeasonCalendar / SeasonHistory boards. There is no second copy
of its markup or its CSS (`.season-cal`, `.season-day` in
`src/css/main.css`), so the pages cannot drift.
- **Shape** — `build_season_months(grid, today, window=None,
  ring_date=None)` reshapes the week-column `SeasonGrid` into
  `SeasonMonths`: one `SeasonMonth` per month of the span, each with one
  `MonthDay` per day of that month — 28 to 31, February 29 in a leap year
  — plus the 31-slot axis labels (1, 6, 11 … 26, the first day of each
  group of five) and the four-digit season
  name ("2025/26").
- **Span** — from the season's start (the window's start, or the grid's
  first day) through that season's April, past April only to the last
  month holding a bulletin, and never beyond tomorrow's month
  (`_last_drawn_day`). So out of season both surfaces draw Nov–Apr — the
  summer months the bulletin page's grid also covers are not drawn — and a
  season with a May bulletin draws through May.
- **Layout** — months are rows (a 36px label column, 31 dot slots, the
  axis row above); a container query under 30rem re-lays the same markup
  as month columns of 10px bars with the axis down the left. A container
  query, not a media query, because the calendar decides by its own
  width: a ~700px resort column, a 640px sheet. In both layouts the days
  are grouped in fives (SNOW-1163) — 1–5, 6–10 … 26–31, the last group six
  days long — by an 8px gap before days 6, 11, 16, 21 and 26: a taller
  margin on a phone, a gap track in the wide grid. CSS draws it with
  `:nth-child`, so the markup and the cached fragment are unchanged.
- **Cell states** (`data-state`) — `none` (no bulletin: outlined), the
  five rating keys (filled), `split` (two levels in one day: `data-lo`
  left, `data-hi` right) and `future` (not yet: a day after today with no
  bulletin, faint). A day holds two levels when its `min_rating`, its
  AM/PM peaks or its ALBINA band ratings include a level below its peak;
  the lower half is the lowest of them and the upper half is always the
  peak, `max_rating` — the [compressed-views rule](compressed-views-rating-rule.md).
  `data-level` is the peak on every cell. The AM/PM direction and the
  ALBINA band geometry the old heatmap drew (left/right by time, top/bottom
  and 2×2 by band) are no longer drawn — the day's title names both levels.
- **Ring** — `.season-day-ring`, a 2px `--color-text-1` ring, always on
  the day the page shows (SNOW-1163). On the resort page it is
  `target_date` — today, or the dated URL's day — passed as `ring_date`,
  and absent when that day is not drawn; SNOW-1163 removed
  `season_ring_date` and its off-season "first High day" ring. On the
  bulletin page it is the page's own day, added client-side (below).
- **Variants** — unlinked (resort, help, library): the calendar is one
  `role="img"` whose `aria-label` is the season summary ("Season 2025/26:
  150 days with a bulletin, 40 at Considerable or higher."), each cell has
  a `title` ("14 Jan 2026 · Considerable"), nothing is a link, and the
  key reads No bulletin, Low, Moderate, Considerable, High (Very high
  only when a day reached it). Linked (bulletin sheet): each day with a
  bulletin is a link to that day with an `aria-label`, and the calendar is
  not an image (its links would leave the accessibility tree). Both
  variants share that one key — colours only, no counts, no entries for
  the split cell or the ring (SNOW-1163, which retired the linked
  variant's counted key).

**The resort page's season calendar** (SNOW-1126, redrawn by SNOW-1158).
`/resorts/<slug>/` renders the calendar server-side and inline rather than
as a deferred fragment, because its summary is the page's indexable
account of the season and a crawler never fires the client trigger. The
cost is one indexed `RegionDayRating.objects.for_region_range` query;
there is no `{% cache %}` around it, so `apply_bulletin_day_ratings`'
invalidation has nothing to add. It sits in a card under the eyebrow
"Avalanche danger • {region} region • {season}" — the bulletin card's own
"{region} region" wording.
- **Window** — `season_window(today)` in `apps/public/season_calendar.py`
  returns a `SeasonWindow(start, end, label)`: a season runs 1 November
  to 30 April (`SEASON_OPENS` / `SEASON_CLOSES`), and from 1 May to
  31 October the window is the season just ended. The start is clamped
  to `settings.SEASON_START_DATE`; the label is the nominal season's.
  `build_season_grid(region, today, window=…)` runs from the window's
  start to the earlier of its end and `today + 1`; the month view draws
  the rest of a running season as `future`. The resort page passes the
  window with its end moved to 31 October, so a bulletin after 30 April is
  read, drawn and counted. Without a `window` the grid is the bulletin
  page's, unchanged.
- **Summary** — `summarise_season(grid)` counts the days with a bulletin,
  the days per level, and the days at Considerable or higher. There is
  deliberately no season peak: nearly every resort reaches 3 or 4 in a
  winter, so it carries no information (SNOW-1153). SNOW-1158 removed the
  visible summary sentence and level-count list; the summary survives as
  the calendar's `aria-label`. A falsy summary (no day with a bulletin)
  omits the whole section.

**Model**: `apps.bulletins.models.RegionDayRating` — one row per
`(region, calendar day)` with:
- `min_rating` / `max_rating` — `Rating` `TextChoices`
  (`no_rating`, `low`, `moderate`, `considerable`, `high`, `very_high`).
  Equal on uniform days, unequal on variable days — the calendar cell
  draws a two-level split (lower left, peak right) when they differ.
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
rebuild). The same call deletes the `season_calendar` cache key for each
affected region, in every `settings.LANGUAGES` language, so the next open
re-renders. On an update, `upsert_bulletin` first captures the stored row's
`(region, day)` pairs with `day_rating_pairs` and passes them as
`prior_pairs`, so a correction that drops a region or moves off a day
reconciles the rating it leaves behind (SNOW-1168).

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
first open (`hx-trigger="snowdesk:load once"`, SNOW-170) and the linked
calendar (`public/partials/_season_calendar.html`, from
`build_season_grid` → `build_season_months`) is swapped in with
`hx-swap="innerHTML"`. The rendered body is cached for 25 hours under
`season_calendar_cache_key(canonical_region_id, today, language)` in
`apps/bulletins/services/day_rating.py`, so a cache hit issues zero DB
queries. The language is in the key because the body is rendered text:
the month labels are Django's own translated month names (`MONTHS_3`) and
the cell labels are `blocktrans` sentences (SNOW-1128). The key also carries
`SEASON_CALENDAR_MARKUP_VERSION`: bump it whenever the calendar's markup
changes shape, or a deploy serves the previous release's cached body
against the new CSS for up to 25 hours (SNOW-1163). The grid runs from
`settings.SEASON_START_DATE` to `today + 1` (the afternoon bulletin
targets tomorrow); the calendar draws only the span above. The body is
the same for every day of the season: nothing in it is per page.

**The page's own day** (SNOW-1158, made one-tap by SNOW-1163). The
bulletin page's day is carried as `data-selected-date` on `#season-grid`;
after the swap `static/js/season_sheet.js` adds `.season-day-ring` and
`aria-current="date"` to that day's cell. Every rated day is a plain link,
and nothing intercepts the click, so one tap opens that day's bulletin.
SNOW-1158's select-then-read readout panel ("Read the bulletin for …")
was removed by SNOW-1163: it cost a second tap to reach a bulletin. The
sheet's header carries the resort page's eyebrow, its season named by
`season_header`'s `season_name`. There are no season tabs: the page has
never offered a second season.

**Route ordering**: `partials/season/...` is registered before
`<region_id:region_id>/` in [`apps/public/urls.py`](../apps/public/urls.py). Same
top-to-bottom concern as `/map/` — don't reorder.
