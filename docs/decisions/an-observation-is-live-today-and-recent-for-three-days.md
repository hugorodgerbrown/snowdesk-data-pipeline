---
name: an-observation-is-live-today-and-recent-for-three-days
description: Field reports live today, recent 3 days, one day when scrubbed (community_reports_geojson ?d=, observed_on, reportState)
status: current
last-reviewed: 2026-10-29
---

# An observation is live today and recent for three days

**SNOW-1290.** The map's field reports ("On the ground") used to be a
rolling 48-hour window whose pins faded linearly with age. They are now
drawn in one of three states, read against the map's day:

- **Live**: made today (the local day, `settings.TIME_ZONE`). A solid pin in
  its family's colour, with a halo and its age ("12m", "3h") beside it.
- **Recent**: made in the three days before today. A hollow ring, no age.
- **Day**: when the map is scrubbed to another day, only that day's reports
  are drawn, solid, with no halo and no age. Nothing from either side of it.

`community_reports_geojson` takes `?d=` and returns the window for that day.
Each feature is one *report* (a `submission`, SNOW-1289), carrying every
type it ticked and its most serious family, and stamped with `observed_on`.
`static/js/community_reports_core.js` turns `observed_on` into a state.

## Why

- **The fade emptied the map.** A report made on the morning of a tour had
  faded to its floor by the next morning, and was gone the morning after.
  Few people report, so most of the time the map showed nothing, and the
  layer read as broken.
- **A fade does not say anything a skier can use.** A two-hour-old warning
  sign and a forty-hour-old one were drawn in two greys that were barely
  different. "Today" and "the last few days" are two different questions,
  so they get two shapes rather than a gradient.
- **The day is the reset.** Snow conditions change overnight: freezing,
  new snow, wind. A report from yesterday afternoon is old news by this
  morning in a way that a report from three hours ago is not. Midnight is
  the boundary skiers already use, and the one bulletins use.
- **Three days, not more.** Long enough for a midweek visitor to see the
  weekend's reports. Short enough that the layer stays a recent signal and
  never becomes a durable map of where reports cluster, which was SNOW-419's
  anonymity argument for a short window and still holds.
- **A scrubbed map shows its own day.** When someone looks at last Tuesday,
  they want what people saw last Tuesday. Live and recent mean nothing
  there, so the map uses one plain state.
- **The server owns the day.** It sends `observed_on` in its own timezone,
  so a browser in another timezone never redraws a report on the wrong day.

## Consequences

- The layer is **on by default**. With a day-based window it is a handful of
  pins, not a pile, and a crowd-sourced layer only works if people see it.
  Its layers-menu row moved to the Bulletins page as "On the ground".
- A date change re-stamps the held payload at once, then refetches with
  `?d=` after the scrub settles. Only today's answer is written to the
  offline cache. An offline read-back is re-stamped against the day it is
  read, so a cached report ages out exactly as it would online.
- A pin takes the colour of its most serious family (`leading_family`): a
  report that ticks a warning sign is a warning pin, whatever else it says.
  A cluster takes the colour of the most serious family inside it.
- `--color-marker-observation` (SNOW-672's amber flag) is gone. The three
  `--color-obs-*` tokens replace it, mirrored in `FAMILY_COLOURS`, and
  `tests/observations/test_family_colour_parity.py` holds the two copies
  together.
