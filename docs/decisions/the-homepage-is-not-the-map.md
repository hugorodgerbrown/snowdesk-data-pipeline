---
name: the-homepage-is-not-the-map
description: / is a static homepage (public:home); the map is /map/ (public:map, map_page); /?<map state> 301s there; start_url /map/, id /
status: current
last-reviewed: 2026-09-29
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
- The manifest's `start_url` is `/map/`; its `id` and `scope` stay `/`.
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
  `_ATTRIBUTION_PARAMS` in `apps/public/views.py`, or campaign links to `/`
  land on the map.
- A cached `/` shell from before the move is the old map page; the next
  activation's `_rewarmShell` warms `/map/` instead, and the old entry is
  dropped with its cache generation.
- The homepage image (`static/img/home/map.jpg`) is a still and goes stale
  as the map changes. Re-capture it when the map's appearance changes.
