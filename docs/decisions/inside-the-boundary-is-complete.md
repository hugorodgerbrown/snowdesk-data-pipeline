---
name: inside-the-boundary-is-complete
description: Why an area download picks its bulletins and weather by crude rectangle (areaContentPlan, areaBBox, bboxesOverlap), never real geometry
status: current
last-reviewed: 2026-09-12
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
narrowed by `bboxesOverlap` and `pointInBBox`
(`static/js/basemap_download_core.js`), both inclusive at the edges. No
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
tightening. `tests/js/test_basemap_download_core.js` pins it against all 149
real CH micro-region boundaries, sweeping generated rectangles across the
country at three sizes and asserting that every region with a boundary
vertex inside the box is selected. A second case runs the real path — framed
bbox → blob → derived rectangle → selection — because that is where a wrong
Mercator inverse would hide.

It is checked to fail: tightening `featureBBox` by 0.01° in each direction
breaks the sweep.

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

So the plan now loads every country and awaits it
(`pwaMapCountries.ensureAllLoaded`, published from `map.js`) before it
reads the lookup. All four rather than the ones the rectangle overlaps:
the feeds are small, three are usually cached already, and a table of
country extents would be a second source of truth about where countries
are — hand-maintained, and able to be wrong in the direction that loses a
bulletin. Loading is not showing; SNOW-891 already separated the two, so
this changes nothing about what the map draws.

`tests/js/test_map_download_content_countries.js` pins it with a fixture
whose region feed is keyed on `?country=` under a CH-only basemap — the
ordinary Alpine configuration, not a contrived one.

This mirrors `test_basemap_tiles.py`'s
`test_clip_ranges_is_a_subset_of_the_candidate_rectangle`, which makes the
same argument at the other end of the pipeline and also runs against every
real boundary rather than a hand-built one.

## What would have to change first

Someone will eventually want exact geometry here — it is the obvious
"improvement", and the code looks crude enough to invite it. The case for it
would have to start by showing that over-selection costs something real. As
of SNOW-924 it does not: a straddling region adds one page to a run that is
already fetching several hundred tiles.

If the content half ever grows to where its size is the constraint — a
per-region payload measured in megabytes, say — then the trade changes and
this document is the thing to revisit. Until then, precision here buys
nothing and risks the one failure mode that matters.

## Not to be confused with

`intersectBBox` in the same module, which uses a strict `<` and returns
`null` for a zero-area overlap. That is correct for what it does — it
returns the overlapping *region*, and a shared edge is not one.
`bboxesOverlap` answers a different question ("might anything of this be in
that") and counts a shared edge, because the contract above says a page is
cheaper than a gap.

## See also

- [`offline-map.md`](../offline-map.md) — the download run and where the
  content phase sits in it
- [`a-downloaded-area-is-verified-by-what-it-renders.md`](a-downloaded-area-is-verified-by-what-it-renders.md)
  — the tile half's own completeness rule
- [`weather-day-picker-is-a-selector-not-navigation.md`](weather-day-picker-is-a-selector-not-navigation.md)
  — why a weather sheet is one undated URL per location and not one per day
