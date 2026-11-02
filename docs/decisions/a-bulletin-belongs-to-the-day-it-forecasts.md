---
name: a-bulletin-belongs-to-the-day-it-forecasts
description: A bulletin belongs to the day it forecasts; the latest BUILT issue shows (target_date, select_default_pick, IssuePick, is_built)
status: current
last-reviewed: 2026-11-02
---

# A bulletin belongs to the day it forecasts

**SNOW-1207.** A bulletin belongs to one day: the day it forecasts, stored
as `Bulletin.target_date` at ingest by `target_day_for_valid_from` (an issue
whose window starts from 12:00 UTC on forecasts the next day). A day shows
the **latest** issue that forecasts it: the morning update when there is
one, otherwise the previous evening's issue. The time of day plays no part.

**Latest that built (SNOW-1368, Hugo, 2 Nov 2026).** "Latest" means the
latest issue whose render model built at the current version
(`render_model_version >= RENDER_MODEL_VERSION`, `selection.is_built`), the
same issue `recompute_region_day` rates. A newer issue that failed to build,
or that the last rebuild did not reach, is passed over, and the body names
both issues' times in a notice (`select_default_pick` returns it as
`IssuePick.skipped`). Only when no issue for the day built is the latest
unbuilt one shown, with its error card. The fallback never leaves the day:
every candidate forecasts it. An explicit `?issue=` still shows the issue it
names. Until then the chip and map showed the rated (built) evening issue
while the body showed the failed morning update's error card, and the map
painted that region's steep ground as described by nothing.

The rule is in one place for every surface:

- the map fill and the calendar read `RegionDayRating`, which
  `recompute_region_day` builds from `Bulletin.objects.for_target_date`;
- the region and resort panels, the bulletin and resort pages, problem
  terrain, the place sheet, favourites and the MCP tools read
  `apps.bulletins.services.selection` (`issues_for_date`,
  `select_default_issue`, `select_default_pick`), which filters on the same
  column. `/api/problem-terrain/`'s batched `_page_picks` applies the same
  built-only threshold on the values it fetches.

## Why

Until SNOW-1207 the panels used another rule. They took every issue whose
validity window *touched* the day and, on today, preferred the one in force
right now. SLF's evening issue is valid from 17:00 on D to 17:00 on D+1. So
after 17:00, every evening of the season, the panels showed tomorrow's
issue labelled with today's date, while the map and calendar kept today's.
On 23 Oct 2026, the season's first issue (out at 17:00, forecasting 24 Oct)
showed in the panel on the 23rd over a grey map.

The "in force now" rule, which is how SLF's own site works, was declined (Hugo, 23 Oct 2026). It would change
today's colour every evening at 17:00, and the calendar could not follow it
without special-casing today. SNOW-1204 had already settled that the
afternoon issue is tomorrow's.

## Consequences

- After 17:00, today still shows today's bulletin; tonight's is one step
  forward on the date row. Any hint that tomorrow's is already out belongs
  to the notification surface, not to this rule.
- A day that only has an evening issue touching it, such as the eve of a
  season's first bulletin, is empty.
- The bulletin page's `?issue=` picker offers only the issues that forecast
  the day.
- A new surface must call `selection`, never query the validity window.
