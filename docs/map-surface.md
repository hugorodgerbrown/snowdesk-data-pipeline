---
name: map-surface
description: Map zoom bands (REGION_SELECT_MAXZOOM), tap priority, overlay kinds, pwaMapOverlays, one bottom sheet, camera padding owner
status: current
last-reviewed: 2026-10-26
---

# The map surface: zoom, taps and overlays

This is the interaction model of `/map/` in one place: what the map draws at
each zoom, what a tap does, and which surfaces can be open together. The
product reasons for each layer are in
[`map-page-functional-spec.md`](map-page-functional-spec.md); this doc is the
mechanics a change has to respect. Every number below is a constant in
`static/js/map.js` unless it says otherwise.

## 1. Zoom bands

The camera runs from z4 to z18 (`MIN_ZOOM`, `MAX_ZOOM`). "Zoom" for a tap
rule means the **whole** zoom the zoom roundel shows, so a camera at 10.6
reads 10 and behaves as 10.

```
z  4    5    6    7    8    9   10   11   12   13   14   15 … 18
   |----|----|----|----|----|----|----|----|----|----|----|----|
   region fill + outline ........................................  every zoom
   major-region labels  [5–7)
                  sub-region labels [7–8.5)
                            region labels [8.5 →
   TAP SELECTS A REGION ===========|  ≤ 10 (REGION_SELECT_MAXZOOM)
   region fit / search frame stops at 10 (REGION_FIT_MAX_ZOOM)
                            favourite labels 8 →
                                  resort labels, route endpoints 10 →
                                       route transitions 11 →
                                       pin / report focus 11 (POINT_FOCUS_ZOOM)
                                                 terrain filter / slope tiles 12–14, overzoomed above
   route drawn as LEGS (climb / descent) ..........|
                                                      route drawn in SLOPE CLASSES 14 → (ROUTE_SLOPE_MINZOOM)
   route cursor a DOT ..................................|  its 25 m SEGMENT 14 → (SNOW-1145)
                       weather symbols 7 →, decluttered to ≤ 11
```

The two halves are deliberately inverse. A region is a valley's worth of
ground, so it is a tap target only while it fits the screen (≤ z10); the
terrain filter and a route's slope classes only mean something close in
(z12 and z14 up). Above z10 the region fill still draws, and a tap on it is
a tap on empty map.

## 2. What a tap does

One generic `map.on('click')` handler decides every tap (map.js, "single
map click dispatcher"). It stops at the first rule that claims the tap:

1. **On a control over the map** (scrubber, utility cluster, bottom-right
   roundels, legend, tour offer, help coachmark): ignored. In help mode
   (SNOW-1119) a control never gets that far — a capture-phase listener in
   `map_help_mode.js` takes the tap and shows the control's card — but a
   tap on the map itself is let through, so the rules below still decide
   it.
2. **On a marker** (favourite pin, report point or cluster, a route's line,
   legs or slope segments): the marker is activated. A route tap opens the
   route rail with the point placed where the line was hit; a tap on the
   open route moves the point. The trip a `?trip=` / `?trip_share=`
   arrival draws is a marker too, under the saved routes (SNOW-1213): it
   opens the same rail on the trip's snapshot, read-only. The feature
   carries no uuid, so the rail shows no "…" menu and fetches no terrain
   rows, and the point is drawn on its own cursor layers
   (`trip-route-cursor-*`), which no overlay switch hides.
3. **On a weather symbol**: the weather sheet opens for that station.
4. **A route is open**: the tap closes the route rail. On a resort pin it
   also opens that resort, as rule 5 would; anywhere else it does nothing
   more, since it was aimed at leaving the route, not at a region.
5. **On a resort pin**: the resort's place sheet opens — at peek on a
   phone, as the inset panel on a desktop (SNOW-1161, which retired the
   resort popup). A resort reached from search, from the region sheet's
   "Resorts in this region" or from the resort page's "See on the map"
   opens the same sheet, after flying to the pin
   ([`decisions/the-map-opens-a-resort-in-the-sheet.md`](decisions/the-map-opens-a-resort-in-the-sheet.md)).
   Under its peek row the sheet shows the resort page's own body for the
   map's day, as the region sheet shows the bulletin page's
   ([`decisions/the-sheet-is-the-page.md`](decisions/the-sheet-is-the-page.md)).
6. **On the region already selected, with its sheet closed**: reopens
   the sheet, at any zoom, and changes nothing else
   (`window.pwaRegionSheet.reopen`). The sheet closes while the region stays
   selected (its ×, a route opening over it), and past z10 this is the only
   way back to it. It works during timelapse playback too, because it
   changes no selection.
7. **On a region, at z10 or below**: selects it, or deselects it if it was
   already selected. The selection opens the region sheet, on every width
   (SNOW-1173: a desktop opened nothing for a region until then, while a
   resort or a weather symbol opened the inset panel).
   **Above z10**: closes the place sheet or popup and leaves the selection
   alone.
8. **On empty map**: deselects the region and closes any popup.

A double-click on a region at z10 or below zooms to the region; above z10 it
is the map's own zoom-in.

## 3. The four kinds of overlay

Every surface over the map is one of four kinds, and the kind decides how it
opens, closes and coexists (CLAUDE.md, design-system rule 6).

| Kind | Examples | Opened by | Phone shape | Closed by |
|---|---|---|---|---|
| **Object sheet** | region sheet, place sheet, weather sheet, route rail | a tap on something on the map | bottom sheet with detents (peek, half, full) | ×, drag below peek, Escape, deselecting, another object sheet |
| **Roundel panel** | routes, favourites, reports, downloads lists | its roundel | full-width docked panel, no detents | its roundel, its ×, another overlay |
| **Form panel** | field report, terrain filter | a call to action | docked panel, no detents, no drag-to-close | its own buttons, its × |
| **Flyout / card** | layers menu, zoom flyout, calendar, legend card, help coachmark, tour offer, a saved pin's desktop popup | its toggle | anchored card | a click away, another overlay |

Help mode (SNOW-1119, the "?" roundel) is none of these: it is a layer
over the whole map that names the controls and keys whichever object sheet
opens under it. It closes every overlay as it starts and does not register
with `pwaMapOverlays`, so a sheet opening does not end it.

### The control column

The bottom-right column is two glass capsules (`.map-control-group`), 16px
apart:

- **the map** (`#map-control-group-map`): locate and layers. Always shown.
- **yours** (`#map-control-group-yours`): favourites, field observations,
  routes, downloads, in that order. It is the inner of
  `#map-controls-collapsible`, so the chevron folds this capsule only.

A roundel carries no on-map marker for "my layer is drawn": the layers menu
is where that is read. Bulletin opacity is a row in the layers menu (the
`role="radiogroup"` "Bulletin opacity", after ALBINA), not a roundel. Each
of the four "yours" panels ends in a "Show on the map" switch bound to the
same setting as its layers-menu row (`static/js/map_panel_overlay_switch.js`).
Why: [`decisions/map-controls-are-two-groups-without-an-on-map-marker.md`](decisions/map-controls-are-two-groups-without-an-on-map-marker.md).

On a phone an object sheet steps up a detent on a press of its handle or
a tap on its header (away from the header's buttons), and drags from
either. While one rests at peek the bottom-right column keeps locate,
layers and routes only; at half and full the column and the bottom-left
stack are hidden whole (`static/css/map.css`).

### Who can be open together

Two mechanisms enforce exclusivity, and a new surface has to join the right
one.

- **`window.pwaMapOverlays`** (`static/js/map_overlay_exclusivity.js`): one
  registered overlay at a time. Every sheet made with `MapSheet.attach` joins
  automatically under its DOM id; flyouts and cards register by hand.
  Opening any of them closes every other registered one — except that a
  surface may open with `opening(name, { spareObjectSheets: true })`, which
  leaves the object sheets (registered with `objectSheet: true` by
  `MapSheet.attach` for any sheet that declares `data-detents`) open. The
  calendar is the one caller (SNOW-1195): a day picked there reloads the
  region, place or weather sheet in place, each listening for
  `snowdesk:date-changed`, where it used to shut it and cost a re-tap.
  The date row's ‹ › steps and the scrubber's keys (`map_day_nav.js`) open
  nothing, so they never closed a sheet.
  The legend card and the layers menu pass `spareObjectSheets: 'wide'`
  (SNOW-1196): from 768 px they open on the right, beside the left-column
  sheet, so they leave it open; below that the 25rem sheet and the flyout
  would overlap, so they close it as before.
- **One object sheet at a time** (`static/js/map_sheet.js`,
  `closeOtherSheets`). On a phone every detent sheet docks to the same foot
  of the screen, and on a desktop all four dock in the left column under
  the search pill (since 2026-10-21), so opening one closes any other that
  is open, at every width, through its owner's
  own close so its teardown runs. This is what covers the **route rail**,
  which stays out of `pwaMapOverlays` on purpose: it has its own lifetime
  (a flyout opening must not close the route the reader is studying), so
  before this rule nothing closed it and it closed nothing, and the region
  sheet's peek sat on top of it.

So on a phone: opening a route closes the region sheet (the region stays
selected and outlined), a search pick that opens the region sheet closes the
route, and a weather symbol tapped beside a route replaces the route sheet.
On a desktop the same holds: the route rail is pinned top-left and, since
2026-10-21, the region, place and weather sheets dock in that column too,
under the search pill they are picked from, so a route and the region sheet
replace one another there as they do on a phone. The roundel and form
panels stay inset at the right, beside the column that opens them.

## 4. The camera padding and the root marks

While an object sheet rests at peek or half on a phone, the camera's bottom
edge is padded by the sheet's height, so a fly-to lands above the sheet. At
full the padding is left where it was, so a drag back down does not move the
camera twice.

**`map_sheet.js` is the one writer of that padding**, and of the root's
`data-map-sheet` attribute. Each sheet reports its lift and its detent; the
module applies the tallest lift, and one `moveend` watcher per map re-applies
it after a camera move that brought its own padding (a `fitBounds`) or after
a write deferred because the camera was moving.

Why one writer: until 2026-10-19 each sheet wrote the padding itself and
kept its own `moveend` watcher, a closed sheet's answer being 0. With two
sheets bound in a session (a route opened, then a region), the first camera
move set them fighting: one watcher's `jumpTo` ended a move, the other's
answered it, until the stack overflowed inside MapLibre's handler manager and
the map stopped answering touches for the rest of the session. A new surface
that needs the camera padded must report through `bindDetents`, never call
`setPadding` or `jumpTo({padding})` from a `moveend` handler of its own.

The other root and `#map` marks, for CSS that needs to know what is up:

| Mark | Set by | Means |
|---|---|---|
| `:root[data-map-sheet="peek\|half\|full"]` | `map_sheet.js` | an object sheet is open on a phone, at that detent |
| `:root[data-region-sheet]` | `map_region_sheet.js` | the region sheet is open |
| `#map[data-route-rail-open]` | `route_rail.js` | the route rail is open; map.css withdraws the controls a phone does not keep |

## 5. What each object sheet costs to open

| Sheet | Peek content comes from | Network |
|---|---|---|
| Region sheet | the page: the selection's name, code and area, the day's level off the map's feature state | the body (`/partials/region-sheet/<id>/?d=`) once per region and day: kept in memory for 10 minutes, and sent `Cache-Control: private` (a day for a past day, 10 minutes for today or a forecast); network-only in the service worker |
| Place sheet | `/partials/place-sheet/resort/<slug>/` or the favourite's | one request per open |
| Weather sheet | `/api/weather…` detail | one request per open |
| Route rail | the routes GeoJSON already in memory | none; the staff-only full detent fetches terrain rows once per route |

The region sheet's peek is painted the moment a region is selected, from a
blank copy of `_region_sheet_peek.html` that the map page carries in a
`<template>` and `map_region_sheet.js` fills (level from
`window.pwaRegionLevel`). The body the server renders includes the same
partial, so the sheet keeps its height when the body lands. Reopening a
region, or scrubbing back to a day already seen, paints the kept body with
no request. If the body fails, the peek stays and a line under it says so.

### During timeline playback

The timelapse changes the day five times a second, and each object sheet
used to reload on every frame — one server-rendered request per 200 ms,
none cancelled (SNOW-1239). Now the weather, place and region sheets
**pause** while the timeline plays: they stay open, show a "Playback in
progress" band over a dimmed body, and ask for nothing. When playback stops
each loads once, for the day it stopped on; a day picked by hand ends
playback and loads that day instead, with no second load from the stop. A
sheet opened during playback shows what needs no request (the region's
kept body or peek row; the band alone for weather and place), and the band
is its peek when it holds nothing else. A superseded request, and one in
flight when the sheet closes by any route, is aborted. The pieces are
`window.pwaSheetPlayback` (`sheet_playback_core.js`); the band and the
`data-sheet-paused` mark live on the sheet element itself, because every
paint replaces its children, and the dim is one data-attribute rule in
`static/css/map.css`. `MapSheet.attach`'s `onTeardown` option is how each
module hears every close route.
