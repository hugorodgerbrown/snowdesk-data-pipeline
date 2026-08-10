---
name: choropleth-blended-not-translucent
description: REVERSED by SNOW-656 — regions-fill was opaque with pre-blended colours; it is now translucent again, at a user-chosen opacity step
status: historical
last-reviewed: 2026-08-10
---

# The choropleth is pre-blended, not translucent (REVERSED)

**Status: historical.** Superseded by
[`bulletin-fill-is-a-user-choice.md`](bulletin-fill-is-a-user-choice.md)
(SNOW-656, 2026-08-10). Kept because the problem it describes is real and
will recur for anyone who reaches for a translucent fill without knowing
what it costs.

**The decision it recorded.** `regions-fill` in `static/js/map.js` was
painted at `fill-opacity: 1`, with the translucency it used to have baked
into the colours instead: each EAWS rating colour alpha-composited against a
fixed backdrop by `compositeOverBackdrop()` in
`static/js/choropleth_core.js`, at one of two blend weights
(`REGION_FILL_WEIGHT`, and `REGION_FILL_WEIGHT_EMPHASIS` for a selected or
previewed region). The backdrop was a constant — the project's own
`--color-bg` off-white — never anything read from the basemap style.

**Why it was taken.** A translucent fill is composited by MapLibre against
whatever the basemap draws underneath it, and the five entries in
`BASEMAP_STYLES` draw very different things: openfreemap Liberty's warm
near-white land, swisstopo winter's blue-white hillshade, swisstopo light's
pale grey, IGN Plan, basemap.at. The same rating therefore rendered as a
different colour on each one — `moderate` yellow reading as a washed pink on
one basemap and as saturated yellow on another — while the legend pill
naming it never moved.

**That reasoning still holds, and the reversal accepts its cost.** A
translucent choropleth does drift with the basemap; SNOW-656 traded that for
letting the user read the terrain under the colours, and gave them the
control to decide how much. The trade is explicit rather than accidental —
which is the part this file exists to make sure stays true. The successor
records what is now done instead and what remains unresolved (the drift is
worst on the darker basemaps, and no single step suits all five).

**What went with it.** Nothing in `map.js` composites any more; the fill
takes the raw `RATING_COLOURS`. `compositeOverBackdrop()` and
`BACKDROP_COLOUR` remain in `choropleth_core.js` — the legend and any other
surface that wants "the same colour as the map" still needs them, and they
are the ready-made mechanism if this decision is ever reinstated.

**One trap worth keeping.** Compositing and translucency do the same job,
and applying both blends every rating twice. At the old resting weight
`#e0e0e0` composited to `#e8e7e5`, four points from the `#f2f0ec` backdrop —
so painting the composited colour at 50% opacity left the no-bulletin grey
invisible at any opacity. If you reach for one of these mechanisms, make
sure the other is off.
