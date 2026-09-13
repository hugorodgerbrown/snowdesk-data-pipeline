---
name: a-pre-departure-sync-is-unconditional
description: syncArea, Sync now, ?sync=, completableArea — one press per area refetches whatever the record says about freshness
status: current
last-reviewed: 2026-09-13
---

# A pre-departure sync is unconditional, and it is per area

**Decision.** Every downloaded area carries one "sync now" action, on two
surfaces: an item in the map page's Manage downloads sheet and a button per
area in the network menu. It goes through
`window.pwaBasemapDownloads.syncArea(areaId)`
(`static/js/map_basemap_downloads.js`), which repairs the area's missing
render dependencies if it has any and then refetches its content — the
bulletins, weather and feeds inside its boundary — **regardless of what the
record says about their age**, and regardless of whether the repair
succeeded.

There is no "sync all". A press names an area.

Off the map page the button does not run the sync: it navigates to
`/offline/` with `?sync=<area id>` appended to the href the panel's
downloads link already carries, and that page runs the area's own Update
control and re-runs its report over the result.

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

**Per area, with no "sync all".** A download is discrete: two areas are two
boundaries, two plans and two runs. One button standing for an unbounded
amount of fetching over a connection about to disappear tells the user
nothing about what they will be holding when it stops. They press the ones
they are going to be standing in. This keeps SNOW-928's premise and
overturns its conclusion — that comment reasoned from discreteness to a
menu row that "STARTS NOTHING", when discreteness is an argument for a row
*per area*.

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
- The network menu's per-area button is a `<button>`, not an `<a>`: on the
  map page it is not a navigation at all.
- A sync started from the network menu on the map page leaves the menu
  **open**. The result is the answer to the question the press asked.
- `?sync=` is read client-side and removed with `history.replaceState`, so
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
