---
name: a-pre-departure-sync-is-unconditional
description: syncArea, Sync now, Get ready for offline, ?sync=all, offline_ready_core.js — refetch regardless of freshness, every area per press
status: current
last-reviewed: 2026-10-20
---

# A pre-departure sync is unconditional, and the network menu runs it for every area

**Decision.** Every downloaded area carries one "sync now" action — an item
in the map page's Manage downloads sheet — and the network menu carries one
**Get ready for offline** press that runs it for every area in turn
(SNOW-1141; until then the menu had a button per area). It goes through
`window.pwaBasemapDownloads.syncArea(areaId)`
(`static/js/map_basemap_downloads.js`), which repairs the area's missing
render dependencies if it has any and then refetches its content — the
bulletins, weather and feeds inside its boundary — **regardless of what the
record says about their age**, and regardless of whether the repair
succeeded.

The menu's press drains the mutation queue first, so queued edits go
before any area, and then syncs each downloaded area one at a time
(`static/js/offline_ready_core.js`). Off the map page it does not run the
sync: it navigates to `/offline/` with `?sync=all` appended to the href the
panel's downloads link already carries, and that page runs the same
sequence through the same core and re-runs its report over the result.
`?sync=<area id>` still names one area, for any caller that wants that.

## Why

SNOW-923 split a download into permanent tiles and perishable content.
SNOW-925 gave the tiles a repair, SNOW-932 gave the content a refresh,
SNOW-928 gave the network menu a staleness line. By SNOW-951 there were
three ways to top an area up and no way to make one current:

| Surface | Control | Gate | Mends |
|---|---|---|---|
| Manage downloads sheet | Repair | `incomplete` — tiles already failed | tiles |
| Manage downloads sheet | Refresh | `contentIncomplete` — a run already fell short | content |
| `/offline/` | Update | not `fresh` | content |
| Network menu | — | — | nothing |

Every one of them was a **remedy**, so none was on the row at the moment
anybody actually wants one: standing in the car park with a bar of signal
about to go, when nothing is wrong yet and everything is about to be
needed. The user's question there is not "what is broken?" but "am I
carrying what I will need?", and until this ticket the app could not answer
it in one press.

**Unconditional, because the guarantee is the point.** Withholding the
content fetch from an area the app believes is fresh saves kilobytes of
HTML and costs the user the only thing they came for. Every freshness
reading is an inference from a `contentAt` stamp; the thing they are about
to rely on is the data itself. A press that silently does nothing because
the app already believes it holds today's bulletins is exactly the failure
the press was made to rule out. This reverses `completableArea`'s SNOW-925
exclusion of `fresh` (`static/js/offline_audit_core.js`), which was written
for a passive report — where an action with nothing to do is noise — and
not for a user asking.

The **tile** half stays conditional, and the asymmetry is not an
inconsistency: tiles are megabytes, they are a fixed grid over fixed ground
and they never go stale, so an area holding all of them has nothing to
fetch. An empty missing-list is the healthy case, not a skipped step.

**Tiles first, then content whatever happened.** They are independent
remedies for independent failures. A repair that fails leaves an area whose
map will not draw; refusing to fetch its bulletins on that account would
cost the user both halves for the sake of a tidy report.

**One press in the menu, the rows as detail (SNOW-1141).** SNOW-951 put a
button per area in the network menu and said "no sync all": a download is
discrete, two areas are two boundaries and two runs, and one button
standing for an unbounded amount of fetching tells the user nothing about
what they will hold when the signal goes. SNOW-1141 overturned that. The
premise holds; the conclusion did not survive the moment it was for —
someone in a car park wants one press, not a row of them to press and read
in turn, and nothing sent the edits waiting in the queue at all. The
discreteness the old rule defended is kept where it is useful: each area's
row answers for that area — its bulletin stamp before the press, then up
to date, parts missing or tiles gone — and the press ends on one summary
line ("Ready — 5 areas up to date" / "3 of 5 ready"), so the user still
learns what they are holding. The result belongs to the press and clears
the next time the menu opens; SNOW-951's session-long "Synced" did not.
(SNOW-951 itself had kept SNOW-928's premise and overturned its conclusion,
a menu row that "STARTS NOTHING".)

**The queue first.** A queued edit is the only thing on the device the
server does not already have; bulletins can be fetched again, a pin
dropped offline cannot. The drain is bounded, because a dead radio hangs
rather than rejects ([bounded-offline-read-paths.md](bounded-offline-read-paths.md)),
and a hung POST must not cost the areas behind it their turn.

**Navigating off the map rather than loading the map's code.**
`basemap_download_core.js` is 140KB and loads only on the map and
`/offline/`; there is no lazy-script precedent anywhere in the tree; and
the two content-fetch implementations genuinely differ (the day range they
take, which feeds they warm). Pulling either into a bulletin page would
have been a third copy of the content plan and its stamp rule. The page
load is spent at the one moment the network is there by definition.

## Consequences

- A region row in the Manage downloads sheet is **always** a menu now: Sync
  now joins Remove, and two actions are a "…" (design-system rule 5). The
  bare trash survives only for an orphan.
- `/offline/` offers **Update on every row whose tiles verify**, including a
  fresh one. The tiles exclusion stays — that page has no loaded basemap
  style and cannot honestly offer to mend them — so an area that does not
  draw still gets the report's named remedy instead of a control.
- The network menu's press is a `<button>`, not an `<a>`: on the map page it
  is not a navigation at all.
- A press on the map page leaves the menu **open**. The result is the
  answer to the question the press asked.
- A tiles-gone (`'absent'`) area is not offered a retry it cannot win: its
  row reads "Tiles gone · Download again" and links to
  `/map/?panel=downloads`, which opens the Manage downloads sheet (in place,
  when the menu is already on the map).
- On `/offline/?sync=all` an area whose tiles do not verify is counted as
  not ready rather than fetched — that page cannot mend tiles — and its row
  keeps the remedy the report names.
- `?sync=` (an id, or `all`) is read client-side and removed with `history.replaceState`, so
  a reload does not re-run a fetch the user asked for once. No server-side
  change: the parameter never reaches a view.
- Both id forms are accepted by `/offline/` (a bucket id, or a bare region
  id), and resolving between them happens there, where `areaIdForRegion`
  is. No surface assembles the bucket-id format by hand.
- `syncArea` composes the two paths that already existed —
  `repairPinnedDownload` and `refreshAreaContent` — rather than fetching
  anything itself, so each half still has exactly one implementation.
  SNOW-844's `repair` member on `window.pwaBasemapDownloads` and
  `includes/_icon_repair.html` existed for the sheet's Repair control
  alone and went with it; the private `repairPinnedDownload` is untouched
  and is still the tile path.
- **A sync is not a re-download, and never reports as one.** Both halves
  write documents into an area's existing pinned bucket; neither fetches a
  tile grid. So `syncArea` reports the tile half as one of five answers,
  and only two of them are a claim that the tiles are established:
  `'ok'` (mended what was missing) and `'none'` (nothing was missing).
  `'failed'` is a repair that did not land; `'unknown'` is a dependency
  list that could not be resolved at all — a record written before
  SNOW-844 stored its `deps`, on a basemap that is not the loaded one;
  `'absent'` is a record whose pinned bucket this device no longer holds,
  which is refused outright rather than half-mended into something that
  satisfies the render check and still draws nothing. `'absent'` is the
  one reconciliation the network menu's list cannot do for itself: it is
  built in `pwa_offline.js`, which runs on every page and deliberately
  does not load the download core it would need to name a bucket, so the
  check lives in `syncArea` where both halves are visible and covers
  every caller. The remedy for such a record is the re-download the
  Manage downloads sheet already offers, which the network menu's row now
  links to.
- **Warm-cache calls queue.** `window.pwaWarmCache` tracks its run in one
  slot and `sw.js` warms one list at a time, so two presses before the
  first settles used to have the second overwrite the first's
  `requestId` — the first then sat out its full silence timeout and
  reported a failure while its documents were landing. `sw_register.js`
  now serialises every call through one chain. Two rows may both read
  "running"; they take the worker in turn.
