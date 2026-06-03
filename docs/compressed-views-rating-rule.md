# Compressed-views rating rule

## The rule

Every compressed or summary representation of a region's danger rating — a
single chip, a choropleth colour, a calendar tile — **always shows the day's
peak danger rating** across all `validTimePeriods` (morning, afternoon, and all
elevation bands).

The peak is stored in `RegionDayRating.max_rating`. Compressed views must read
`max_rating`; they must never use `min_rating` alone as the single displayed
value.

## Why

The SLF bulletin interpretation guide specifies that when reading a single
danger level for a day (e.g. for a travel decision or a map choropleth), you
should use the highest rating that applies at any point during the day.
Showing only the morning (lower) level would under-represent the actual hazard
on days where conditions deteriorate in the afternoon.

## How peak is computed

`bulletins/services/day_rating.py` — `recompute_region_day` — implements the
split logic:

1. A single authoritative bulletin is selected for each (region, calendar day)
   pair: the morning-of-day issue if present, otherwise the prior-evening
   fallback.
2. Traits are partitioned into two buckets:
   - `morning_levels`: traits with `time_period` in `("all_day", "earlier")`.
   - `afternoon_levels`: traits with `time_period == "later"`.
3. `_resolve_min_max_keys` applies the split rule:
   - If `max(afternoon_levels) > max(morning_levels)` → split day:
     `min_rating = key of max(morning_levels)`,
     `max_rating = key of max(afternoon_levels)`.
   - Otherwise → headline-only: both `min_rating` and `max_rating` equal the
     bulletin's aggregate `render_model["danger"]["key"]` (the SLF-computed
     headline, kept in sync with the Day Risk Profile panel — SNOW-138).
4. On quiet days (no traits) both ratings fall back to the headline key.
5. On days with no qualifying bulletin both are set to `NO_RATING`.

The canonical two-period escalating fixture is **morning=2 (moderate),
afternoon=3 (considerable) → `max_rating`=considerable**. This is the
SNOW-252 regression fixture; see
`tests/bulletins/services/test_day_rating.py::TestPeakSemantics`.

## Surfaces covered by this rule

| Surface | Where | Field read |
|---------|-------|------------|
| Map choropleth | `public/api.py` `_build_ratings_payload` | `max_rating` via `_RATING_TO_INT` |
| Map tooltip (region summary chip) | `public/api.py` `region_summary` → `public/templates/public/_region_tooltip.html` | `day_rating.max_rating` |
| Season-trend calendar tiles | `public/season_calendar.py` `build_season_grid` → `public/templates/public/partials/_season_calendar.html` | `rdr.max_rating` as `max_rating_key` |

## Explicit exclusion

**Bulletin detail page hero** (`public/views.py` `bulletin_view`) — unchanged
by SNOW-252. The hero is a full-detail view that displays the complete
morning-and-afternoon breakdown (SNOW-246), not a compressed single chip.

## Cache invalidation chain

`RegionDayRating` rows are written by `apply_bulletin_day_ratings` (called
inline from `upsert_bulletin` after each ingest). The same function:

1. Calls `recompute_region_day` for every region linked to the bulletin,
   ensuring `max_rating` is up to date.
2. Deletes the `season_calendar` fragment cache key for each affected region
   so the next HTMX open re-renders with the freshly written row (see
   `make_template_fragment_key("season_calendar", ...)` in
   `apply_bulletin_day_ratings`).

The `ratings` endpoint at `public/api.py` uses a 5-minute server-side
`cache.get_or_set` keyed on `(country, date)`. After a new bulletin lands the
choropleth colour is stale for at most 5 minutes; the season calendar is
invalidated immediately.

## Regression tests

- `tests/bulletins/services/test_day_rating.py::TestPeakSemantics` — verifies
  that `recompute_region_day` produces `max_rating` = afternoon peak on a
  two-period escalating day.
- `tests/public/test_map_api.py::test_ratings_choropleth_emits_peak_for_split_day`
  — choropleth endpoint returns `3` (considerable) for a morning=2, afternoon=3
  fixture.
- `tests/public/test_map_api.py::test_region_summary_tooltip_chip_shows_peak_on_split_day`
  — tooltip chip `data-level` and `level` JSON key are both `"considerable"`.
- `tests/public/test_season_calendar.py::TestSeasonCalendarPeakSemantics` —
  calendar cell `max_rating_key` is the peak on a split day.
