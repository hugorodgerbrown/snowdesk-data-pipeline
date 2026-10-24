---
name: the-plan-page-reads-like-the-map
description: route_profile.js, _route_profile.html, _trip_when.html, plan_week, trip_danger, trip_route_profile.js; Plan page tiles, trip page profile
status: current
last-reviewed: 2026-10-24
---

# The Plan page reads like the map: one profile, and the week as tiles

## Decision

SNOW-1215 restructured the Plan page (`trips:new`) and the trip page's
"Edit this trip" form, which is the same form.

1. **One route profile.** The profile the map's route panel draws (one
   fill per leg under one outline, distance ticks along the foot, and a
   cursor line with its readout) is now its own component:
   `templates/includes/_route_profile.html`, drawn by
   `static/js/route_profile.js`. The route panel (`route_rail.js`) mounts
   it on its lane and keeps only what places the point (taps and scrubs).
   The Plan page's route card draws it through
   `static/js/trip_route_profile.js`, and the trip page will too
   (SNOW-1216). The lane's two CSS rules moved from `map.css` to
   `src/css/main.css`, because the trip pages do not load the map's
   stylesheet.
2. **The route's figures are one line.** `includes/_route_meta_line.html`
   renders "12.9km · 337m ↑ · 1906m ↓ · 2h51m" with the routes list's
   msgids, and the route card and the trip map use it. The routes row keeps
   its own copy because it needs the line as a variable to compose
   ("Shared with you · …"), which an include cannot hand back.
3. **The form is three numbered sections and a footer:** 1 When, 2 Where
   you meet, 3 Details (optional). The footer reads the plan back as it
   is chosen, beside Cancel and Create, at the end of the form. It is not
   sticky: the first build floated it, nothing else in the product
   floats, and Hugo chose a static footer (2026-10-24). The Plan page frames each section as a card.
   Inside the trip page's edit disclosure, which is already a card, a rule
   divides them instead (`framed`).
4. **The day is chosen from tiles.** There are five tiles (today and the
   four days after it) and an "Other" tile, in the weather day picker's
   pattern. The checked tile gets an accent border and a marker bar; it is
   never filled black. The first build had seven tiles, to match the
   weather picker's seven columns; eight columns squashed every tile on a
   phone, and Hugo cut the week to five (2026-10-24). A later day is one
   tap away on "Other", and only the first two tiles ever carry a level.
   Every tile is a `name="date"` radio, so the form posts without a
   script. "Other" lays a native date input over itself.
   `trip_form.js` copies a picked day into the Other radio, or onto the
   day's own tile when the week has one.
5. **A tile shows the danger level only when a bulletin rated that day.**
   The level is the route start region's `RegionDayRating.max_rating`
   (`apps/trips/services/plan_days.py`). Most tiles show nothing, because a
   bulletin covers only today and, after the evening issue, tomorrow. That
   is the intended reading: there is no "no bulletin" chip.
6. **The trip name's placeholder is the route's name.** It is no longer
   prefilled as the value, because a trip left unnamed is called after its
   route (`Trip.display_name`). The note's placeholder is an example.

## The trip page (SNOW-1216)

The trip page (`trips:detail`, and the share page, which renders the same
context) follows the same rules.

1. **The route card is the route.** It names the route (not the trip,
   which is the page heading), carries the meta line with the duration
   added, and draws the shared profile. The details card's Distance ·
   Ascent · Descent · Time row went: it was a second spelling of the
   same figures. `_trip_stats.html` stays for the trips list card.
2. **The profile scrubs, and the map follows.** A press or a drag on the
   lane places the point (`trip_route_profile.js`, `data-trip-route-scrub`),
   and the index is announced as a `trip:route-cursor` event that
   `trip_map.js` turns into a dot with `route_cursor_map_core.js`, the map
   page's own conversion. There is no point card: the page is a plan to
   read, and "View on the map" is where the terrain is read.
3. **The danger line is the Plan tile's, for one day.** `trip_danger`
   uses the same region rule and `RegionDayRating` row as the tiles. The
   plan card shows "Considerable (3) in Martigny-Verbier for Sunday · Read
   the bulletin →" only on a rated day, and has no strip otherwise.

## Why

Hugo's review of the iPad Plan and trip pages (2026-10-24) made two
points. The profile on the trip pages was "different from the map
version". The day field, a native date input, was malformed on an iPad and
showed the selection in black, where the product already has a pattern
for a selected day. Giving each surface its own drawing is how the two
profiles diverged in the first place. Extracting the drawing means the
route panel's own tests now exercise the same code the trip pages run.

The level is shown "if relevant": on today and, after the 15:00/17:00
issue, tomorrow. Its absence on the rest of the week is graceful
degradation, not a gap to fill.

## Consequences

- A change to how the profile draws is made once, in `route_profile.js`,
  and reaches the map, the Plan page and the trip page together.
- The route start's region is looked up on every render of the form (one
  query plus a polygon test, `region_for_point`), with one more query for
  the week's ratings.
- `TripForm.date` keeps its `DateInput` widget as the field's definition,
  but no template renders it any more. The radios are the control.
