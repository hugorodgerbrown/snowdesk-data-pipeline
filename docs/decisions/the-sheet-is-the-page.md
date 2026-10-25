---
name: the-sheet-is-the-page
description: Region and resort pages and map sheets share one body (_region_body.html, _resort_body.html, in_map); resort_date URL
status: current
last-reviewed: 2026-10-25
---

# The sheet is the page

## Decision

**A region and a resort each have one body, rendered in two containers.**
The page wraps it in the site's frame; the map's sheet wraps it under its
one-row peek (SNOW-1164, Hugo, 21 Oct 2026). On a phone the sheet at full
height therefore *is* the page; on a desktop it is the inset panel at the
map's right, for a region as for a resort (SNOW-1173).

| | Region | Resort |
|---|---|---|
| Builder | `views.build_bulletin_body` | `views.build_resort_body` |
| Body | `public/partials/_region_body.html` | `public/partials/_resort_body.html` |
| Page | `bulletin.html` (`bulletin_detail`) | `resort.html` (`resort_detail`) |
| Sheet | `_region_sheet_body.html` (`region_sheet_partial`, every width since SNOW-1173) | `_place_sheet_body.html` (`resort_sheet_partial`, every width) |

**A location's weather joined them (Hugo, 24 Oct 2026):** builder
`views._location_forecast_context`, body
`public/partials/_weather_body.html`, page `location_weather.html`, sheet
`_weather_detail.html` (`api.weather_detail`). The sheet opens the week on
the map's day; see [`weather-surfaces`](../weather-surfaces.md). Since 25
Oct 2026 both read one context, `views._weather_point_context`, and share
the masthead (`includes/_weather_masthead.html`) and the summary line
(`includes/_weather_day_summary.html`) as well as the body, which now holds
the "Where this is" rows: the page draws the sheet's strips, not the
meteogram it kept until then.

The sheet passes `in_map=True`. The body then drops what only makes sense
off the map — the region page's superuser debug block — and the resort body
keeps to one column, since the desktop inset panel is a phone's width. The
peek row names the object, so the page's title card, breadcrumb, "See on
the map" and Save stay in the page's frame.

The sheet follows the map's day, so **the resort page takes a day too**:
`/resorts/<slug>/<YYYY-MM-DD>/` (`resort_date`). The bare URL is today, the
dated URL for today redirects to it, and any other day is `noindex`.

## Why

On a phone the sheet at full height already filled the screen, so "Open
bulletin" or "View resort" led to a page with no more room and different
content: the same object, said twice, and drifting. SNOW-1161 split the
resort's facts between the sheet and the page by question ("this place on
the map's day" / "this resort today"); with one body there is nothing to
split.

## Consequences

- **A fact about a region or a resort is added to its body**, never to the
  page or the sheet alone. Both containers then show it.
- The sheets' own links onward ("Open bulletin", "Read the bulletin",
  "View resort") are gone; the body's own links stay — the resort body's
  region row leads to the region.
- The resort body carried the day's problems for a while (21 Oct 2026);
  since 25 Oct it carries no danger at all
  ([`a-resort-has-no-avalanche-danger`](a-resort-has-no-avalanche-danger.md)).
  A saved pin, which is one point, keeps its problems and their "Above /
  Below this location" verdict.
- The forecast strip lays itself out by its card's width (`@container`,
  `@md:`), not the viewport's, because it now sits in the inset panel too.
- A saved pin's place sheet is unchanged: it has no page.
- The address bar does not change while a sheet is open; back still closes
  it. A "you are not on today" marker is SNOW-1165: a "Showing … · Back to
  today" strip (`includes/_another_day_strip.html`) that the page renders
  full width under the nav, as a link to its bare URL. In a sheet the
  same fact is the peek's own date row (`includes/_sheet_date_row.html`,
  Hugo, 21 Oct 2026: "Data from 21 Oct 2026", highlighted on another day,
  with a button `static/js/map_today.js` turns into a scrubber move); it
  names the day once for every section, so the body's eyebrows drop their
  dates in the map (`in_map`). It is deliberately NOT in the shared body, because
  the way back differs by container: a page navigates, the map moves its
  day. The map itself carries an always-shown "Today" half on its date
  control, disabled on today.
- Supersedes the ownership table in
  [`the-map-opens-a-resort-in-the-sheet`](the-map-opens-a-resort-in-the-sheet.md)
  and amends [`a-resort-page-answers-the-query`](a-resort-page-answers-the-query.md).
