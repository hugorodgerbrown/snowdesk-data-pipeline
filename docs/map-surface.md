---
name: map-surface
description: Map zoom bands (REGION_SELECT_MAXZOOM), tap priority, overlay kinds, pwaMapOverlays, one bottom sheet, camera padding owner
status: current
last-reviewed: 2026-10-19
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
   roundels, legend, intro card, help coachmark): ignored.
2. **On a marker** (favourite pin, report point or cluster, a route's line,
   legs or slope segments): the marker is activated. A route tap opens the
   route rail with the point placed where the line was hit; a tap on the
   open route moves the point.
3. **On a weather symbol**: the weather sheet opens for that station.
4. **A route is open**: the tap closes the route rail and does nothing
   else. It was aimed at leaving the route, not at a region.
5. **On a resort pin**: the resort popup opens.
6. **On the region already selected, with its phone sheet closed**: reopens
   the sheet, at any zoom, and changes nothing else
   (`window.pwaRegionSheet.reopen`). The sheet closes while the region stays
   selected (its ×, a route opening over it), and past z10 this is the only
   way back to it.
7. **On a region, at z10 or below**: selects it, or deselects it if it was
   already selected. On a phone the selection opens the region sheet.
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
| **Roundel panel** | routes, favourites, reports, downloads lists; region panel | its roundel | full-width docked panel, no detents | its roundel, its ×, another overlay |
| **Form panel** | field report, terrain filter | a call to action | docked panel, no detents, no drag-to-close | its own buttons, its × |
| **Flyout / card** | layers menu, fill flyout, zoom flyout, calendar, legend card, help coachmark, intro card, resort popup | its toggle | anchored card | a click away, another overlay |

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
  Opening any of them closes every other registered one.
- **One bottom sheet at a time on a phone** (`static/js/map_sheet.js`,
  `closeOtherSheets`). Every detent sheet docks to the same foot of the
  screen, so opening one closes any other that is open, through its owner's
  own close so its teardown runs. This is what covers the **route rail**,
  which stays out of `pwaMapOverlays` on purpose: it has its own lifetime
  (a flyout opening must not close the route the reader is studying), so
  before this rule nothing closed it and it closed nothing, and the region
  sheet's peek sat on top of it.

So on a phone: opening a route closes the region sheet (the region stays
selected and outlined), a search pick that opens the region sheet closes the
route, and a weather symbol tapped beside a route replaces the route sheet.
On a desktop the route rail is a panel pinned top-left and the region sheet
never opens, so nothing changes there.

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
