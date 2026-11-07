---
name: the-homepage-is-not-the-map
description: / is a static homepage (public:home); the map is /map/ (public:map, map_page); /?<map state> 301s there; start_url /map/, id /
status: current
last-reviewed: 2026-11-07
---

# The homepage is not the map

## Decision

`/` is a static homepage: a still image of the map, a one-line pitch, short
sections on what a reader gets, and a link into the app. The map lives at
`/map/`. This reverses SNOW-344, which made `/map/` a redirect to `/`.

- `public:home` → `/` (`views.home`, `public/home.html`). No MapLibre, no
  map JavaScript.
- `public:map` → `/map/` (`views.map_page`, `public/map.html`). A link that
  means "the map" reverses `public:map`.
- A request to `/` whose query string holds any parameter other than
  attribution (`utm_*`, `ref`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`) 301s
  to `/map/` with the query string unchanged. A bare `/` renders the homepage.
- A fragment (`/#CH-4115`, the old bulletin back-link) never reaches the
  server, so a nonce'd script in the homepage head replaces the location
  with `/map/` plus the search and fragment.
- The manifest's `start_url` is `/map/`; its `id` and `scope` stay `/`. An
  app installed before the move keeps launching `/` until the browser
  refreshes the installed metadata (an iOS home-screen icon may never), so
  the homepage forwards to `/map/` when it runs standalone, and the worker
  answers an offline navigation to `/` with a 302 to `/map/`. The browser
  carries a request's fragment across that redirect, so an offline
  `/#CH-4115` still opens the region.
- The service worker's `SHELL_PAGE` is `/map/`. The homepage is not warmed.

## Why

Before launch, a first visit needs to say what Snowdesk is before it asks
the visitor to operate a map. A full-frame map is a poor first page for
someone who arrived from a search or a shared link without context, and it
is the heaviest page on the site.

The redirect rule is written as "anything but attribution" rather than a
list of the map's parameters because every map link minted while the map
lived at `/` — `?d=`, `?panel=`, `?resort=`, `?route_share=`, `?trip=`,
`?trip_share=`, `?edit=`, `?layers=`, `?intro=` — has to keep working, and a
list would drift the next time the map reads a new one.

The manifest `id` is the installed app's identity. Changing it would make
every existing install a different app, so only `start_url` moves.

## Consequences

- A new attribution parameter a campaign uses must be added to
  `_ATTRIBUTION_PARAMS` in `apps/public/views.py` **and** `ATTRIBUTION_PARAMS`
  in `static/js/sw.js`, or campaign links to `/` land on the map.
  `tests/public/test_homepage.py` fails when the two lists differ.
- The service worker answers the `/?<map state>` 301 itself (`_legacyRootMapUrl`),
  so a legacy link opened offline reaches the cached `/map/` shell rather
  than the offline fallback.
- A cached `/` shell from before the move is the old map page; the next
  activation's `_rewarmShell` warms `/map/` instead, and the old entry is
  dropped with its cache generation.
- The homepage images (`static/img/home/`, a 4:3 desktop and a 3:4 phone
  capture for each of its four feature cards, and a 2:1 desktop and a 3:4
  phone capture for the 3D card, SNOW-1454) are stills and go stale as the
  app changes. Re-capture them at the same ratios when the app's appearance
  changes. The 3D card's pair is taken at `HOME_3D_CAMERA`
  (`apps/public/views.py`), the camera its link opens, in Conditions with
  no Terrain layers, because that is what most readers get. The phone
  capture is pulled back to zoom 11.3 so a narrow frame still holds the
  Allalin, the Matterhorn and Zermatt. The danger wash comes from a real
  Moderate day (15 May 2026); headless Chromium can draw it, against the
  dev server with the ratings API pointed at production.
