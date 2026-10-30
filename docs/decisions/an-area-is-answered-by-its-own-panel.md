---
name: an-area-is-answered-by-its-own-panel
description: Why each downloaded area has its own /offline/ panel — offline_journey_core.js, stages, verdicts, stampContentRecord, pwaBasemapAreaRemoval
status: current
last-reviewed: 2026-10-30
---

# An area is answered by its own panel

**SNOW-1308.** The offline report (`offline_audit.js`) listed each
download as one row under "Map downloads", answered Yes or No about its
tiles, with content age and the remedy folded into the summary. A reader
about to lose signal asks something narrower — *will this area work?* —
and the row answered a third of it.

## Decision

Each downloaded area gets a **panel** on `/offline/` and on
`static/offline.html`, above the report, and the report's "Map
downloads" section is gone (Hugo's call). A panel answers three stages
and leads with one verdict:

| Stage | Yes when |
|---|---|
| The map | `areaState` is `ready` **and** every basemap tile the record's ground implies is in its bucket (`blobFullyCached`, over the stored tile template) |
| Bulletins and weather | `areaContentState` is `fresh` |
| Your things | each trip and pin the last sync expected is in Cache Storage, by name — omitted for a signed-out reader |

| Verdict | When |
|---|---|
| Ready for offline | every stage is Yes |
| Needs a refresh — open the map | the map stage is No (links to `/map/?panel=downloads`) |
| Needs a refresh — tap to fix | another stage is No; the tap runs Sync now (or "Get ready for offline" on `?sync=all`) |
| Can't tell | a stage could not be read and none is No |

The panels are a **reading layer**: they take no storage walk of their
own, and paint from the collection the audit already takes
(`snowdesk:offline-readings`, `pwaOfflineAudit.latest()`), asking it for
one read on load.

## Why

**The expectation is stamped, because the page cannot ask.**
`/api/area-items/` is `no-store` and per-user, so `/offline/` — often
opened without a signal — has no way to learn which trips and pins an
area holds. Every successful sync therefore stamps what it expected onto
the area record (`items`, from `areaItemExpectation`), through one rule
(`stampContentRecord`) shared by the map's region roundel, its Manage
downloads sheet, the first download and `/offline/`'s Sync now. The
expectation is composed by the same function `areaItemURLs` fetches from,
so the panel cannot expect a url the sync never fetched. No stamp reads
"Can't tell" — a guess in either direction is the false reading the
report exists to stop.

**A tile gap sends the reader to the map.** Tiles need the loaded
basemap style, which exists only on the map; `/offline/` cannot fetch
them, so it does not pretend to.

**One removal.** Remove on a panel and Remove on the Manage downloads
sheet are the same verb, so they are the same code:
`evictBasemapAreas` moved into `basemap_area_removal.js`
(`pwaBasemapAreaRemoval.evict`), which both pages load. The map keeps its
own follow-ups (the overlay, the roundel's event); the panel re-reads
storage through the audit afterwards rather than trusting the attempt.

## Consequences

- The report says nothing per area. Its verdict still reads the areas for
  "there is no map" and "none of your downloads will draw".
- `static/offline.html` shows the panels with no Sync (no endpoints) and
  no Remove (no IndexedDB layer or download core there); its "tap to fix"
  links to `/offline/?sync=<id>`, and its map stage reads "Can't tell"
  because the tile reading needs the download core.
- An area synced by another account never names its things
  (`principal` on the record, SNOW-1142).
- Saved routes are not in "your things" yet — a later ticket.
