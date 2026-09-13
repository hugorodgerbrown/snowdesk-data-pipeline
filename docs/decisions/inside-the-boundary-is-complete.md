---
name: inside-the-boundary-is-complete
description: Why an area download picks its content by crude rectangle, server-side (/api/area-content/, bboxes_overlap, areaBBox), never real geometry
status: current
last-reviewed: 2026-09-13
---

# Inside the boundary, everything. Outside, whatever is there.

**SNOW-924.** A downloaded area stopped at its tiles. It now carries the
content inside its boundary too — the bulletins for the regions it covers,
the weather for the locations in it, and the four overlay feeds.

This document exists for one paragraph of it: the selection is done by
**rectangle**, and that is a decision rather than a shortcut. Everything
below is here so the next reader does not "fix" it.

## The asymmetry the whole design turns on

**Under-fetching is the only defect.** A bulletin a user needed and does not
have is the failure this feature exists to prevent; it is discovered in a
car park with no signal, which is the worst possible place to discover it.

**Over-fetching is not a defect.** The content half is measured in
kilobytes against a tile half measured in megabytes. A region wrongly
included costs one HTML page.

Those two are not close to each other in cost, so the design does not treat
them as a trade-off to balance. It errs, deliberately and everywhere, to the
same side.

## What that buys

Three simplifications, each of which would otherwise be real work:

**The overlay feeds are not filtered at all.** Favourites, routes,
community reports and weather are one small request each, already covering
everything, so they are fetched whole. Narrowing one to the area would save
almost no bytes and would cost a per-area storage model — and overlapping
areas make that model ambiguous the moment two areas share a favourite. The
boundary decides what must be *verified present*, not what gets stored.

**The two sets that cannot be fetched wholesale are selected by rectangle.**
There are 461 micro-regions across the estate and roughly 550 public weather
locations, so bulletins and weather sheets do have to be narrowed. They are
narrowed by `bboxes_overlap` and `point_in_bbox`
(`apps/regions/services/area_content.py`), both inclusive at the edges. No
point-in-polygon, no polygon clipping, no shared-edge or antimeridian
handling — none of which is written, and none of which can therefore be
wrong.

**A region download needs no stored boundary.** SNOW-583 replaced a region
record's `bbox` with `z`, the tile rows its download was clipped to, on the
reasoning that the region id is the whole definition. `areaBBox` derives a
rectangle back out of those rows via `zoomRows` and `tileBounds`, and
because tile edges bound the region's real boundary, the derived rectangle
is *larger* than the region — again the correct side.

## The invariant, and where it is pinned

> For any area, the rectangle selection is a **superset** of the selection
> real geometry would make.

A rectangle test can only ever over-select, so this holds by construction —
but only while every step in the chain keeps over-stating rather than
tightening. `tests/public/test_area_content_api.py` pins it against all 149
real CH micro-region boundaries, sweeping generated rectangles across the
country at three sizes and asserting that every region with a boundary
vertex inside the box is selected. The client's own half of the chain —
framed bbox → blob → derived rectangle — stays pinned in
`tests/js/test_basemap_download_core.js`, because that is where a wrong
Mercator inverse would hide.

It is checked to fail: tightening the box by 0.01° in each direction breaks
the sweep.

### The invariant is about the test, not the candidate set

**SNOW-931.** A superset of *what you looked at* is not a superset of what
exists, and SNOW-924 shipped believing otherwise. `areaContentPlan` is
handed candidate features by `assembleAreaContentURLs`, which read them
from `snowdeskMapState.featureByRegionId` — the regions the client happens
to have loaded. That set is built lazily: boot fetches Switzerland, then
the active basemap's declared countries un-awaited, and `swisstopo_*`
declares `ch` alone. A Swiss border area resolved to **zero French
bulletins**, because France was never a candidate for the rectangle to
over-select.

Two things let it through, and both are worth remembering:

- **Every fixture was complete.** The sweep above hands `areaContentPlan`
  all 149 CH features directly, and the wiring test preloads one whole
  `regions.geojson`. A test that supplies the input cannot discover that
  the real caller supplies less of it.
- **The failure reported success.** Weather comes from one global feed
  that is fetched whole regardless of country, so the run still tallied
  `ok === total`, stamped `contentAt`, and painted the roundel green. The
  `partial` state could not catch it.

SNOW-931's fix was to load every country and await it
(`pwaMapCountries.ensureAllLoaded`) before reading the lookup.

### And then the candidate set stopped being the client's problem

**SNOW-953.** Awaiting all four countries is 764 KB over the wire, on
production, to discover roughly 55 KB of pages — fourteen times the content
in discovery cost, on the thin connection this feature exists to serve. The
selection moved to the server, which holds every boundary already:
`/api/area-content/` takes a bbox and answers which micro-regions it covers
(`{id, slug}`) and which public weather locations sit inside it
(`{short_id}`). The client composes the urls from that plus its own day
window (`areaContentURLs`).

Three things follow, and the middle one is the point:

- **The rule did not change.** `apps/regions/services/area_content.py` runs
  the same edge-inclusive rectangle test over real boundaries, with the
  same over-inclusive contract. The Python predicates are the JS ones,
  moved; a golden vector in `tests/regions/services/test_area_content.py`
  pins the behaviour they inherited.
- **SNOW-931's failure mode is no longer possible.** There is no
  "which countries are loaded" state for the answer to depend on: the
  candidate set is `MicroRegion.objects` under the same filter
  `regions.geojson` is built from, every request. A client cannot
  under-select from an answer it did not make.
- **A plan that cannot be made is SHORT, not empty.** One request replaced
  four, so an endpoint that does not answer loses everything rather than
  one country — and every url the run does fetch still lands, so no tally
  can see it. Both refresh paths record `contentIncomplete` on the area
  before returning, which is what keeps SNOW-932's "a shortfall survives
  the next repaint" true for this failure too.

`tests/js/test_map_download_content_countries.js` still pins the border
case, with a fixture whose region feed is keyed on `?country=` under a
CH-only basemap — the ordinary Alpine configuration. It now also asserts
that France's outlines are **never fetched**: the French bulletin arrives
without them.

This mirrors `test_basemap_tiles.py`'s
`test_clip_ranges_is_a_subset_of_the_candidate_rectangle`, which makes the
same argument at the other end of the pipeline and also runs against every
real boundary rather than a hand-built one.

## The day window

A download takes each region's bulletin for a window of days, not one day.
Forwards it runs to the last published day, resolved from the season
payload the scrubber already holds (`latestKnownDate`, SNOW-927) — "is
tomorrow's bulletin out yet" is a fact about the pipeline, not a
preference. Backwards it runs `OFFLINE_CONTENT_PAST_DAYS` days (SNOW-953,
default 3), rendered onto the page as `data-content-past-days`.

Backwards is a setting because it is a judgement: yesterday's bulletin says
what the snowpack has just been through, and forward-only carried tomorrow
but not yesterday. It is safe to be generous — a region with no bulletin
for a day renders a 200 empty state, so a day too far cannot fail a
download, only cost a page.

## What would have to change first

Someone will eventually want exact geometry here — it is the obvious
"improvement", and the code looks crude enough to invite it. The case for it
would have to start by showing that over-selection costs something real. As
of SNOW-924 it does not: a straddling region adds one page to a run that is
already fetching several hundred tiles. SNOW-953 changed *where* the
selection runs and left that unchanged — what it removed was the cost of
assembling the candidate set, which is a different objection and the one
that turned out to be real.

If the content half ever grows to where its size is the constraint — a
per-region payload measured in megabytes, say — then the trade changes and
this document is the thing to revisit. Until then, precision here buys
nothing and risks the one failure mode that matters.

## Not to be confused with

`intersectBBox` in `basemap_download_core.js`, which uses a strict `<` and
returns `null` for a zero-area overlap. That is correct for what it does —
it returns the overlapping *region*, and a shared edge is not one.
`bboxes_overlap` answers a different question ("might anything of this be
in that") and counts a shared edge, because the contract above says a page
is cheaper than a gap.

## See also

- [`offline-map.md`](../offline-map.md) — the download run and where the
  content phase sits in it
- [`a-downloaded-area-is-verified-by-what-it-renders.md`](a-downloaded-area-is-verified-by-what-it-renders.md)
  — the tile half's own completeness rule
- [`weather-day-picker-is-a-selector-not-navigation.md`](weather-day-picker-is-a-selector-not-navigation.md)
  — why a weather sheet is one undated URL per location and not one per day
