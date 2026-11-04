---
name: the-connection-panel-answers-per-area
description: Connection panel, one row per area, verdictFor, stagesFromRecord, verified stamp, Sync, /map/?sync=all, header readiness mark
status: historical
last-reviewed: 2026-11-04
---

# The connection panel answers "will this area work offline?" per area

SNOW-1415.

> **Superseded by SNOW-1420**
> ([the Downloads sheet is the one home for offline](downloads-is-the-home-for-offline.md)).
> The per-area answer and Sync moved to the map's Downloads sheet, the
> readiness mark and `/map/?sync=all` are gone, and the Offline mode switch
> is back in the network menu. The `verified` stamp below still holds.

**Decision.** The header's connection panel (`includes/_connection_panel.html`)
gives one answer per downloaded area and one Sync button, and nothing that
needs knowledge of the app's internals to read. Each row shows the verdict
/offline/'s panels already give (`offline_journey_core.js`'s `verdictFor`):
yes, mostly with its first reason, map missing, or not checked yet. The
header symbol carries the device's overall answer as a small mark (ready,
syncing, needs attention), and none with no areas or no signal.

The panel runs on every page, where neither the audit's readings nor the tile
arithmetic exist. So the pages that can read tiles write what they verified on
the area record, and every other page reads it back:

- the map writes `verified` after each `syncArea` (`verifiedFromSync`). A map
  Yes needs the whole tile grid in the bucket (`_areaTilesHeld`, the same
  `tileReading` /offline/ takes), not only the render dependencies;
- /offline/ writes it from each panel's map and things stages
  (`verifiedFromStages`);
- a things answer is stamped with the account it was read for
  (`verified.principal`) and read back only to that account;
- `stagesFromRecord(record, now, {signedIn, principal})` turns the record back
  into stages, and `verdictFor` is unchanged. A record no tile-capable page
  has stamped reads map unknown, and the panel shows "Not checked yet" for it
  whatever its bulletins' age, so its verdict is never a false yes or
  "mostly".

`offline_journey_core.js` loads globally from `base.html` for this. It stays
pure and never reads the audit core on the record-only path.

Sync drains the queue and syncs every area (`offline_ready_core.run`). Off the
map it navigates to `/map/?sync=all`: only the map can mend tiles, so only the
map can answer for them. The map's downloads module runs the sync once,
through `window.pwaConnectionPanel.syncAll`, with the panel open, and strips
the parameter.

The Offline mode switch and the Debug log left the panel for /offline/, which
is now the debug page, reached from Help and settings.

## Why

Hugo, 4 Nov 2026: the panel exists to give confidence that the map works
offline inside the areas you chose. Before this it showed two clocks, a
switch, "Get ready for offline", a job counter, a mislabelled "Download for
offline" link and a Debug log: true, and legible only to someone who knows
how the app works inside. A second reader of the stored records would have
let the panel and /offline/ disagree about one area, so the stamp feeds the
one verdict instead.

## Consequences

- A new tile-capable surface that verifies an area should stamp `verified`,
  or the panel keeps the last answer another surface gave.
- `verified` is device-only: `downloads_sync.js` sends a whitelist of fields.
- A row is not interactive yet; the tap is reserved for the inventory
  (SNOW-1418).
- `/offline/?sync=all` still works for the debug page's own use, but nothing
  in the panel links to it.
