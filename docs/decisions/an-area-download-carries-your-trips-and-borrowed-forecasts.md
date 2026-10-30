---
name: an-area-download-carries-your-trips-and-borrowed-forecasts
description: Why an area download carries your trips and borrowed forecasts (/api/area-items/, private-json route, public-json weather sheet)
status: current
last-reviewed: 2026-10-30
---

# An area download carries your trips and your pins' forecasts

**SNOW-965.** An area download carried the public content inside its box —
the region bulletins and the curated weather sheets
([inside-the-boundary-is-complete.md](inside-the-boundary-is-complete.md)) —
and missed three things someone heading out needs.

## Decision

1. **A private endpoint, `/api/area-items/`, names the reader's own things
   in a rectangle**: the trips they are on whose snapshot `bounds` touch
   the box, and their placed pins inside it, each with the curated point
   it borrows a forecast from (`apps/public/area_items.py`). Same
   edge-inclusive rectangle rule as `/api/area-content/`. Anonymous gets
   200 and two empty lists for no query. `private, no-store`,
   `Vary: Cookie`, no ETag. Three queries however many pins:
   `nearest_forecast` was split into `forecast_candidates` (one scan) and
   `nearest_candidate` (a pick) so a request reads the candidates once.
2. **The client composes the urls** (`areaItemURLs`): each trip's page and
   its `route.geojson`, and each borrowed weather sheet, de-duplicated
   against the area-content plan. The map's download and `/offline/`'s
   Update both ask for items alongside area-content. An items endpoint
   that was asked and failed makes the run short (`contentIncomplete`), so
   the area is never painted done over it. A page without
   `data-area-items-url` was not asked, which is not a shortfall.
3. **A trip's route is principal-stamped JSON.** The worker classifies
   `/trips/<uuid>/route.geojson` as `'private-json'`. It is fetched
   network-first, kept in the shell cache stamped with the signed-in
   principal (`_signedInPrincipal`, from `mutations.principal`), and
   served offline only when the stamp matches. No principal means no
   write and no read. A 403 or 404 online drops the kept copy. The warm
   stamps it the same way, and fails a url it cannot stamp, so the tally
   comes up short. The share-token route matches neither class and stays
   network-only.
4. **The uuid route answers `private, no-cache`**, not `no-store`, which
   is what lets the worker keep it. `private` still keeps it out of every
   shared cache. The share-token endpoint stays `no-store`.
5. **Weather sheets are read offline.** `/api/weather/<short_id>/detail/`
   was `'network'`, a branch that never reads a cache, so every sheet a
   download warmed since SNOW-924 was unservable. It is now
   `'public-json'`: network-first, kept unstamped, served offline on an
   **exact url match only**.
6. **Every warmed sheet is dated** (`?date=<today>`, `weatherDetailURL`),
   because the map's sheet always asks with the map's day and the worker
   matches exactly.

## Why exact match, and no query-less fallback

The plan allowed a fallback from `?date=D` to the undated entry if the
sheet's body names its own day. It does not. The sheet's day is named by
its dated eyebrow (`includes/_dated_eyebrow.html`), which on the day it was
rendered reads **"Today"** and prints no date. A sheet warmed yesterday and
served for today would claim to be today's forecast. So the fallback was not
built. Dating the warmed url with today makes the exact match land on the
day of the download. On a later day offline, the sheet does not open.
That is the honest answer for a forecast that has aged.

## Consequences

- The scope's weather criterion is **today's sheet opens offline**: the
  sheet for the day the area was downloaded or updated, not a later day.
- The stamp comes from `mutations.principal`, which the page reconciles at
  boot, not from the response (a JSON body names no principal). The map
  fetches a trip route inside `map.on('load')`, well after that reconcile
  has started, and a download runs later still on a press. The ordering is
  timing rather than an awaited handshake, so a mismatch fails closed: a
  stale stamp is never served to anyone else.
- Kept routes and sheets live in the shell cache (`CACHE_VERSION`), like
  the bulletins and pages a download warms. Each deploy reaps them with
  the rest.
- Every weather sheet opened online is now kept under its own url, until
  the next deploy reaps the shell cache.
