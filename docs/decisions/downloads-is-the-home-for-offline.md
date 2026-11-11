---
name: downloads-is-the-home-for-offline
description: Downloads sheet is the home for offline — row verdict dot, inventory reveal (inventoryFor), Sync all, /offline/ unlinked
status: current
last-reviewed: 2026-11-10
---

# The Downloads sheet is the one home for offline

SNOW-1420. Supersedes
[the-connection-panel-answers-per-area.md](the-connection-panel-answers-per-area.md).

**Decision.** A download is everything you need to go offline, so the map's
Downloads sheet (`public/partials/_map_downloads_sheet.html`) is where a user
asks "will this work offline?" and does something about it:

- each row this device holds carries its area's verdict as a dot
  (`includes/_ugc_panel_row.html` with `status_dot=True`): green with nothing
  on screen for a yes, amber or red with one reason line ("Bulletins out of
  date", "Some trips or pins missing", "Terrain not saved", "Map data
  missing"), grey for "Not checked yet". Terrain counts (SNOW-1483): an area
  that should hold terrain data and was saved without it is amber, and Sync
  adds it. 3D does not count. An account-only row and an orphaned bucket have no dot.
  While a reason line shows, the meta drops its bulletins clause ("Bulletins
  saved 3 Nov", "Bulletins not saved"), so the row states one caveat, not
  two (Hugo, 4 Nov 2026);
- each row's "…" menu keeps its own Sync now, first, with Remove last;
- the sheet's foot carries one **Sync all**, which drains the mutation queue
  and syncs every area on the device in the sheet's order
  (`offline_ready_core.run` over `pwaBasemapDownloads.syncArea`). It is
  refused offline, shows its progress as its label, marks the row it is on
  as syncing, and toasts once if any area fell short. SNOW-1487: the label
  is "Syncing 2 of 5 areas", the row it is on shows its step and counts,
  the rest say "Waiting", Stop ends it, and the closing toast names every
  area with its outcome
  ([a-failed-addition-never-hides-a-working-map.md](a-failed-addition-never-hides-a-working-map.md)).

`static/js/map_downloads_sync_all.js` (`window.pwaDownloadsSyncAll`) owns the
verdict and the run; `map_downloads_manager.js` paints them on every render.
The verdict is the same reading SNOW-1415 gave the connection panel —
`stagesFromRecord` and `verdictFor` over the stored record and its
`verified` stamp — so /offline/'s own panels and the sheet cannot disagree
about one area.

The header's network menu (`includes/_connection_panel.html`) holds only the
state line and the **Offline mode** switch, which tells the service worker
not to try the network when the radio is on and no data moves. Its "Try
reconnecting" button shows only while the worker itself has latched offline;
a forced mode is undone by the switch beside it. The header symbol carries
no readiness mark.

`/offline/` is the debug page. It is not staff-only, but nothing links to it:
not Help, not settings, not the Downloads sheet. It keeps the per-area
journey panels and `?sync=all` for that use.

## Why

Hugo, 4 Nov 2026: the Downloads sheet, the network menu and /offline/ were
saying versions of the same thing in three places. A download is what makes
an area work offline, so its status and its Sync belong on the download. The
network menu is about the connection, and the one thing a user decides there
is whether the app should try it. /offline/ reads the app's internals, which
is what a developer needs and a user should not have to see.

The run lives in module state rather than the DOM because the sheet
re-clones its body on every render, and a run in flight must survive that.

## Consequences

- `/map/?sync=all` and `window.pwaConnectionPanel` are gone; nothing off the
  map starts a sync any more.
- A sync fetches only what changed, and stale areas sync in the background
  ([sync-fetches-only-what-changed.md](sync-fetches-only-what-changed.md),
  SNOW-1416, superseding
  [a-pre-departure-sync-is-unconditional.md](a-pre-departure-sync-is-unconditional.md)).
- A new surface that verifies an area should still stamp `verified`, or the
  sheet keeps the last answer another surface gave.
- The inventory of what a download holds is a reveal on its row (SNOW-1418),
  not a page. See "The inventory under a row" below.
- A link to /offline/ anywhere a user can see it is a regression;
  `tests/public/test_offline_page.py` checks Help, settings, the map and the
  offline shell.

## The inventory under a row

SNOW-1418. A row's chevron, the shared trailing disclosure
(`includes/_row_disclosure.html`), opens what the area holds into the row's
own panel (`includes/_downloads_inventory.html`): the map, the terrain, the
bulletin, the weather, the reader's routes, trips and saved places, the SAC tours, the
resorts and the community reports. Each has a `sync-*` dot and one value.

- **One reading.** `pwaOfflineJourneyCore.inventoryFor` is built on
  `stagesFromRecord`, so the row's dot and the list under it read the same
  stamp and cannot disagree.
- **It asks what is held.** Opening a row asks the worker which stamped urls
  it would serve offline (SNOW-1416's `held-content`, published as
  `pwaBasemapDownloads.heldContent`). It also reads the route and report ids
  in the overlay cache. A reading it could not take leaves the row on its
  stage's stamp, never a No.
- **It lists only what the last sync stamped.** A record synced before a
  field existed lists less, rather than a zero it never counted. The stamp
  gained `items.resorts` (public) and `items.routes` (the reader's, beside
  trips and pins under `items.principal`) for it.
- **Saved places goes beyond the mockup**, and shows only when the area has
  pins. Without it, a missing pin makes the row amber while every item reads
  green.
- **The chevron trails the row** rather than sitting by the name, as in the
  mockup. The name keeps its job of framing the area on the map (SNOW-811),
  and the trailing chevron is the system's one row disclosure.
- **The size lives in the inventory** (Hugo, 4 Nov 2026), split into Map and
  Terrain. The row's meta line is the kind and the basemap. SNOW-1487: both
  are measured from the area's bucket by URL class
  (`pwaBasemapDownloads.measureAreaInventory`), and no size shows until
  they are; the estimate split this replaced could read "0 MB" for a map.
  The Terrain line reads its own tiles, and an area in Switzerland still to
  gain the Swiss map lists "Swiss map: Not added yet"
  ([a-failed-addition-never-hides-a-working-map.md](a-failed-addition-never-hides-a-working-map.md)).

## Every download includes terrain data

Hugo, 4 Nov 2026: terrain data comes with every download wherever the
terrain filter is configured, and the sheet's "Terrain data" switch is gone.
`terrainDataPreferred` now answers `terrainDataAvailable`. An area saved
without terrain keeps its record's `terrain` flag absent, so its inventory
reads Terrain "Not saved" until it is downloaded again.

## A finished download is a reading

A download that completes stamps `verified` on its new record
(`downloadVerified` in `map_basemap_downloads.js`), with the same tile-grid
reading and content answer `syncArea` stamps. Before this, a new row read
"Not checked yet" until a Sync that fetched nothing turned it green.
