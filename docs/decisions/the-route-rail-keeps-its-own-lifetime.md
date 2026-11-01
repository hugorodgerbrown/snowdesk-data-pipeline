---
name: the-route-rail-keeps-its-own-lifetime
description: route_rail.js stays out of pwaMapOverlays, reads openNames(); closeOtherSheets, two-phase Escape, route-rail-changed, data-route-rail-open
status: current
last-reviewed: 2026-11-01
---

# The route rail keeps its own lifetime, outside the overlay registry

## Decision

Every floating surface over `/map/` joins `window.pwaMapOverlays`
(`static/js/map_overlay_exclusivity.js`), so that opening one closes the
rest ([`../map-surface.md`](../map-surface.md#who-can-be-open-together)).
The route rail (`#route-rail`, `static/js/route_rail.js` with its pure half
`route_rail_core.js`, published as the frozen `window.pwaRouteRail`) does
not, and must not. There were two rails and a route detail sheet once;
SNOW-1065 collapsed the rails into one panel, and SNOW-1095 removed the
sheet, so this one panel is now the whole of the route's surface.

## Why

A tap on a saved route opens the rail and nothing else, and the reader
then studies it: they place a point and read the terrain under it, while
they check the layers, the legend, the calendar or the zoom flyout.
Registering the rail would close it as soon as any of those announced
itself, and the point and the route with it. The registry has one rule,
"opening anything closes everything else", and the rail is the surface
that rule is wrong for. The only collisions that matter are with the
other object sheets, which dock in the same place, so the rail joins the
narrower rule that covers exactly them.

## Consequences

- **What closes it.** Its own × (`[data-route-rail-close]`), a map tap no
  marker takes (map.js; on a resort pin the resort opens as well), Escape
  (below), a claim or a delete, and a drag below peek on a phone. Each one
  goes through `pwaRouteRail.close()`, which clears the cursor first so
  the map's dot clears too. Tapping another route refills the rail where
  it is.
- **One object sheet at a time.** The rail binds its detents through
  `MapSheet.bindDetents(rail, {onClose: close})` and calls
  `detents.open()` on every open, at every width. Since the rail is not in
  the registry, `closeOtherSheets` in `static/js/map_sheet.js` keeps it
  apart from the region, place and weather sheets: opening any of them
  closes the rail through its own close, and opening the rail closes them.
  That rule exists because the registry could not express it. These four
  share a phone's foot, and since 2026-10-21 they share the desktop's
  left column too.
- **Coexisting with registry surfaces.** On a desktop the rail is pinned
  top-left, over the canvas (`z-index: 3`). It sits below the control
  columns (4) and the utility cluster (6), so their menus open over it
  (`static/css/map.css`, `.route-rail`), and roundel and form panels open
  on the right beside it
  ([`a-panel-opens-beside-what-opened-it.md`](a-panel-opens-beside-what-opened-it.md)).
  On a phone the rail is a detent sheet at `--z-popup`. While it is open,
  `#map[data-route-rail-open]` and `:root[data-map-sheet]` withdraw the
  controls that would open a competing surface. Surfaces that appear by
  themselves read the same mark and stay away: the tour offer in
  `map_tour_offer.js` and the between-seasons note in
  `map_between_seasons.js`.
- **Escape goes to what the reader is looking at.** On the window's
  capture phase the rail records whether something else will take the
  key: a focused field, an open `[data-overlay]` (every
  `_overlay_sheet.html` sheet, the help tour), an open overflow menu
  (`[data-overflow-open]`), or anything registered with
  `window.pwaMapOverlays` that is open (`openNames()`: the layers menu,
  zoom, the calendar, the alerts card, the help menu, the legend). On the
  document's bubble phase, if nothing took it, the rail acts: the first
  Escape clears a placed point, and the next one closes the route.

  The registry read was added by SNOW-1335. Before it, those flyouts
  carried none of the other signals and closed on their own listeners, so
  one Escape closed the flyout and also cleared the point or closed the
  route. Reading the registry covers every flyout registered later
  without a new attribute on each. The rail still never registers itself;
  it only reads what is open.
- **What it announces instead.** `snowdesk:route-rail-changed` on every
  open, refill and close (the leader line, map.js, the example guide),
  `snowdesk:route-rail-resized` when its content changes height, and
  `#map[data-route-rail-open]` for CSS. Readers use `pwaRouteRail.isOpen()`,
  `cursor()`, `isSheet()` and `cursorPoint()`. A new surface does not
  close the rail by registering. If it docks where the rail docks, it must
  bind detents. Otherwise, coexisting with the rail is the intended
  behaviour.
