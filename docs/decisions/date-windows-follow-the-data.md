---
name: date-windows-follow-the-data
description: Date windows follow bulletin runs, not a fixed season (rated(), latest_run, SEASON_GAP_DAYS, scrubber_on_track, MCP 366-day cap)
status: current
last-reviewed: 2026-11-05
---

# Date windows follow the data, not a fixed season

**SNOW-1431.** Every public date window used to be a fixed avalanche season:
the map scrubber's track was 1 November – 31 May narrowed to the rows inside
it, the bulletin page's calendar ran from `settings.SEASON_START_DATE` and
was padded to 30 April, the resort strip and its fixtures used
`season_window` (1 November – 30 April), and the MCP `get_danger_history`
tool clamped every range to 1 November – 31 May. Providers do not publish to
that calendar. SLF issues October bulletins after an early snowfall, and
ALBINA and Météo-France start and stop on dates of their own. An October
bulletin fell off the scrubber, every range crossing 1 November was cut in
two, and from June to October the map hid the scrubber and showed a
"Between seasons" note naming a month the data had not promised.

Hugo chose "behaviour and copy" on 2026-11-05: the windows follow the data,
and user-facing "season" copy goes with the fixed season it described.

## One rule for a bulletin day

A **rated row** is a `RegionDayRating` with `source_bulletin` set and
`max_rating` other than `no_rating`: `RegionDayRatingQuerySet.rated()`. It is
the test `build_season_grid` already used for `has_bulletin`. A `no_rating`
row is written for a (region, day) with no qualifying bulletin, and a
provider can publish a `no_rating` bulletin. Neither is a day a window should
stretch to.

Every window below reads `rated()`, and so does the `season_start` alert's
`had_bulletin_in_season`. Before this change the alert counted any row with a
bulletin, so a region whose last 60 days held only a `no_rating` bulletin got
"Bulletins are back." while the windows treated it as quiet. Now both say the
same thing, and that region gets `season_start`.

## A bulletin run

A **bulletin run** is a stretch of rated days with no gap of more than
`SEASON_GAP_DAYS` (60, in `apps/bulletins/models.py`) empty days between two
consecutive rated dates. The gap counts the days strictly between them,
`(later - earlier).days - 1`. Rated days on 1 March and 1 May have 60 empty
days between them and stay one run; one more empty day splits them.

This is the alert's arithmetic too. `had_bulletin_in_season(region, day)` is
called with the day before a new bulletin and looks back `SEASON_GAP_DAYS`
days, so the bulletin is a resumption exactly when the previous rated day is
at most 61 days earlier, that is, at most 60 empty days between. The constant
moved from `services/alerts.py` to the models module so the queryset can read
it without an import cycle. The alerts module imports it from there.

`latest_run()` reads distinct rated dates newest first, in one query, and
stops consuming them at the first gap that splits a run. The database still
evaluates the `DISTINCT` over every rated row, so the query's cost grows with
the archive. The map therefore reads it through `apps.map.views.cached_latest_run`,
a five-minute cache entry (`map:latest_run:v1`, the same TTL as the
single-date ratings payload) holding the run as ISO strings.
`first_rated_date(region)` is the region's earliest rated day.
`earliest_date` is unchanged, because it counts `no_rating` rows and the
bulletin page's day navigation relies on that.

## The windows

- **Map scrubber and timelapse** (`apps/map/views.py` `_base_map_context`):
  from the first day of `latest_run()` (or today, with no rated row) to the
  later of the run's last rated day and today. Today is always on the track, so the
  scrubber is on screen on an unchosen map in every month. In August its
  track ends in an empty stretch up to today. Hugo chose that over ending
  the track at the run's last day ("latest run through today", 2026-11-05).
  `map_scrubber_reveal.js` stays as a generic guard: `data-on-track`
  (`scrubber_on_track`) is false only for a calendar pick before the run
  started.
- **Map "off-season"**: `is_offseason` means no rated row for today. That is
  data, not a date. It still moves the resort walkthrough to a day with a
  bulletin (`resort_guide.guide_date`). The "Between seasons" note and the
  welcome card's "live bulletins resume in November" line are deleted. Both
  promised a month that nothing in the data supported.
- **Bulletin calendar and ribbon** (`apps/public/season_calendar.py`): the
  grid and the ribbon start at the region's `first_rated_date`, and the months
  drawn run to the later of the last bulletin and tomorrow, with no padding to
  30 April. `season_header(region, today)` returns `{"first_date": …}` or
  `None`. It carries no season label, because a label like "25/26" names a
  fixed window. `season_window`, `SeasonWindow`, `SEASON_OPENS` and
  `SEASON_CLOSES` are deleted.
- **MCP `get_danger_history`**: a cost cap, not a season. The window holds at
  most 366 days, ending at `min(to_date, today + 1)`. A longer range is
  clamped from its start. `window_start` / `window_end` replace
  `season_start` / `season_end`, and `apps/mcp_server/season.py` is deleted.
  `get_danger_trend` inherits the cap.

`settings.SEASON_START_DATE` stays as `fetch_bulletins`' backstop on an empty
database. That is ingest-only and internal, and no public surface reads it.

## What still says "season"

A resort's own season ("Season opens", "Season closes", "Typical season"), the
"out of season" closure strings, the `season_start` alert kind, provider
issue-schedule strings ("during the main season") and internal names
(`season-scrubber`, `season_sheet`, `map_season_ribbon.js`,
`getSeasonRatings`, the `season_*` context keys and `data-season-*`
attributes). These describe resorts, providers or code, not a bulletin window.

## Consequences

- One rule (`rated()` and `SEASON_GAP_DAYS`) now drives the windows and the
  alert, so they cannot disagree about whether a day had a bulletin.
- The map page runs one distinct-dates query (`latest_run`) in place of the
  season aggregate, at most once per five minutes through the cache, plus an
  `EXISTS` for `is_offseason`. The bulletin page's
  `season_header` adds one indexed aggregate.
- Supersedes the fixed-window reasoning in
  [`map-defaults-to-today-not-the-last-rated-day.md`](map-defaults-to-today-not-the-last-rated-day.md):
  today is now always on the track, so the "thumb clamps to the end off
  season" case it describes no longer arises. Its rule that the default is
  only ever today is unchanged.
