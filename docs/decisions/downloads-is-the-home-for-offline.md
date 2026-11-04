---
name: downloads-is-the-home-for-offline
description: Downloads sheet is the home for offline — row verdict dot, Sync all, pwaDownloadsSyncAll, network menu switch only, /offline/ unlinked
status: current
last-reviewed: 2026-11-04
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
  date", "Some trips or pins missing", "Map data missing"), grey for "Not
  checked yet". An account-only row and an orphaned bucket have no dot;
- each row's "…" menu keeps its own Sync now, first, with Remove last;
- the sheet's foot carries one **Sync all**, which drains the mutation queue
  and syncs every area on the device in the sheet's order
  (`offline_ready_core.run` over `pwaBasemapDownloads.syncArea`). It is
  refused offline, shows its progress as its label, marks the row it is on
  as syncing, and toasts once if any area fell short.

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
- A sync is still unconditional
  ([a-pre-departure-sync-is-unconditional.md](a-pre-departure-sync-is-unconditional.md))
  until SNOW-1416 makes it fetch only what changed.
- A new surface that verifies an area should still stamp `verified`, or the
  sheet keeps the last answer another surface gave.
- The inventory of what a download holds is a reveal on its row (SNOW-1418),
  not a page.
- A link to /offline/ anywhere a user can see it is a regression;
  `tests/public/test_offline_page.py` checks Help, settings, the map and the
  offline shell.
