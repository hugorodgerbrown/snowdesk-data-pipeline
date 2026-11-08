---
name: the-map-has-a-dated-mode-and-a-terrain-mode
description: Conditions / Terrain map modes (ONLY_IN, SUPPRESSION.MAP_MODE) suppress, never rewrite; no Terrain region taps; Today row; ?view=3d
status: current
last-reviewed: 2026-11-06
---

# The map has a Dated mode and a Terrain mode

**SNOW-1421 (5 Nov 2026) changed this record.** The Dated mode is called
"Conditions" on screen (`MODES.DATED`, `dated`, in code), and each mode now
holds the other's own layers. See [Each mode has its own
layers](#each-mode-has-its-own-layers-snow-1421) below; the sections
before it describe SNOW-1394 and say where SNOW-1421 replaced them.

**SNOW-1422 (5 Nov 2026) changed it again.** Terrain has no region taps,
and the place sheet there has one Today row. See [No regions to choose in
Terrain](#no-regions-to-choose-in-terrain-snow-1422).

**SNOW-1394**, behind the `map_modes` flag. Some of what the map draws
changes by the day: the avalanche bulletin, the weather and the field
reports. The rest is the same tomorrow as today: the region boundaries,
Steep terrain, routes, resorts, ski tours, rest zones and pins. Hugo, 2 Nov 2026: "You're in
'what is happening now / on a date' mode, or planning + browsing mode."

The map status bar that tried to say all this in one strip (SNOW-1392,
#1425) did not work on a phone and was reverted (#1427). A two-way switch
that is always on screen replaced it.

## Decision

- **One control, two segments.** The date control with its day steps is
  the Dated segment, and a mountain after it (`#map-mode-terrain`) is the
  Terrain one. The active segment carries the label and the inactive one
  is an icon: `[ ‹ Today › | ⛰ ]` and `[ 📅 | ⛰ Terrain ]`.
  - The date says the day itself, never "Now", because "Now" is wrong
    once the day moves. On today it reads "Today", so the separate
    "Today" button is not rendered with the toggle.
  - **The way back to today (5 Nov 2026).** With that button gone, nothing
    but stepping days or finding today in the grid led back. Hugo chose
    two ways (options C and B of the back-to-today mockup):
    - The calendar card ends with a primary "Today"
      (`#map-calendar-today`, `_map_calendar.html`, only behind the flag).
      Like the button it stands in for, it is always shown and disabled
      on today. A press calls `pwaMapMode.toConditionsToday()` and the
      card closes (`map_calendar.js`).
    - A row followed from the Today panel, which is always today, calls
      `toConditionsToday()` first, in Terrain too, so the sheet it opens
      shows the level the row showed (`today_panel.js`). Until 8 Nov 2026
      the panel also opened with a "Map shows 2 Nov 2026" (or "Map shows
      Terrain") notice and a "Show today"; Hugo removed it, as a "Show
      today" on a panel, and a tab, named Today makes no sense.
  - A tap on the date opens the calendar. In Terrain, a tap on the
    calendar icon comes back to the dated map on the day the reader left.
- **Terrain is the map with the dated overlays off, and nothing more.**
  Hugo: "Terrain mode is really just turning off the date data overlays."
  - The dated overlays (`DATED_OVERLAYS` until SNOW-1421; now
    `pwaMapModeCore.ONLY_IN.dated`) are the bulletins, the bulletin
    grouping outline (`l3`), problem-terrain sync, weather and the season
    scrubber. Field reports were among them until SNOW-1421 made them
    shared. The region chip, which named a region by its level, went with
    them; SNOW-1422 removed it in both modes.
  - **The regions stay (4 Nov 2026).** They first went with the bulletins,
    on the reading that a region exists only to hold one. Hugo: "regions
    exist beyond the daily bulletin, and we should reflect that." So the
    boundary tiers (`l1`, `l2`, `l4`) are drawn as the reader chose them in
    both modes, and their layers-menu rows stay live. From z10, where Dated
    labels a region "3 · Martigny-Verbier", Terrain labels it with the bare
    name (`regions-level-label` reads `name`, not `text`).
  - **A tap opens a lean region sheet.** Replaced by SNOW-1422, which
    takes region taps out of Terrain (below). The fill stays installed at
    opacity 0 as the tap target, so a tap opens the same sheet as in Dated.
    What it marks `data-dated` (the level tile, the bulletin, the field
    reports, the problem-terrain switch) is hidden in Terrain by map.css,
    which leaves the name, the breadcrumb, the pin and download buttons and
    the resorts. In their place a panel says "Select a date to view current
    weather and avalanche conditions" (`[data-map-mode-to-dated]`). A press
    switches to Dated on **today**, not the day the reader left, because
    the panel offers current conditions, and the region stays selected, so
    its sheet repaints in place with the day. A region selected in either
    mode stays selected across a switch.
  - Terrain switches nothing on. Steep terrain stays the reader's own
    switch. (Until SNOW-1421 it was drawn in both modes, and SNOW-1377's
    precedence of Steep terrain over the sync applied in Dated; now it is
    Terrain's own, so the two are never on screen together.)
  - The Today panel's sun stays in both modes. It is a panel, not a map
    surface.
- **Suppression, never preference.** This is the rule the rest hangs on
  (Codex's scope review).
  - The mode never calls an overlay's `show()` or `hide()` and never
    writes its stored choice. It adds `SUPPRESSION.MAP_MODE`
    (`layer_visibility_core.js`) through each overlay's `setSuppressed`
    hook: `pwaBulletinsLayer`, `pwaWeatherLayer` and `pwaBulletinTerrain`
    for Conditions' own, and since SNOW-1421 `pwaTerrainFilter`,
    `pwaSkiTours` and `pwaRestZones` for Terrain's.
    (`pwaCommunityReportsOverlay`'s hook went with SNOW-1421.)
  - What is drawn is the preference AND-ed with "not held"
    (`pwaMapModeCore.effective`). Leaving Terrain brings each overlay back
    exactly as the reader had it, re-read from storage and caught up with
    the day on its own load path.
  - A choice made in Terrain changes that choice alone. Switching Steep
    terrain on and off there leaves the problem-terrain choice intact.
- **Disabled rows do nothing.** Replaced by SNOW-1421, which hides the
  other mode's rows instead. In Terrain the dated rows of the layers menu
  showed dimmed (`data-map-mode-disabled`) and a tap on one was stopped in
  the capture phase, before the picker heard it (Hugo: "Do nothing.").
- **The mode is in the URL and on the device.** `?mode=terrain` is written
  with every other parameter kept, and the date write keeps it
  (`withDate`), so a link reproduces what its sender saw. A bare URL takes
  the device's last mode (`snowdesk.map.mode`), and a stored Terrain is
  written into it at boot, so a link copied from the address bar carries
  it too. Dated is spelt by leaving
  the parameter out.
- **Applied before the first paint.** `map.js` reads the boot mode itself
  at parse time (`modeSuppressedKeys`), before a single layer is
  installed, and every install asks `overlayDrawn` for its first
  visibility. A page opened on `?mode=terrain` never draws the dated
  layers and then takes them away. `map_mode.js` calls the hooks only on
  a change.

## Consequences

- **Every install path has to ask.** A lazy load, a basemap swap or a
  region reinstall that sets a dated layer's visibility from its
  preference alone would paint it under Terrain. Two things backstop
  that. The `snowdesk:overlay-load` handler AND-s its re-read preference
  with the mode. A listener on `snowdesk:overlay-visibility-changed`
  re-hides every held layer synchronously, so a panel switch (the reports
  panel's "Show on the map") records the reader's choice and is then held
  like any other.
- **A region can be downloaded in Terrain two ways.** The region sheet's
  button is back now that Terrain draws the regions. Before that, holding
  them took away the only way in, so SNOW-1414 added the Downloads sheet's
  "Download a region", which stays as the way in from that sheet in both
  modes: a short pick task
  (`static/js/map_region_pick.js`) that draws plain outlines of its own
  over `PlacementFocus` and removes them when it closes. It releases no
  held tier and writes no stored choice, so it is not an exception to
  suppression, never preference.
- **A region row on another panel still opens its region.** A resort
  sheet's region row is an explicit ask, so `snowdesk:region-open` is not
  refused in Terrain. Since SNOW-1422 it goes to Conditions on today first.
- **The pure core loads for everyone; the flag decides whether it is read.**
  `map_mode_core.js` is on every `/map/`, because every core `map.js`
  reads must be (`test_home_scripts.py`). `map.js` and the shared date
  write (`map_shared.js`) act on it only where `#map` carries
  `data-map-modes-enabled`, so off the flag a pasted `?mode=terrain` does
  nothing and is dropped on the first day change. The toggle itself
  (`map_mode.js`) loads only behind the flag.
- **The help topic waits for the rollout.** `/help/#help-topic-map-modes`
  renders only behind the flag, and `docs_corpus.GATED_HELP_TOPICS` keeps
  it out of the public MCP docs corpus. The rollout ticket ungates both and
  adds the question "what does the date / terrain toggle do" to the
  search eval set.

## Rejected

- **Hiding the overlays through their own `show()` / `hide()`.** Simpler,
  but it rewrites the reader's stored choices: a reader with weather off
  would find it on after a round trip through Terrain, or the other way
  round.
- **Terrain turning Steep terrain on.** Tempting, since Terrain is "the
  terrain map", but it would be a second writer of the reader's switch.
  Hugo: Terrain "doesn't turn terrain on by default".
- **Hiding the dated rows in the layers menu.** Rejected for SNOW-1394: a
  row that disappears reads as a lost layer. SNOW-1421 took it up once the
  menu said which mode it lists (below).

## Each mode has its own layers (SNOW-1421)

Hugo, 5 Nov 2026, explaining the map to a first-time user: "Snowdesk has
two maps. Conditions shows the mountains on a day: avalanche danger,
weather and what people have seen. Terrain shows the mountains themselves:
how steep, where the tours go, where wildlife needs space." With only the
dated layers taken away, Terrain at its defaults was Conditions without
the danger colours. So each mode now has layers of its own.

- **The per-mode table.** `pwaMapModeCore.ONLY_IN` replaces
  `DATED_OVERLAYS`. `ONLY_IN.dated` is `bulletins`, `l3`,
  `problem_terrain` and `weather`; `ONLY_IN.terrain` is `terrain_filter`,
  `ski_tours`, `ski_summits` and `rest_zones`. `isSuppressed(key, mode)`
  holds a key in the mode it does not belong to, and `suppressedIn(mode)`
  lists what a mode holds. Everything else is shared: one setting, drawn
  in both.
- **Field observations are shared.** The reports are dated, but what
  people have seen matters to someone planning too, and they are user
  data like favourites and routes (Hugo).
- **Still suppression, never preference.** The three Terrain-only layers
  gain `setSuppressed` hooks, and `map_mode.js`'s `suppressFor(mode)`
  drives all six, the holds first so the map never draws both modes'
  layers on the way over. Conditions never writes a Terrain setting.
- **First paint, both ways.** `map.js` seeds `modeSuppressedKeys` from
  `suppressedIn(bootMode)` and publishes `window.pwaMapModeState`
  (`isSuppressed`, `hold`). `ski_tours.js` (`partVisibility()`) and
  `rest_zones.js` (`visibility()`) draw their own layers and load after
  `map.js`, so they ask it on every install: a Conditions boot never
  paints a stored-on SAC layer. Their hooks write through `hold`.
- **Rest zones while drawing a route.** Draw mode asks for the rest zones
  whatever the switch says, so `rest_zones.js` shows them in either mode
  while a route is drawn, and the `overlay-visibility-changed` backstop
  leaves that key to it.
- **Steep terrain and the sync.** `bulletinTerrainShown` gives way only to
  a Steep terrain that is drawn. Behind the flag, switching the sync on no
  longer switches Steep terrain off (SNOW-1377 did), because that would
  write a Terrain setting from Conditions. Off the flag nothing changed.
- **The layers menu says which mode it lists.** Its header is a
  Conditions | Terrain control (`[data-map-mode-tab]`, buttons with
  `aria-pressed`) in place of the "Map layers" title and the count. A tap
  changes the map live, exactly as the date-row toggle does, and the two
  stay in step. The mode's own rows sit in a tinted block at the top of
  level one (`data-mode-only`); `map.css` hides the other mode's under
  `:root[data-map-mode]`. The dimmed rows and the capture-phase blocker
  are gone. The order is in
  [the-layers-menu-is-on-off-then-options.md](the-layers-menu-is-on-off-then-options.md).
- **Finding a tour moves the map to Terrain.** "Find a route" and a
  summit picked in search switch the mode (`ski_tours.js` `toTerrain`):
  finding a tour is a planning task, and in Conditions the mode holds the
  tours, so the "tap a route" hint would point at nothing. A tour already
  open in the route panel is drawn by the panel's own layers, so it still
  shows in Conditions.

### Rejected (SNOW-1421)

- **Tabs that only change which settings show.** A Terrain layer switched
  on from the menu while the map stayed in Conditions would draw nothing,
  which looks broken. The tabs are the mode.
- **Separate stored settings for each mode.** One switch per layer is
  simpler, and a layer belongs to one mode or is shared.

## No regions to choose in Terrain (SNOW-1422)

Hugo, 5 Nov 2026: in Terrain a region tap opened a sheet that could say
nothing about the day, and a panel that sent the reader back to
Conditions. A region is about the day's conditions, so Terrain has none to
choose.

- **No region taps.** With `pwaMapMode.isTerrain()`, `map.js`'s click
  dispatcher does not query `regions-fill`, so a tap reaches a resort pin,
  a rest zone or the empty-map close. The fill's pointer cursor and the
  double-click zoom to a region are off too. The fill stays installed:
  Conditions still taps it, and the boundaries are drawn in both modes.
- **Entering Terrain drops the region.** It clears the selection, which
  closes the region sheet, and replaces the hash so the `?mode=terrain`
  just written stays. It does not close the place sheet, as
  `deselectRegion()`'s `closeMapDetailSurfaces()` would: a resort is a
  Terrain thing too.
- **Any other way in goes to Conditions on today.** A search pick
  (`chooseRegion`), `snowdesk:region-open` from a sheet's region row, a
  hash (initial load, `hashchange`, `popstate`) and the tour's "Tap a
  region" step call `pwaMapMode.toConditionsToday()` first, then select.
  That helper is what the region sheet's panel did (Dated, then today
  through `snowdesk:scrub-to`), lifted onto `window.pwaMapMode`.
- **The place sheet's Today row.** A resort, a saved pin and the reader's
  location render one row in the peek
  (`public/partials/_place_sheet_today_row.html`, `data-terrain-only`):
  the region's level tile, "Today", the level's name and one weather
  reading, the day's icon and high (`place_sheet._today_row_weather`, from
  what the view already loaded, so it costs no query). Every section about
  the day carries `data-dated`, and `map.css` shows one or the other under
  `:root[data-map-mode="terrain"]` in `#place-sheet`. In Terrain
  `map_place_sheet.js` asks for today (`#season-scrubber[data-today]`), so
  the row is always today's, and refetches an open sheet when a mode change
  moves the day it reads. A tap on the row calls `toConditionsToday()` with
  the sheet left open, so it repaints in full.
- **The region sheet's Terrain parts went.** The "Select a date…" panel
  (`[data-map-mode-to-dated]`), its handler in `map_mode.js`, the
  `data-dated` marks and their hide rules: the sheet no longer opens in
  Terrain.
- **The region chip went, in both modes** (Hugo, not behind the flag);
  [the-region-colour-gives-way-by-zoom.md](the-region-colour-gives-way-by-zoom.md)
  has its history. Past z10 a region is reached by zooming out in
  Conditions or by search.

### Rejected (SNOW-1422)

- **Today in full in Terrain.** The place sheet would read as Conditions
  fixed to today (option A of the mockup). One row says the day exists and
  where to see it.
- **Closing the place sheet on entering Terrain.** It is the one sheet that
  has something to say in both modes.


## 3D is a view of either mode (SNOW-1449, SNOW-1452)

The mode says what the map shows; the view says how it is drawn. SNOW-1449
offered 3D (`?view=3d`, `map_view_core.js`) in Terrain only. Since SNOW-1452
(6 Nov 2026) both modes are drawn in 3D: the 3D switch shows under
`:root[data-map-mode]` in either, a mode change keeps the view, and
`?view=3d` opens in whichever mode the mode rule picks. In Conditions the
danger fill is a light wash and problem terrain drapes on the relief. The
view never writes the mode or any layer
preference, and it is never stored on the device: a bare `/map/` opens
flat even when the stored mode is Terrain. A date write carries `view` with
`mode` (`withDate`). Details in
[a-3d-map-is-maplibre-terrain.md](a-3d-map-is-maplibre-terrain.md).
