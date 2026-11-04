---
name: sync-fetches-only-what-changed
description: Sync now, Sync all and background sync fetch only the delta — contentDelta, dueAreas, CONTENT_FRESH_MS, offline_autosync_core.js, contentRun
status: current
last-reviewed: 2026-11-04
---

# A sync fetches only what changed, and runs in the background too

**Decision (SNOW-1416).** Every content sync on the map page — a row's
**Sync now**, the Downloads sheet's **Sync all** and the new **background
sync** — fetches only the part of an area's content plan that the device
does not already hold up to date. The rule is one pure function,
`contentDelta` in `static/js/offline_autosync_core.js`
(`window.pwaOfflineAutosyncCore`):

- a url missing from the area's pinned bucket is always fetched;
- a held **permanent** url (a trip's page and its `route.geojson`) is never
  refetched;
- a held **perishable** url (bulletins, region sheets, ratings, weather) is
  refetched only when the area is **stale**.

An area is stale when its record has no `contentAt`, a `contentAt` older
than `CONTENT_FRESH_MS` (one hour), or `contentIncomplete` set. The
perishable urls carry their day, so a new day's bulletins are new urls and
are fetched whatever the age.

**Background sync.** While the map page is open and online (the effective
check, `pwaConnectivity.isOnline()`, so Offline mode and a latched dead
radio both stop it) and Data Saver is off, the page refreshes the content
of every stale downloaded area: once a few seconds after load, then on each
`online` event and each return to the foreground. `dueAreas` picks the
areas; an area attempted within the same hour is skipped, success or
failure, so a failing area is not retried on every trigger. Attempts are
kept in IndexedDB under `autosync.attempts`. Background runs fetch content
only, never tiles, and the row it is on shows it syncing.

**One content run at a time.** `pwaBasemapDownloads.contentRun` holds who
is running content: `'user'` (Sync now, Sync all) or `'auto'`. A user press
asks the background run to stop after the area it is on, waits for it, then
runs. A background run never starts while a user run or a pinned download
is going.

## Why

Hugo, 4 Nov 2026: "sync is all data, with the expectation that most of it
already exists — i.e. don't re-download, just fetch the delta", and
"background sync yes". The rethink made Downloads the home for offline
([downloads-is-the-home-for-offline.md](downloads-is-the-home-for-offline.md)),
where Sync all runs over every area. Refetching every bulletin, sheet and
trip of every area per press, as
[a-pre-departure-sync-is-unconditional.md](a-pre-departure-sync-is-unconditional.md)
required, made the press slow on exactly the bar of signal it is for, and
made a background sync unaffordable.

**Why the unconditional rule could go.** It existed because a freshness
reading is an inference from a stamp. Two things change that inference.
The day is in every perishable url, so "today's bulletins are held" is a
fact read from the bucket, not inferred from `contentAt`. And the hour
window bounds the inference that remains: a held bulletin for today is at
most an hour older than the server's, and a background sync keeps topping
that up while the page is open.

**Why the filter is page-side.** The service worker's warm refetches every
url it is given; it has no notion of "held". `_contentDeltaFor` in
`static/js/map_basemap_downloads.js` opens the pinned bucket, reads the
held urls, and hands the warm only the delta. Without the Cache API or the
core it falls back to the whole plan, which is the old behaviour.

**Why `contentAt` does not always move.** A run on a fresh area that only
filled gaps keeps the area's earlier stamp (`keepsContentAt`). Stamping it
would claim the held bulletins were refetched when they were not, and the
window would drift forward without the data being any newer.

**Why one hour.** Hugo's call on the plan. SLF updates its bulletin twice a
day; an hour keeps a held bulletin close to current without fetching an
area's content on every page load. It is one constant to tune.

## Consequences

- A sync on an area that holds everything and is fresh fetches nothing and
  settles at once.
- The tile half of `syncArea` is unchanged: it was already conditional.
- `/offline/` keeps its own sequence and is unaffected.
- `data-background-sync="on"` on the map embed's `#map` opts a page in;
  pages without it, and the Vitest harness, never start one.
