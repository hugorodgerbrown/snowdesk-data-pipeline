---
name: map-defaults-to-today-not-the-last-rated-day
description: Why a bare / on the map page shows today (readDisplayDate, data-today, map_shared.js) and never the last rated day
status: current
last-reviewed: 2026-09-02
---

# The map defaults to today, never to the last rated day

**SNOW-793.** A bare `/` shows today. `?d=` still wins outright. The
default is not written to the URL.

This looks like a revert of SNOW-660 and is not one. The distinction is
the whole decision, so it is written down here rather than left to be
re-litigated the next time the off-season map looks wrong.

## What SNOW-660 actually removed

Not "today". The boot default it deleted was `effectiveTodayKey` — the
latest date in the ratings cache carrying a visible country, derived by
walking `sortedDates` backwards. Two properties made it a bug:

1. **The map picked it.** It came from the shape of the data, not from
   anything the visitor did or asked for.
2. **Nothing named it.** `#map-date-ribbon` did not report it, so the
   choropleth was coloured for a day with no on-screen explanation.

Off season those combine into the reported symptom: one coloured polygon,
standing for some day in May, on a map opened in September.

SNOW-660's fix was to commit nothing and say so — "No date selected"
beside an uncoloured map. Correct, and it left the common case worse: in
season, a visitor opening an avalanche map got a grey map and a prompt,
and had to scrub before seeing anything.

## Why today is a different proposition

Today fails both of the properties above, which is exactly why it is
allowed back:

1. **The visitor's own frame of reference.** "What is the danger today"
   is the question the site exists to answer. Defaulting to it is
   answering the question asked, not inventing one.
2. **It is named.** `#map-date-ribbon` reads today's date on first paint.
   The map is coloured *and* explained, which the derived date never was.

So the rule is not "restore the default". It is: **the default may only
ever be today.** A future change that reaches for the last populated day,
for `min(today, seasonEnd)`, or for anything else derived from the
ratings payload is reintroducing SNOW-660's bug, whatever it is called.
`tests/js/test_map_scrubber_no_boot_snap.js` and
`tests/js/test_map_boot_date_paint.js` both assert the derived date's
absence for that reason.

## Today unconditionally, off-season included

Off season today has no ratings and sits past the narrowed `season_end`.
The default still commits it: the thumb clamps to the end of the track,
the choropleth stays uncoloured, and the ribbon names today.

The alternative considered and rejected was falling back to the last
populated day when today has no data — which is precisely the derived
date, reached by a condition instead of unconditionally. An uncoloured
map under today's date is an honest statement that there is no bulletin
for today. A coloured map under a date from four months ago is not.

(The off-season map has a separate, real legibility problem — it says
little about *why* it is empty. That is SNOW-788's, and it is a copy
problem, not a reason to paint the wrong day.)

## The default is not written to the URL

`commitDate(todayKey, { silent: true })` — no `writeUrlDateParam`.

A bare URL *means* today, so a link copied off a defaulted map still
shows the current day when it is opened next week. Writing `?d=` on boot
would freeze every shared bare link onto the day it happened to be
copied, which is the opposite of what someone sharing "the map" wants.
Visitor-driven commits still write `?d=`, today included: a day someone
chose has to survive a reload even when it coincides with the default.

The same reasoning governs the popstate handler. A back step onto a
dateless URL commits today, so that URL renders one map however it is
reached — arriving fresh and stepping back to it must not differ.

## Where the precedence lives

`readDisplayDate()` in `static/js/map_shared.js`, one definition:
`readUrlDateParam() || readTodayDateParam()`. Every boot-time date read
goes through it — `map.js`'s `currentDisplayedDate` seed and its boot
ratings leg, both `repaintDateForStyleSwap` call sites,
`map_season_ribbon.js`'s `dateKey` seed, `map_scrubber.js`'s boot and
popstate paths.

Today is read from the scrubber's server-rendered `data-today`, never
from `new Date()`. The season bounds, the ribbon track and the thumb are
all positioned against the *server's* day; a second "today" off the
client clock would put the thumb and the painted day on different dates
for anyone a timezone away.

`null` is still reachable — no scrubber, or a malformed `data-today` —
and still means "paint nothing". That is what keeps SNOW-660's
uncoloured state alive as the honest fallback rather than deleting it.

## What the default does not wait for

The scrubber commits today **synchronously**, not behind
`getSeasonRatings()`.

The season payload is fetched on every cold load regardless — the
scrubber's own init pulls it to leave the loading state (SNOW-234) — so
this is not about saving a request. It is about ordering and failure:
the default's output is the thumb and the announcement, `map.js`'s
separate single-date leg paints the choropleth, and waiting on the season
promise would leave the ribbon reading "No date selected" over an
already-coloured map for the length of that fetch, and forever if it
fails. Two tests pin that, one per failure mode.

(`docs/map-and-api.md` claimed the season payload was lazy until first
scrubber interaction until SNOW-793 measured it. It never was.)
