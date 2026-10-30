---
name: a-download-looks-forward-and-carries-the-region-sheet
description: Why an area download saves forward days only and carries the region sheet ('public-partial', path + d key, X-SW-Offline-Miss)
status: current
last-reviewed: 2026-10-30
---

# A download looks forward, and carries the region sheet

**SNOW-1312.** A downloaded region tapped offline said "Region details are
unavailable offline": the map's region sheet
(`/partials/region-sheet/<id>/?d=<day>`) was never saved, and the worker
classified it `'network'`, a branch that never reads a cache. The scrubber
also lost its colours offline on any day but the one the map last booted on.

## Decision

1. **A download looks forward only.** "You're looking forward out in the
   field, not back" (Hugo, 2026-10-30). The day window is today to the
   last published day (`latestKnownDate` over the season ratings,
   SNOW-927), plus a forward day on screen — never a past one. One pure
   helper, `pwaBasemapDownloadCore.forwardContentDays`, serves the map's
   download and `/offline/`'s Update, which loads `calendar_core.js` for
   the ceiling. SNOW-953's reach backwards is gone with its setting
   (`OFFLINE_CONTENT_PAST_DAYS`) and its `data-content-past-days`
   attribute. Field observations keep their own "recent for three days"
   window; they are an overlay feed, not dated content.
2. **The download carries the region sheet** for every region and forward
   day (`regionSheetUrls` in `areaContentURLs`, the id lowercased, `d`
   first), and the **dated ratings feed** the map boots its colours from
   (`ratingsUrls`, `/api/ratings/?d=<day>&country=ch`, map.js's own
   parameter order). `/offline/`'s Update also warms each country's season
   ratings, which the map's download already warms with its feeds. All of
   them are urls the run tallies, so a sheet or a feed that fails counts
   against the area like a failed bulletin page.
3. **A `'public-partial'` worker class** for the region sheet only:
   network-first, kept unstamped under **path + `d`**
   (`_publicPartialKey`), whatever `r` or other parameter the sheet sends.
   `d` is required; an undated sheet is never kept or answered offline. A
   non-OK answer is never kept. The warm sends `HX-Request: true` for this
   family alone, because `require_htmx` answers 400 without it.
4. **A miss says so.** Offline with no sheet for that key, the worker
   answers a 504 carrying `X-SW-Offline-Miss: 1`, and the sheet's
   peek-preserving failure path says "This day isn't downloaded for
   offline use" instead of "unavailable".
5. **`/offline/` lists the sheets** as their own row, "Region details for
   downloaded days", and treats a `/partials/` entry as a fragment rather
   than a page.

## Why path + `d`, and only for this sheet

`r` is the reports cache-buster SNOW-1295 stamps into the sheet's url after
the reader files or removes a report. The body it asks for — that region on
that day — does not change with it, so an exact-url key would lose every
downloaded sheet the first time the reader filed a report. The weather sheet
keeps its exact match: its body names its day only relatively ("Today"), so
the url has to carry the day exactly
([an-area-download-carries-your-trips-and-borrowed-forecasts.md](an-area-download-carries-your-trips-and-borrowed-forecasts.md)).
The region sheet's body names its day itself.

## Why unstamped

The body names nothing about the reader. One element depends on who asks:
"On the ground"'s call to report is a button signed in and a sign-in link
signed out (SNOW-1291). `tests/public/test_region_sheet.py`
(`TestOfflineCopy`) holds that to being the only difference, so a copy saved
under one account shows the other at worst that button. A body is served in
the language it was saved in.

The one other reader-dependent branch is the staff callout on a bulletin
that failed processing (version 0): the admin link, the bulletin id and the
processing error. `region_sheet_partial` answers that body `no-store`
(`_renders_staff_callout`), which the worker already refuses to keep, so it
can never be served to another reader on the same device.

## Consequences

- Nothing from before today is saved by a download from either entry
  point. A past day offline opens to the peek and the "not downloaded"
  line.
- Sheets live in the shell cache (`CACHE_VERSION`) with the bulletins, so
  an update carries them across with the rest (SNOW-1311,
  `an-update-carries-your-downloaded-content-across.md`).
- Every region sheet opened online with a `d` is kept too, under its key.
