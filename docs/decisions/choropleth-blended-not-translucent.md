---
name: choropleth-blended-not-translucent
description: regions-fill paints opaque pre-blended colours, not a translucent fill-opacity, so a danger rating renders the same on every basemap
status: current
last-reviewed: 2026-08-09
---

# The choropleth is pre-blended, not translucent

**Decision.** `regions-fill` in `static/js/map.js` is painted at
`fill-opacity: 1`. The translucency it used to have is baked into the
colours instead: each EAWS rating colour is alpha-composited against a fixed
backdrop by `compositeOverBackdrop()` in `static/js/choropleth_core.js`, at
one of two blend weights (`REGION_FILL_WEIGHT`, and
`REGION_FILL_WEIGHT_EMPHASIS` for a selected or previewed region). The
backdrop is a constant — the project's own `--color-bg` off-white — and
never anything read from the basemap style.

**Why.** A translucent fill is composited by MapLibre against whatever the
basemap draws underneath it, and the five entries in `BASEMAP_STYLES` draw
very different things: openfreemap Liberty's warm near-white land,
swisstopo winter's blue-white hillshade, swisstopo light's pale grey, IGN
Plan, basemap.at. The same rating therefore rendered as a different colour
on each one — `moderate` yellow reading as a washed pink on one basemap and
as saturated yellow on another — while the legend pill naming it never
moved. For a danger-rating choropleth the colour *is* the message: an
avalanche map whose colours are a function of the basemap the visitor last
picked is wrong, not merely inconsistent.

Blending against a fixed value is the only way to get a rating to render as
one colour everywhere. Doing that arithmetic up front, rather than adding an
opaque underlay layer beneath the fill, keeps the choropleth a single layer
— the eight places that name `regions-fill` (the L4 visibility toggle,
country filters, the edit-resorts mode, hit-testing, the style-swap
reinstall) would otherwise each have to learn about a second layer and stay
in lockstep with it.

The backdrop value was chosen to sit within a couple of points of
openfreemap Liberty's land colour, which is the basemap the palette was
originally tuned against — so the default basemap looks as it did, and the
other four now match it rather than each going their own way.

**Consequences.** Basemap detail no longer shows through a covered region;
inside the choropleth you see the rating colour and nothing else. Terrain,
roads and hillshade are still readable outside covered regions, and the L4
overlay toggle switches the choropleth off entirely when a user wants the
basemap underneath. Every Snowdesk layer that matters still draws *above*
the fill — region outlines and labels, resort pins, favourites, weather
points, community reports, the download grid — so nothing of ours is lost.

Changing a rating colour now means changing it in `RATING_COLOURS` only;
the blended values are derived, never written down. Anything that wants to
show the same colour as the map (rather than the pure EAWS token a UI chip
should use) must call `compositeOverBackdrop()` with the same weight rather
than reaching for `RATING_COLOURS` and an opacity.
