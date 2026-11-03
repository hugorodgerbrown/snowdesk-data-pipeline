---
name: the-map-has-a-dated-mode-and-a-terrain-mode
description: Why the map has a Dated / Terrain toggle (map_mode_core.js, SUPPRESSION.MAP_MODE, ?mode=terrain) that suppresses, never rewrites
status: current
last-reviewed: 2026-11-03
---

# The map has a Dated mode and a Terrain mode

**SNOW-1394**, behind the `map_modes` flag. Some of what the map draws
changes by the day: the avalanche bulletin, the weather and the field
reports. The rest is the same tomorrow as today: Steep terrain, routes,
resorts, ski tours, rest zones and pins. Hugo, 2 Nov 2026: "You're in
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
  - A tap on the date opens the calendar. In Terrain, a tap on the
    calendar icon comes back to the dated map on the day the reader left.
- **Terrain is the map with the dated overlays off, and nothing more.**
  Hugo: "Terrain mode is really just turning off the date data overlays."
  - The dated overlays (`pwaMapModeCore.DATED_OVERLAYS`) are the
    bulletins, the region boundaries and labels (`l1`, `l2`, `l4`) with
    the region tap and the region chip, the bulletin grouping outline
    (`l3`), problem-terrain sync, weather, field reports and the season
    scrubber.
  - Regions go with the bulletins because a region exists only to hold
    one. A region left selected is deselected on the way in.
  - Terrain switches nothing on. Steep terrain stays the reader's own
    switch in both modes, and SNOW-1377's precedence of Steep terrain over
    the sync still applies in Dated.
  - The Today panel's sun stays in both modes. It is a panel, not a map
    surface.
- **Suppression, never preference.** This is the rule the rest hangs on
  (Codex's scope review).
  - The mode never calls an overlay's `show()` or `hide()` and never
    writes its stored choice. It adds `SUPPRESSION.MAP_MODE`
    (`layer_visibility_core.js`) through each overlay's `setSuppressed`
    hook: `pwaBulletinsLayer`, `pwaWeatherLayer`,
    `pwaCommunityReportsOverlay` and `pwaBulletinTerrain`.
  - What is drawn is the preference AND-ed with "not held"
    (`pwaMapModeCore.effective`). Leaving Terrain brings each overlay back
    exactly as the reader had it, re-read from storage and caught up with
    the day on its own load path.
  - A choice made in Terrain changes that choice alone. Switching Steep
    terrain on and off there leaves the problem-terrain choice intact.
- **Disabled rows do nothing.** In Terrain the dated rows of the layers
  menu show dimmed (`data-map-mode-disabled`) and a tap on one is stopped
  in the capture phase, before the picker hears it (Hugo: "Do nothing.").
- **The mode is in the URL and on the device.** `?mode=terrain` is written
  with every other parameter kept, and the date write keeps it
  (`withDate`), so a link reproduces what its sender saw. A bare URL takes
  the device's last mode (`snowdesk.map.mode`). Dated is spelt by leaving
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
- **A region row on another panel still opens its region.** A resort
  sheet's region row is an explicit ask, so `snowdesk:region-open` is not
  refused in Terrain.
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
- **Hiding the dated rows in the layers menu.** A row that disappears reads
  as a lost layer. A dimmed one says the mode is holding it.
